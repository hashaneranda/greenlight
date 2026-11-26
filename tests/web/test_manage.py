import re
from html import unescape

import pytest

from tests.web.test_pages import csrf_from, sign_in_as

HEADER = "email,name,job_title,department,site,manager_email,active"


def person_id(client, name: str) -> str:
    page = client.get("/manage/people", params={"q": name}).text
    return re.search(r'href="/manage/people/([0-9a-f-]+)">\s*<span class="title">', page).group(1)


def option_id(html: str, name: str) -> str:
    return re.search(rf'<option value="([0-9a-f-]+)">{name}', html).group(1)


def test_an_admin_adds_a_joiner_who_can_then_sign_in(client):
    sign_in_as(client, "Daniel Reyes")
    form = client.get("/manage/people/new").text

    response = client.post(
        "/manage/people/new",
        data={
            "csrf": csrf_from(form),
            "name": "Andre Silva",
            "email": "Andre.Silva@orvane.example",
            "job_title": "Mine Planner",
            "manager_id": option_id(form, "Priya Raman"),
            "department_id": option_id(form, "Operations"),
        },
    )

    assert "Andre Silva is added and can sign in now." in response.text
    assert "Andre Silva" in client.get("/sign-in").text


def test_a_person_the_form_refuses_keeps_what_was_typed(client):
    sign_in_as(client, "Daniel Reyes")
    form = client.get("/manage/people/new").text

    response = client.post(
        "/manage/people/new",
        data={"csrf": csrf_from(form), "name": "Andre Silva", "email": "tom.becker@orvane.example"},
    )

    assert response.status_code == 400
    assert "Tom Becker already uses tom.becker@orvane.example." in response.text
    assert 'value="Andre Silva"' in response.text


def test_finance_adds_a_category_people_can_choose(client):
    sign_in_as(client, "Tom Becker")
    page = client.get("/manage/categories").text

    client.post("/manage/categories", data={"csrf": csrf_from(page), "name": "Office furniture"})

    assert "Office furniture" in client.get("/requests/new").text
    assert "added a category" in client.get("/audit").text


def test_a_retired_category_drops_out_of_the_request_form(client):
    sign_in_as(client, "Tom Becker")
    page = client.get("/manage/categories").text
    category_id = re.search(
        r'action="/manage/categories/([0-9a-f-]+)">\s*<input[^>]+>\s*'
        r'<input type="hidden" name="name" value="Training">',
        page,
    ).group(1)

    response = client.post(
        f"/manage/categories/{category_id}",
        data={"csrf": csrf_from(page), "name": "Training", "is_active": "no"},
    )

    assert "Retired." in response.text
    assert ">Training<" not in client.get("/requests/new").text


def test_finance_sets_a_rate_and_is_named_against_it(client):
    sign_in_as(client, "Tom Becker")
    page = client.get("/manage/rates").text

    response = client.post(
        "/manage/rates", data={"csrf": csrf_from(page), "currency": "aed", "usd_per_unit": "0.2725"}
    )

    assert "AED saved." in response.text
    assert "$0.2725" in response.text
    assert "Set by Tom Becker" in response.text


@pytest.mark.parametrize(
    "path",
    [
        "/manage/people",
        "/manage/people/new",
        "/manage/people/import",
        "/manage/people/import/template.csv",
        "/manage/categories",
        "/manage/rates",
    ],
)
def test_staff_are_refused_every_manage_page(client, path):
    sign_in_as(client, "Grace Mutombo")

    assert client.get(path).status_code == 403


@pytest.mark.parametrize(
    ("name", "path"),
    [
        ("Daniel Reyes", "/manage/rates"),
        ("Daniel Reyes", "/manage/categories"),
        ("Tom Becker", "/manage/people"),
    ],
)
def test_admins_and_finance_only_open_their_own_pages(client, name, path):
    sign_in_as(client, name)

    assert client.get(path).status_code == 403


def test_a_change_without_the_csrf_token_is_refused(client):
    sign_in_as(client, "Daniel Reyes")

    response = client.post("/manage/people/new", data={"name": "Andre Silva", "email": "a@b.c"})

    assert response.status_code == 403
    assert "Andre Silva" not in client.get("/manage/people").text


@pytest.mark.parametrize(
    ("name", "shown", "hidden"),
    [
        ("Daniel Reyes", ["/manage/people"], ["/manage/categories", "/manage/rates"]),
        ("Tom Becker", ["/manage/categories", "/manage/rates"], ["/manage/people"]),
        ("Grace Mutombo", [], ["/manage/people", "/manage/categories", "/manage/rates"]),
    ],
)
def test_the_nav_shows_each_person_only_what_they_manage(client, name, shown, hidden):
    sign_in_as(client, name)
    home = client.get("/").text

    assert all(f'href="{path}"' in home for path in shown)
    assert not any(f'href="{path}"' in home for path in hidden)


def test_the_import_template_is_the_header_row(client):
    sign_in_as(client, "Daniel Reyes")

    response = client.get("/manage/people/import/template.csv")

    assert response.headers["content-type"].startswith("text/csv")
    assert response.text.strip() == HEADER


def test_an_import_is_previewed_before_it_is_applied(client):
    sign_in_as(client, "Daniel Reyes")
    page = client.get("/manage/people/import").text
    csv_text = "\n".join(
        [
            HEADER,
            "andre.silva@orvane.example,Andre Silva,Mine Planner,Operations,Kolwezi site,"
            "joseph.mwamba@orvane.example,yes",
            "grace.mutombo@orvane.example,Grace Mutombo,Site Office Lead,Operations,Kolwezi site,"
            "joseph.mwamba@orvane.example,yes",
        ]
    )

    preview = client.post(
        "/manage/people/import", data={"csrf": csrf_from(page), "csv_text": csv_text}
    ).text

    assert "Apply 2 changes" in preview
    assert "Site Administrator → <strong>Site Office Lead</strong>" in preview
    assert "Andre Silva" not in client.get("/manage/people").text

    carried = unescape(re.search(r'name="csv_text" value="([^"]*)"', preview).group(1))
    applied = client.post(
        "/manage/people/import/apply", data={"csrf": csrf_from(preview), "csv_text": carried}
    ).text

    assert "Imported: 1 added, 1 changed, 0 deactivated." in applied
    assert "Andre Silva" in applied
    assert "Site Office Lead" in applied


def test_an_import_with_a_bad_row_names_the_row_and_offers_no_apply(client):
    sign_in_as(client, "Daniel Reyes")
    page = client.get("/manage/people/import").text
    csv_text = f"{HEADER}\nandre.silva@orvane.example,Andre Silva,,Mining,,,yes\n"

    response = client.post(
        "/manage/people/import",
        files={"file": ("people.csv", csv_text.encode(), "text/csv")},
        data={"csrf": csrf_from(page)},
    )

    assert response.status_code == 400
    assert "Row 2" in response.text
    assert "There is no department called Mining." in response.text
    assert "Apply" not in response.text


def test_a_leaver_hands_their_approval_to_the_coo(client):
    sign_in_as(client, "Daniel Reyes")
    page = client.get(f"/manage/people/{person_id(client, 'Baraka Mushi')}").text
    picker = page[page.index('id="hand_over_to_id"') :]

    assert "Baraka has 1 approval waiting" in page
    assert "Amina Juma" not in picker
    response = client.post(
        re.search(r'action="(/manage/people/[0-9a-f-]+/deactivate)"', page).group(1),
        data={"csrf": csrf_from(page), "hand_over_to_id": option_id(picker, "Nadia Frost")},
    )

    assert "Baraka is deactivated and can no longer sign in." in response.text
    sign_in_as(client, "Nadia Frost")
    assert "Work permit renewal" in client.get("/").text


def test_a_leaver_with_approvals_waiting_stays_until_someone_takes_over(client):
    sign_in_as(client, "Daniel Reyes")
    page = client.get(f"/manage/people/{person_id(client, 'Baraka Mushi')}").text

    response = client.post(
        re.search(r'action="(/manage/people/[0-9a-f-]+/deactivate)"', page).group(1),
        data={"csrf": csrf_from(page)},
    )

    assert "Choose who takes it over." in response.text
    assert "Baraka Mushi" in client.get("/sign-in").text
