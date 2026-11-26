import re

import pytest
from fastapi.testclient import TestClient

from greenlight.config import Settings
from greenlight.infrastructure.persistence import tables
from greenlight.infrastructure.seed import seed_demo
from greenlight.web.app import create_app
from greenlight.web.container import build_container


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'web.db'}",
        secret_key="test-secret",
        cron_token="cron-secret",
    )
    container = build_container(settings)
    tables.metadata.create_all(container.session_factory.kw["bind"])
    seed_demo(container.session_factory, container.approvals)
    return TestClient(create_app(settings, container))


def csrf_from(html: str) -> str:
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def sign_in_as(client: TestClient, name: str) -> None:
    page = client.get("/sign-in").text
    employee_id = re.search(
        rf'value="([0-9a-f-]+)">\s*<span class="avatar">[A-Z]+</span>\s*<span>\s*<strong>{name}',
        page,
    ).group(1)
    client.post("/sign-in/demo", data={"csrf": csrf_from(page), "employee_id": employee_id})


def test_signed_out_visitors_are_sent_to_sign_in(client):
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/sign-in"


def test_every_main_page_renders_for_the_coo(client):
    sign_in_as(client, "Nadia Frost")

    for path in ["/", "/requests", "/requests/new", "/insights", "/payments", "/cover"]:
        response = client.get(path)
        assert response.status_code == 200, path


def test_coo_approves_from_the_request_page(client):
    sign_in_as(client, "Nadia Frost")
    home = client.get("/").text
    reference = re.search(r'href="/requests/(GL-\d+)"', home).group(1)

    page = client.get(f"/requests/{reference}").text
    assert "Approve" in page
    client.post(f"/requests/{reference}/approve", data={"csrf": csrf_from(page)})

    assert "Approved" in client.get(f"/requests/{reference}").text


def test_posting_without_the_csrf_token_is_refused(client):
    sign_in_as(client, "Nadia Frost")

    response = client.post("/requests/GL-00003/approve", data={})

    assert response.status_code == 403


def test_staff_cannot_open_the_payments_ledger_or_audit_log(client):
    sign_in_as(client, "Grace Mutombo")

    assert client.get("/payments").status_code == 403
    assert client.get("/audit").status_code == 403


def test_raising_a_request_from_a_phone_form(client):
    sign_in_as(client, "Grace Mutombo")
    form = client.get("/requests/new").text
    category_id = re.search(r'<option value="([0-9a-f-]+)"\s*>Facilities', form).group(1)

    response = client.post(
        "/requests",
        data={
            "csrf": csrf_from(form),
            "title": "Water dispenser",
            "supplier": "Kolwezi Supplies",
            "justification": "The old one leaks.",
            "category_id": category_id,
            "amount": "240",
            "currency": "USD",
        },
        files={"quotes": ("quote.pdf", b"%PDF-1.4 test", "application/pdf")},
    )

    assert response.status_code == 200
    assert "Water dispenser" in response.text
    assert "Joseph Kabila Mwamba" in response.text


def test_jobs_need_the_cron_token(client):
    assert client.post("/jobs/reminders").status_code == 401
    response = client.post("/jobs/reminders", headers={"Authorization": "Bearer cron-secret"})
    assert response.status_code == 200
