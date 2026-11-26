import base64
import logging
from collections.abc import Callable

from openai import OpenAI, OpenAIError
from pydantic import BaseModel

from greenlight.infrastructure.ai.document_readers import ModelUnavailable, ReadFailed, Usage

MOST_ANSWER_TOKENS = 1_500

log = logging.getLogger(__name__)


class OpenAIModel:
    """OpenAI's Responses API, which reads PDFs and images directly."""

    def __init__(
        self,
        api_key: str,
        model: str,
        client: OpenAI | None = None,
        on_usage: Callable[[Usage], None] | None = None,
    ):
        self._client = client or OpenAI(api_key=api_key, timeout=60, max_retries=2)
        self._model = model
        self._on_usage = on_usage

    def fill[T: BaseModel](
        self, content: bytes, content_type: str, instructions: str, schema: type[T]
    ) -> T:
        try:
            response = self._client.responses.parse(
                model=self._model,
                instructions=instructions,
                input=[{"role": "user", "content": [_document_part(content, content_type)]}],
                text_format=schema,
                temperature=0,
                max_output_tokens=MOST_ANSWER_TOKENS,
            )
        except OpenAIError as error:
            raise ModelUnavailable(str(error)) from error
        if usage := response.usage:
            log.info(
                "OpenAI read a %s: %s tokens in, %s out",
                content_type,
                usage.input_tokens,
                usage.output_tokens,
            )
            if self._on_usage:
                self._on_usage(Usage(usage.input_tokens, usage.output_tokens))
        if response.output_parsed is None:
            raise ReadFailed("The model returned nothing.")
        return response.output_parsed


def _document_part(content: bytes, content_type: str) -> dict:
    data_url = f"data:{content_type};base64,{base64.b64encode(content).decode()}"
    if content_type == "application/pdf":
        return {"type": "input_file", "filename": "document.pdf", "file_data": data_url}
    return {"type": "input_image", "image_url": data_url}
