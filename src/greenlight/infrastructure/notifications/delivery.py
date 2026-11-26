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
                recipient = session.get(Employee, notification.recipient_id)
                link = self._link(session, notification)
                self._deliver(notification, recipient, link)
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
        notification.attempts += 1
        body = f"{notification.body}\n\nOpen in Greenlight: {link}"
        teams_webhook = self._settings.teams_webhook_url.get_secret_value()
        try:
            if self._settings.smtp_host:
                self._email(notification.subject, body, recipient)
            if teams_webhook:
                self._post_to_teams(teams_webhook, notification.subject, body, recipient)
            if not self._settings.smtp_host and not teams_webhook:
                log.info("Notification to %s: %s", recipient.email, notification.subject)
        except (OSError, smtplib.SMTPException, httpx.HTTPError) as error:
            log.warning("Notification %s failed: %s", notification.id, error)
            notification.delivery_error = str(error)[:500]
            return
        notification.delivered_at = SystemClock().now()
        notification.delivery_error = ""

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
    def _post_to_teams(webhook: str, subject: str, body: str, recipient: Employee) -> None:
        text = f"**{subject}** for {recipient.name}\n\n{body}"
        httpx.post(webhook, json={"text": text}, timeout=10).raise_for_status()
