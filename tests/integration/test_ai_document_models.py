import io
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
from openai import APIConnectionError
from PIL import Image
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from greenlight.config import Settings
from greenlight.domain.errors import InvalidRequest
from greenlight.infrastructure.ai.document_readers import AIInvoiceReader, AIQuoteReader
from greenlight.infrastructure.ai.groq_model import GROQ_URL, MOST_TEXT, GroqModel
from greenlight.infrastructure.ai.openai_model import OpenAIModel
from greenlight.infrastructure.ai.remembered_readings import RememberedReadings
from greenlight.web.container import document_model

INVOICE_JSON = """{
  "supplier": "Figma Inc", "invoice_number": "INV-204", "currency": "USD", "total": 6480,
  "invoice_date": "2026-11-02", "purchase_reference": "gl-00007",
  "lines": [{"description": "Professional plan", "amount": 6480}], "unclear_fields": []
}"""


class FakeChat:
    def __init__(self, answer: str = INVOICE_JSON, error: Exception | None = None):
        self.answer, self.error, self.calls = answer, error, []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **request):
        self.calls.append(request)
        if self.error:
            raise self.error
        message = SimpleNamespace(content=self.answer)
        usage = SimpleNamespace(prompt_tokens=900, completion_tokens=120)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)

    @property
    def sent(self) -> list[dict]:
        return self.calls[-1]["messages"][1]["content"]


def groq(client: FakeChat) -> GroqModel:
    return GroqModel("key", "qwen/qwen3.8-27b", client=client)


def pdf_with_text(text: str) -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(612, 792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 72 700 Td ({text}) Tj ET".encode())
    page[NameObject("/Contents")] = writer._add_object(stream)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def scanned_pdf() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (300, 400), "white").save(buffer, format="PDF")
    return buffer.getvalue()


def test_an_invoice_is_read_through_groq_and_checked_against_the_schema():
    client = FakeChat()
    invoice = AIInvoiceReader(groq(client)).read(b"\x89PNG", "image/png")

    assert invoice.supplier == "Figma Inc"
    assert invoice.total == Decimal("6480")
    assert invoice.purchase_reference == "GL-00007"
    request = client.calls[0]
    assert request["response_format"] == {"type": "json_object"}
    assert request["max_completion_tokens"] <= 1500
    assert '"title"' not in request["messages"][0]["content"]
    assert "JSON schema" in request["messages"][0]["content"]
    assert client.sent[0]["image_url"]["url"].startswith("data:image/png;base64,")


def test_a_pdf_with_text_is_sent_as_text_because_groq_takes_no_files():
    client = FakeChat()
    AIInvoiceReader(groq(client)).read(
        pdf_with_text("Invoice INV-204 from Figma Inc, total USD 6480"), "application/pdf"
    )

    assert client.sent[0]["type"] == "text"
    assert "INV-204" in client.sent[0]["text"]


def test_a_scanned_pdf_is_sent_as_pictures_of_its_pages():
    client = FakeChat()
    AIInvoiceReader(groq(client)).read(scanned_pdf(), "application/pdf")

    assert client.sent[0]["image_url"]["url"].startswith("data:image/png;base64,")


def test_thinking_before_the_answer_is_ignored():
    client = FakeChat(answer=f"<think>The total is at the bottom.</think>{INVOICE_JSON}")
    assert AIInvoiceReader(groq(client)).read(b"img", "image/jpeg").invoice_number == "INV-204"


@pytest.mark.parametrize(
    "client",
    [
        FakeChat(answer='{"supplier": "Figma Inc"}'),
        FakeChat(error=APIConnectionError(request=httpx.Request("POST", GROQ_URL))),
    ],
    ids=["answer does not fit the schema", "provider unreachable"],
)
def test_a_failed_read_asks_the_person_to_fill_it_in_by_hand(client):
    with pytest.raises(InvalidRequest, match="fill it in by hand"):
        AIQuoteReader(groq(client)).read(b"img", "image/png", ["Software"])


def test_a_long_pdf_keeps_its_first_and_last_pages_where_the_total_is():
    client = FakeChat()
    pages = "Figma Inc quote " + "terms and conditions " * 1500 + "Grand total USD 6480"
    AIInvoiceReader(groq(client)).read(pdf_with_text(pages), "application/pdf")

    sent = client.sent[0]["text"]
    assert len(sent) < MOST_TEXT + 200
    assert "Figma Inc" in sent and "Grand total USD 6480" in sent


def test_the_same_document_is_only_sent_to_the_model_once():
    client = FakeChat()
    reader = AIInvoiceReader(RememberedReadings(groq(client)))

    reader.read(b"invoice one", "image/png")
    reader.read(b"invoice one", "image/png")
    reader.read(b"invoice two", "image/png")

    assert len(client.calls) == 2


def test_a_failed_read_is_not_remembered_so_it_can_be_tried_again():
    client = FakeChat(answer="{}")
    reader = AIInvoiceReader(RememberedReadings(groq(client)))
    for _ in range(2):
        with pytest.raises(InvalidRequest):
            reader.read(b"blurred", "image/png")

    assert len(client.calls) == 2


def test_groq_is_used_when_its_key_is_set_and_openai_otherwise():
    def provider(settings: Settings) -> type | None:
        model = document_model(settings)
        return type(model._model) if model else None

    assert provider(Settings(groq_api_key="g", openai_api_key="o")) is GroqModel
    assert provider(Settings(openai_api_key="o")) is OpenAIModel
    assert provider(Settings(groq_api_key="", openai_api_key="")) is None
