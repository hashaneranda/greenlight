import pytest

from greenlight.domain.catalog import Category
from greenlight.domain.errors import InvalidRequest


@pytest.fixture
def travel():
    return Category("Travel")


@pytest.fixture
def existing(travel):
    return [travel, Category("Software and subscriptions", requires_it_review=True)]


def test_a_new_category_is_named_and_open(existing):
    category = Category.create("  Training ", requires_it_review=False, existing=existing)

    assert (category.name, category.is_active) == ("Training", True)


def test_category_names_are_unique_whatever_the_case(existing):
    with pytest.raises(InvalidRequest, match="already a category called Travel"):
        Category.create("travel", requires_it_review=False, existing=existing)


def test_a_category_needs_a_name(existing):
    with pytest.raises(InvalidRequest, match="name"):
        Category.create(" ", requires_it_review=False, existing=existing)


def test_a_category_keeps_its_own_name_when_other_details_change(travel, existing):
    travel.update(name="Travel", requires_it_review=True, is_active=False, existing=existing)

    assert (travel.requires_it_review, travel.is_active) == (True, False)


def test_a_category_cannot_take_another_ones_name(travel, existing):
    with pytest.raises(InvalidRequest, match="already"):
        travel.update(
            name="software and subscriptions",
            requires_it_review=False,
            is_active=True,
            existing=existing,
        )
