import base64
import io
import json
import logging
import re
from collections.abc import Callable

import pypdfium2
from openai import OpenAI, OpenAIError
from pydantic import BaseModel, ValidationError
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from greenlight.infrastructure.ai.document_readers import ReadFailed, Usage

GROQ_URL = "https://api.groq.com/openai/v1"
MOST_PAGES_AS_IMAGES = 3
TEXT_WORTH_READING = 40
MOST_TEXT = 12_000
MOST_ANSWER_TOKENS = 1_500

log = logging.getLogger(__name__)


class GroqModel:
    """Groq's OpenAI-compatible chat API.

    Groq takes text and images but not files, so a PDF goes as its text, or as pictures of its
    first pages when it is a scan with no text in it.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        client: OpenAI | None = None,
        on_usage: Callable[[Usage], None] | None = None,
    ):
        self._client = client or OpenAI(
            api_key=api_key, base_url=GROQ_URL, timeout=60, max_retries=2
        )
        self._model = model
        self._on_usage = on_usage

    def fill[T: BaseModel](
        self, content: bytes, content_type: str, instructions: str, schema: type[T]
    ) -> T:
        shape = json.dumps(_without_titles(schema.model_json_schema()), separators=(",", ":"))
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {
                        "role": "system",
                        "content": f"{instructions}\n\nReply with one JSON object that "
                        f"matches this JSON schema:\n{shape}",
                    },
                    {"role": "user", "content": _document_parts(content, content_type)},
                ],
                response_format={"type": "json_object"},
                temperature=0,
                max_completion_tokens=MOST_ANSWER_TOKENS,
            )
        except OpenAIError as error:
            raise ReadFailed(str(error)) from error

        if usage := response.usage:
            log.info(
                "Groq read a %s: %s tokens in, %s out",
                content_type,
                usage.prompt_tokens,
                usage.completion_tokens,
            )
            if self._on_usage:
                self._on_usage(Usage(usage.prompt_tokens, usage.completion_tokens))
        answer = _without_thinking(response.choices[0].message.content or "")
        try:
            return schema.model_validate_json(answer)
        except ValidationError as error:
            raise ReadFailed("The model's answer did not fit the schema.") from error


def _document_parts(content: bytes, content_type: str) -> list[dict]:
    if content_type != "application/pdf":
        return [_image_part(content, content_type)]
    text = _pdf_text(content)
    if len(text) >= TEXT_WORTH_READING:
        return [{"type": "text", "text": f"The document's text:\n\n{_shortened(text)}"}]
    return [_image_part(page, "image/png") for page in _pdf_pages_as_png(content)]


def _image_part(content: bytes, content_type: str) -> dict:
    encoded = base64.b64encode(content).decode()
    return {"type": "image_url", "image_url": {"url": f"data:{content_type};base64,{encoded}"}}


def _pdf_text(content: bytes) -> str:
    try:
        pages = PdfReader(io.BytesIO(content)).pages
        return "\n\n".join(page.extract_text() or "" for page in pages).strip()
    except PdfReadError as error:
        raise ReadFailed("The PDF could not be opened.") from error


def _pdf_pages_as_png(content: bytes) -> list[bytes]:
    pdf = pypdfium2.PdfDocument(content)
    images = []
    for index in range(min(len(pdf), MOST_PAGES_AS_IMAGES)):
        buffer = io.BytesIO()
        pdf[index].render(scale=2).to_pil().save(buffer, format="PNG")
        images.append(buffer.getvalue())
    return images


def _shortened(text: str) -> str:
    """Long documents keep their start and end, where suppliers put who, what and the total."""
    if len(text) <= MOST_TEXT:
        return text
    half = MOST_TEXT // 2
    return f"{text[:half]}\n\n[…middle pages left out…]\n\n{text[-half:]}"


def _without_titles(schema: object) -> object:
    if isinstance(schema, dict):
        return {key: _without_titles(value) for key, value in schema.items() if key != "title"}
    if isinstance(schema, list):
        return [_without_titles(value) for value in schema]
    return schema


def _without_thinking(answer: str) -> str:
    return re.sub(r"<think>.*?</think>", "", answer, flags=re.DOTALL).strip()
