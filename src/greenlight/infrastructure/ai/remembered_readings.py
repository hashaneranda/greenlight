import hashlib
from collections import OrderedDict
from threading import Lock

from pydantic import BaseModel

from greenlight.infrastructure.ai.document_readers import DocumentModel


class RememberedReadings:
    """Keeps recent readings so the same document is never paid for twice.

    People often upload the same quote again after going back a step, and finance re-reads
    an invoice they've already checked. Failed reads are not kept, so they can be retried.
    """

    def __init__(self, model: DocumentModel, size: int = 256):
        self._model = model
        self._size = size
        self._readings: OrderedDict[str, BaseModel] = OrderedDict()
        self._lock = Lock()

    def fill[T: BaseModel](
        self, content: bytes, content_type: str, instructions: str, schema: type[T]
    ) -> T:
        key = _key(content, content_type, instructions, schema)
        with self._lock:
            if key in self._readings:
                self._readings.move_to_end(key)
                return self._readings[key].model_copy(deep=True)

        reading = self._model.fill(content, content_type, instructions, schema)
        with self._lock:
            self._readings[key] = reading
            if len(self._readings) > self._size:
                self._readings.popitem(last=False)
        return reading.model_copy(deep=True)


def _key(content: bytes, content_type: str, instructions: str, schema: type[BaseModel]) -> str:
    digest = hashlib.sha256(content)
    for part in (content_type, instructions, schema.__name__):
        digest.update(b"\0" + part.encode())
    return digest.hexdigest()
