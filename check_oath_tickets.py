"""
check_oath_tickets.py
------------------------

This script queries the NYC DSNY Sanitation OATH database for
violations associated with a specific address (1407 Overing Street by
default) and sends an email whenever new tickets are detected.  It is
designed to be run periodically by a GitHub Actions workflow.  On the
first run, it will create a ``known_tickets.json`` file in the
repository directory containing the ticket numbers that were present
when the script executed.  Subsequent runs compare the set of
previously‑seen ticket numbers against the latest records and only
send an email when new tickets appear.

Configuration
~~~~~~~~~~~~~

The script reads several configuration values from environment
variables.  These variables should be defined as GitHub secrets when
the workflow is set up:

* ``SMTP_SERVER`` – hostname of the SMTP server (e.g. ``smtp.gmail.com``)
* ``SMTP_PORT`` – port number for the SMTP server (typically ``587``)
* ``SMTP_USERNAME`` – username for authenticating to the SMTP server
* ``SMTP_PASSWORD`` – password or app‑specific password for the SMTP account
* ``FROM_EMAIL`` – email address used in the ``From`` field of outbound messages
* ``TO_EMAIL`` – email address that should receive notification messages

The repository should include this script (e.g. in a ``scripts``
directory) and a GitHub Actions workflow can call it on a schedule.

Note
~~~~

This script uses the public Socrata API provided by NYC Open Data.  The
API occasionally enforces rate limits, so high‑frequency polling is not
recommended.  Daily checks are usually sufficient for tracking new
cases.
"""

import json
import os
import smtplib
from email.mime.text import MIMEText
from pathlib import Path
from typing import Iterable, List, Dict, Set

import requests

# Address details
#
# The script supports monitoring a single address or multiple addresses.  If
# ``TICKET_ADDRESSES`` is defined in the environment, it should contain
# one or more street addresses separated by semicolons or newlines.  Each
# address string must include a house number followed by the street name
# (e.g. ``"1407 Overing Street"``).  The street portion should match
# exactly how it appears in the DSNY dataset (usually all uppercase
# with a trailing descriptor like ``STREET`` or ``AVENUE``).  When
# ``TICKET_ADDRESSES`` is not set, the script falls back to the
# single ``OATH_ADDRESS_HOUSE``/``OATH_ADDRESS_STREET`` variables.
ADDRESS_HOUSE = os.environ.get("OATH_ADDRESS_HOUSE", "1407")
ADDRESS_STREET = os.environ.get("OATH_ADDRESS_STREET", "OVERING STREET")

def parse_addresses() -> List[Dict[str, str]]:
    """Parse the configured addresses from the environment.

    Returns a list of dictionaries with ``house`` and ``street`` keys.
    When ``TICKET_ADDRESSES`` is defined, it is split on semicolons
    and newlines to produce individual address strings.  Each address
    string is split on whitespace; the first token is taken as the
    house number and the remainder (joined back together) is treated as
    the street name.  If no multi‑address string is configured, a
    single entry containing ``ADDRESS_HOUSE`` and ``ADDRESS_STREET`` is
    returned.
    """
    multi = os.environ.get("TICKET_ADDRESSES")
    addresses: List[Dict[str, str]] = []
    if multi:
        # split by semicolon or newline, and filter out empty strings
        for raw in [part.strip() for part in multi.replace("\n", ";").split(";")]:
            if not raw:
                continue
            parts = raw.split()
            if len(parts) < 2:
                # Skip invalid entries
                continue
            house = parts[0]
            street = " ".join(parts[1:]).upper()
            addresses.append({"house": house, "street": street})
    if not addresses:
        # fall back to single address from OATH_ADDRESS_HOUSE/STREET
        addresses.append({"house": ADDRESS_HOUSE, "street": ADDRESS_STREET})
    return addresses

# Base URL for the DSNY Sanitation OATH database.  See
# https://data.cityofnewyork.us for documentation.
DATASET_URL = "https://data.cityofnewyork.us/resource/r78k-82m3.json"


def fetch_tickets(house: str, street: str) -> List[Dict[str, str]]:
    """Fetch all DSNY OATH tickets for a given address.

    Parameters
    ----------
    house : str
        The house number of the property.
    street : str
        The street name as it appears in the DSNY dataset.

    Returns
    -------
    List[Dict[str, str]]
        A list of ticket records sorted by violation date descending.

    Raises
    ------
    requests.HTTPError
        If the network request fails or returns a non‑200 status code.
    """
    query = (
        f"violation_location_house='{house}' AND "
        f"violation_location_street_name='{street}'"
    )
    params = {
        "$where": query,
        "$order": "violation_date DESC"
    }
    response = requests.get(DATASET_URL, params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def load_known_tickets(path: Path) -> Set[str]:
    """Load the set of known ticket numbers from a JSON file.

    Parameters
    ----------
    path : Path
        Path to a JSON file containing a list of ticket numbers.

    Returns
    -------
    Set[str]
        A set of ticket numbers.  If the file does not exist, an
        empty set is returned.
    """
    if not path.exists():
        return set()
    with path.open("r", encoding="utf‑8") as f:
        try:
            data = json.load(f)
            # ensure unique values
            return set(data)
        except json.JSONDecodeError:
            return set()


def save_known_tickets(path: Path, tickets: Iterable[str]) -> None:
    """Write the list of ticket numbers back to disk.

    Parameters
    ----------
    path : Path
        Path where the list should be stored.
    tickets : Iterable[str]
        Collection of ticket numbers to persist.
    """
    with path.open("w", encoding="utf‑8") as f:
        json.dump(sorted(tickets), f, indent=2)


def send_email(new_tickets: List[Dict[str, str]]) -> None:
    """Send an email notifying about new tickets.

    Parameters
    ----------
    new_tickets : List[Dict[str, str]]
        List of ticket records that have not been seen before.

    The SMTP configuration and recipient details are read from
    environment variables as described in the module docstring.  If any
    required variable is missing, this function prints an error and
    returns without sending mail.
    """
    smtp_server = os.environ.get("SMTP_SERVER")
    smtp_port = os.environ.get("SMTP_PORT")
    smtp_username = os.environ.get("SMTP_USERNAME")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    from_email = os.environ.get("FROM_EMAIL")
    to_email = os.environ.get("TO_EMAIL")

    missing = [
        name
        for name, value in [
            ("SMTP_SERVER", smtp_server),
            ("SMTP_PORT", smtp_port),
            ("SMTP_USERNAME", smtp_username),
            ("SMTP_PASSWORD", smtp_password),
            ("FROM_EMAIL", from_email),
            ("TO_EMAIL", to_email),
        ]
        if not value
    ]
    if missing:
        print(
            f"Cannot send email: missing environment variables: {', '.join(missing)}"
        )
        return

    # Compose subject and body.  If multiple addresses are being
    # monitored, omit the address from the subject and include it in
    # each bullet point.
    # Determine if multiple addresses are configured by checking
    # whether there are distinct address annotations on the new
    # tickets.
    addresses = {ticket.get("_address") for ticket in new_tickets if ticket.get("_address")}
    if len(addresses) == 1:
        addr_str = next(iter(addresses))
        subject = f"New DSNY OATH tickets for {addr_str}"
        lines = [f"The following new DSNY OATH tickets have been issued for {addr_str}:", ""]
    else:
        subject = "New DSNY OATH tickets detected"
        lines = ["The following new DSNY OATH tickets have been issued:", ""]
    for ticket in new_tickets:
        violation_date = ticket.get("violation_date", "Unknown Date")
        description = ticket.get("charge_1_code_description", "")
        status = ticket.get("hearing_status", "")
        ticket_number = ticket.get("ticket_number", "")
        addr = ticket.get("_address", f"{ADDRESS_HOUSE} {ADDRESS_STREET}")
        lines.append(
            f"• {addr}: Ticket {ticket_number} on {violation_date[:10]} – {description} (Status: {status})"
        )
    body = "\n".join(lines)

    message = MIMEText(body)
    message["Subject"] = subject
    message["From"] = from_email
    message["To"] = to_email

    # Attempt to send the email via SMTP
    try:
        port = int(smtp_port)
        with smtplib.SMTP(smtp_server, port) as server:
            # use TLS if supported
            server.starttls()
            server.login(smtp_username, smtp_password)
            server.sendmail(from_email, [to_email], message.as_string())
        print(f"Notification email sent to {to_email} with {len(new_tickets)} new tickets.")
    except Exception as exc:
        print(f"Failed to send email: {exc}")


def main() -> None:
    """Main entry point for the script.

    This function orchestrates fetching current tickets, determining
    which tickets are new, optionally sending an email, and persisting
    the updated set of known ticket numbers.
    """
    repo_root = Path(__file__).resolve().parent
    known_file = repo_root / "known_tickets.json"

    # Load previously known ticket numbers
    known_tickets = load_known_tickets(known_file)

    # Collect current records across all configured addresses
    current_records: List[Dict[str, str]] = []
    addresses = parse_addresses()
    for addr in addresses:
        house = addr["house"]
        street = addr["street"]
        try:
            records = fetch_tickets(house, street)
        except Exception as e:
            print(f"Error fetching tickets for {house} {street}: {e}")
            continue
        # annotate each record with its address for later use
        for rec in records:
            rec["_address"] = f"{house} {street}"
        current_records.extend(records)

    # Compute set of ticket numbers from all addresses
    current_ticket_numbers: Set[str] = {
        record.get("ticket_number")
        for record in current_records
        if record.get("ticket_number")
    }
    new_ticket_numbers = current_ticket_numbers - known_tickets

    if new_ticket_numbers:
        # Build list of new ticket records preserving order
        new_records = [
            record
            for record in current_records
            if record.get("ticket_number") in new_ticket_numbers
        ]
        send_email(new_records)
        # Update stored set
        save_known_tickets(known_file, current_ticket_numbers)
    else:
        print("No new tickets found.")


if __name__ == "__main__":
    main()