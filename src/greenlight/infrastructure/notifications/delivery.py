import logging
import smtplib
from email.message import EmailMessage

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from greenlight.application.ports import OutgoingNotification
from greenlight.config import Settings
from greenlight.domain.people import Employee
from greenlight.infrastructure.clock import SystemClock
from greenlight.infrastructure.persistence import tables

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 5
CHANNEL_NAMES = {
    (): "logged",
    ("email",): "email",
    ("teams",): "teams",
    ("email", "teams"): "email and Teams",
}


class NotificationSender:
    def __init__(self, settings: Settings, session_factory: sessionmaker[Session]):
        self._settings = settings
        self._session_factory = session_factory

    def deliver_pending(self) -> int:
        columns = tables.notifications.c
        with self._session_factory() as session:
            pending = session.scalars(
                select(OutgoingNotification)
                .where(columns.delivered_at.is_(None), columns.attempts < MAX_ATTEMPTS)
                .order_by(columns.id)
                .limit(100)
                .with_for_update(skip_locked=True)
            ).all()
            for notification in pending:
                notification.attempts += 1
                try:
                    recipient = session.get(Employee, notification.recipient_id)
                    self._deliver(notification, recipient, self._link(session, notification))
                except Exception as error:
                    log.exception("Notification %s could not be delivered", notification.id)
                    notification.delivery_error = f"{type(error).__name__}: {error}"[:500]
            session.commit()
            return len(pending)

    def _link(self, session: Session, notification: OutgoingNotification) -> str:
        if notification.request_id is None:
            return f"{self._settings.base_url}/"
        reference = session.execute(
            select(tables.spend_requests.c.reference).where(
                tables.spend_requests.c.id == notification.request_id
            )
        ).scalar_one()
        return f"{self._settings.base_url}/requests/{reference}"

    def _deliver(self, notification: OutgoingNotification, recipient: Employee, link: str) -> None:
        subject = _single_line(notification.subject)
        body = f"{notification.body}\n\nOpen in Greenlight: {link}"
        teams_webhook = self._settings.teams_webhook_url.get_secret_value()
        channels = []
        if self._settings.smtp_host:
            channels.append(("email", lambda: self._email(subject, body, recipient)))
        if teams_webhook:
            channels.append(("teams", lambda: self._post_to_teams(teams_webhook, recipient, link)))
        if not channels:
            log.info("Notification to %s: %s", recipient.email, notification.subject)

        sent, errors = [], []
        for name, send in channels:
            try:
                send()
                sent.append(name)
            except (OSError, smtplib.SMTPException, httpx.HTTPError) as error:
                log.warning("Notification %s failed on one channel: %s", notification.id, error)
                errors.append(str(error))

        notification.delivery_error = "; ".join(errors)[:500]
        if sent or not channels:
            notification.channel = CHANNEL_NAMES[tuple(sent)]
            notification.delivered_at = SystemClock().now()

    def _email(self, subject: str, body: str, recipient: Employee) -> None:
        settings = self._settings
        message = EmailMessage()
        message["From"] = settings.mail_from
        message["To"] = recipient.email
        message["Subject"] = subject
        message.set_content(body)
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
            smtp.starttls()
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password.get_secret_value())
            smtp.send_message(message)

    @staticmethod
    def _post_to_teams(webhook: str, recipient: Employee, link: str) -> None:
        # The channel may be shared, so the post says only that something is waiting.
        # The details stay behind sign-in.
        name = _escape_markdown(recipient.name)
        text = f"{name}, there is an update for you in Greenlight: {link}"
        httpx.post(webhook, json={"text": text}, timeout=10).raise_for_status()


def _single_line(text: str) -> str:
    return " ".join("".join(ch if ch.isprintable() else " " for ch in text).split())[:200]


def _escape_markdown(text: str) -> str:
    return "".join(f"\\{ch}" if ch in "\\`*_{}[]()#+-.!<>|~" else ch for ch in text)
