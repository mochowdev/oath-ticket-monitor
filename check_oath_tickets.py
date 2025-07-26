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

# Address details; adjust these constants if you need to monitor a
# different property.  Keep the street name exactly as it appears in
# the DSNY dataset (all uppercase with a trailing "STREET").
ADDRESS_HOUSE = os.environ.get("OATH_ADDRESS_HOUSE", "1407")
ADDRESS_STREET = os.environ.get("OATH_ADDRESS_STREET", "OVERING STREET")

# Base URL for the DSNY Sanitation OATH database.  See
# https://data.cityofnewyork.us for documentation.
DATASET_URL = "https://data.cityofnewyork.us/resource/r78k-82m3.json"


def fetch_tickets() -> List[Dict[str, str]]:
    """Fetch all tickets for the configured address.

    Returns a list of dictionaries, each representing a single record
    from the DSNY dataset.  The records are sorted by violation date
    descending so that the newest ticket appears first.

    Raises
    ------
    requests.HTTPError
        If the network request fails or returns a non‑200 status code.
    """
    query = (
        f"violation_location_house='{ADDRESS_HOUSE}' AND "
        f"violation_location_street_name='{ADDRESS_STREET}'"
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

    subject = f"New DSNY OATH tickets for {ADDRESS_HOUSE} {ADDRESS_STREET}"
    lines = [
        f"The following new DSNY OATH tickets have been issued for {ADDRESS_HOUSE} {ADDRESS_STREET}:",
        ""
    ]
    for ticket in new_tickets:
        violation_date = ticket.get("violation_date", "Unknown Date")
        description = ticket.get("charge_1_code_description", "")
        status = ticket.get("hearing_status", "")
        ticket_number = ticket.get("ticket_number", "")
        lines.append(
            f"• Ticket {ticket_number} on {violation_date[:10]}: {description} (Status: {status})"
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

    try:
        current_records = fetch_tickets()
    except Exception as e:
        print(f"Error fetching tickets: {e}")
        return

    current_ticket_numbers: Set[str] = {
        record.get("ticket_number") for record in current_records if record.get("ticket_number")
    }
    new_ticket_numbers = current_ticket_numbers - known_tickets

    if new_ticket_numbers:
        # Build list of new ticket records preserving order from the API
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