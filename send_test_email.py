import os
import smtplib
from email.message import EmailMessage


def send_test_email():
    """Send a test email using SMTP settings from environment variables."""
    smtp_server = os.environ.get("SMTP_SERVER")
    smtp_port = int(os.environ.get("SMTP_PORT", 587))
    smtp_username = os.environ.get("SMTP_USERNAME")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    from_email = os.environ.get("FROM_EMAIL")
    to_email = os.environ.get("TO_EMAIL")

    if not all([smtp_server, smtp_port, smtp_username, smtp_password, from_email, to_email]):
        raise ValueError("Missing one or more required environment variables for sending email.")

    subject = "Test OATH Ticket Monitor Notification"
    body = (
        "This is a test email from the OATH Ticket Monitor.\n\n"
        "If you receive this message, the email notification system is configured correctly."
    )

    msg = EmailMessage()
    msg["From"] = from_email
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)

    # Send email via SMTP with TLS
    with smtplib.SMTP(smtp_server, smtp_port) as server:
        server.starttls()
        server.login(smtp_username, smtp_password)
        server.send_message(msg)


if __name__ == "__main__":
    send_test_email()
