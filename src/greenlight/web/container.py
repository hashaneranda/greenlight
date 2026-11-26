from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from greenlight.application.administration import Administration
from greenlight.application.queries import SpendQueries
from greenlight.application.sign_in import MicrosoftSignIn
from greenlight.application.use_cases import SpendApprovals
from greenlight.config import Settings
from greenlight.infrastructure.ai.document_readers import (
    AIInvoiceReader,
    AIQuoteReader,
    DocumentModel,
)
from greenlight.infrastructure.ai.groq_model import GroqModel
from greenlight.infrastructure.ai.openai_model import OpenAIModel
from greenlight.infrastructure.ai.remembered_readings import RememberedReadings
from greenlight.infrastructure.clock import SystemClock
from greenlight.infrastructure.notifications.delivery import NotificationSender
from greenlight.infrastructure.persistence.orm import start_mappers
from greenlight.infrastructure.persistence.unit_of_work import SqlUnitOfWork, make_engine
from greenlight.infrastructure.storage.database import DatabaseFileStorage


@dataclass
class Container:
    settings: Settings
    session_factory: sessionmaker[Session]
    approvals: SpendApprovals
    admin: Administration
    queries: SpendQueries
    notifications: NotificationSender
    microsoft_sign_in: MicrosoftSignIn

    ai_enabled: bool


def document_model(settings: Settings) -> DocumentModel | None:
    """Groq when its key is set, else OpenAI, else no AI reading at all."""
    if groq_key := settings.groq_api_key.get_secret_value():
        return RememberedReadings(GroqModel(groq_key, settings.groq_model))
    if openai_key := settings.openai_api_key.get_secret_value():
        return RememberedReadings(OpenAIModel(openai_key, settings.openai_model))
    return None


def build_container(settings: Settings) -> Container:
    start_mappers()
    engine = make_engine(settings.database_url)
    session_factory = sessionmaker(engine, expire_on_commit=False)
    clock = SystemClock()

    def unit_of_work() -> SqlUnitOfWork:
        return SqlUnitOfWork(session_factory)

    model = document_model(settings)
    invoice_reader = AIInvoiceReader(model) if model else None
    quote_reader = AIQuoteReader(model) if model else None
    return Container(
        settings=settings,
        session_factory=session_factory,
        approvals=SpendApprovals(
            unit_of_work,
            DatabaseFileStorage(engine),
            clock,
            invoice_reader,
            max_upload_bytes=settings.max_upload_mb * 1024 * 1024,
            quote_reader=quote_reader,
        ),
        admin=Administration(unit_of_work, clock),
        queries=SpendQueries(unit_of_work, clock),
        notifications=NotificationSender(settings, session_factory),
        microsoft_sign_in=MicrosoftSignIn(unit_of_work),
        ai_enabled=model is not None,
    )
