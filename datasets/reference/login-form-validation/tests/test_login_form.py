from pathlib import Path

import pytest
from playwright.sync_api import Page

LOGIN_PAGE = (Path(__file__).resolve().parent.parent / "web" / "login.html").as_uri()


@pytest.fixture
def form(page: Page) -> Page:
    page.goto(LOGIN_PAGE)
    return page


def submit(page: Page, email: str, password: str) -> None:
    page.fill("#email", email)
    page.fill("#password", password)
    page.click("#submit")


def test_an_email_without_at_shows_an_error(form: Page) -> None:
    submit(form, "not-an-email", "secret")
    assert form.inner_text("#error").strip() == "Enter a valid email address"


def test_an_empty_password_shows_an_error(form: Page) -> None:
    submit(form, "a@example.com", "")
    assert form.inner_text("#error").strip() == "Password is required"


def test_invalid_input_cancels_the_submit_and_valid_input_does_not(form: Page) -> None:
    form.evaluate(
        """() => {
            window.__prevented = [];
            document.getElementById('login-form').addEventListener('submit', (e) => window.__prevented.push(e.defaultPrevented));
        }"""
    )
    submit(form, "bad", "secret")
    submit(form, "a@example.com", "secret")
    assert form.evaluate("window.__prevented") == [True, False]


def test_valid_input_hides_the_error(form: Page) -> None:
    submit(form, "bad", "secret")
    submit(form, "a@example.com", "secret")
    assert form.is_hidden("#error")
