import pytest
from pydantic import ValidationError

from greenlight.config import Settings
from greenlight.infrastructure.notifications.delivery import _escape_markdown, _single_line
from greenlight.web.auth import is_local_path
from greenlight.web.routes.requests import _inline_disposition
from greenlight.web.security import HourlyLimit


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
        {"entra_tenant_id": ""},
    ],
)
def test_production_refuses_unsafe_settings(overrides):
    safe = {
        "environment": "production",
        "sign_in": "entra",
        "secret_key": "x" * 40,
        "base_url": "https://greenlight.orvane.example",
        "entra_tenant_id": "orvane-tenant",
    }
    with pytest.raises(ValidationError):
        Settings(**{**safe, **overrides})


def test_a_hosted_demo_needs_a_real_secret():
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        Settings(environment="demo", base_url="https://greenlight.onrender.com")


def test_running_locally_works_without_setting_a_secret():
    assert Settings(base_url="http://localhost:8000").is_local


def test_ai_reads_are_limited_per_person():
    limit = HourlyLimit(per_hour=2)

    assert limit.allow("tom") and limit.allow("tom")
    assert not limit.allow("tom")
    assert limit.allow("leila")


def test_notification_subjects_stay_on_one_line():
    assert _single_line("Approve\r\nBcc: everyone") == "Approve Bcc: everyone"


def test_names_cannot_inject_markdown_into_teams():
    assert _escape_markdown("[click](https://evil.example)") == (
        "\\[click\\]\\(https://evil\\.example\\)"
    )


def test_a_hosted_demo_needs_a_real_database():
    with pytest.raises(ValidationError, match="DATABASE_URL"):
        Settings(
            base_url="https://greenlight.onrender.com",
            secret_key="x" * 40,
            database_url="sqlite:///./greenlight.db",
        )
