import pytest
from pydantic import ValidationError

from greenlight.config import Settings
from greenlight.web.auth import is_local_path
from greenlight.web.routes.requests import _inline_disposition


@pytest.mark.parametrize(
    ("url", "allowed"),
    [
        ("/requests/GL-00001", True),
        ("/", True),
        ("//evil.example/login", False),
        ("/\\evil.example", False),
        ("https://evil.example", False),
    ],
)
def test_only_local_paths_are_followed_after_sign_in(url, allowed):
    assert is_local_path(url) is allowed


def test_quote_filenames_in_any_language_can_be_downloaded():
    header = _inline_disposition('报价单 – "final";.pdf')

    header.encode("latin-1")
    assert "filename*=UTF-8''" in header


@pytest.mark.parametrize(
    "overrides",
    [
        {"sign_in": "demo"},
        {"secret_key": "short"},
        {"base_url": "http://greenlight.orvane.example"},
    ],
)
def test_production_refuses_unsafe_settings(overrides):
    safe = {
        "environment": "production",
        "sign_in": "entra",
        "secret_key": "x" * 40,
        "base_url": "https://greenlight.orvane.example",
    }
    with pytest.raises(ValidationError):
        Settings(**{**safe, **overrides})
