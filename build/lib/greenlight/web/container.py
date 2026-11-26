from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from greenlight.application.queries import SpendQueries
from greenlight.application.sign_in import MicrosoftSignIn
from greenlight.application.use_cases import SpendApprovals
from greenlight.config import Settings
from greenlight.infrastructure.clock import SystemClock
from greenlight.infrastructure.invoices.openai_reader import OpenAIInvoiceReader
from greenlight.infrastructure.notifications.delivery import NotificationSender
from greenlight.infrastructure.persistence.orm import start_mappers
from greenlight.infrastructure.persistence.unit_of_work import SqlUnitOfWork, make_engine
from greenlight.infrastructure.storage.database import DatabaseFileStorage


@dataclass
class Container:
    settings: Settings
    session_factory: sessionmaker[Session]
    approvals: SpendApprovals
    queries: SpendQueries
    notifications: NotificationSender
    microsoft_sign_in: MicrosoftSignIn

    @property
    def invoice_reading_enabled(self) -> bool:
        return bool(self.settings.openai_api_key.get_secret_value())


def build_container(settings: Settings) -> Container:
    start_mappers()
    engine = make_engine(settings.database_url)
    session_factory = sessionmaker(engine, expire_on_commit=False)
    clock = SystemClock()

    def unit_of_work() -> SqlUnitOfWork:
        return SqlUnitOfWork(session_factory)

    api_key = settings.openai_api_key.get_secret_value()
    invoice_reader = OpenAIInvoiceReader(api_key, settings.openai_model) if api_key else None
    return Container(
        settings=settings,
        session_factory=session_factory,
        approvals=SpendApprovals(
            unit_of_work,
            DatabaseFileStorage(engine),
            clock,
            invoice_reader,
            max_upload_bytes=settings.max_upload_mb * 1024 * 1024,
        ),
        queries=SpendQueries(unit_of_work, clock),
        notifications=NotificationSender(settings, session_factory),
        microsoft_sign_in=MicrosoftSignIn(unit_of_work),
    )
