"""Formats outgoing email. Sending is handled elsewhere; this module only builds the messages."""

from email.message import EmailMessage

SENDER = "no-reply@example.com"


def build_message(to: str, subject: str, body: str) -> EmailMessage:
    message = EmailMessage()
    message["From"] = SENDER
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    return message


def welcome_email(to: str) -> EmailMessage:
    return build_message(to, "Welcome!", f"Thanks for signing up with {to}.")
