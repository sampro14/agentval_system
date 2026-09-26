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


def test_email_without_at_shows_error(form):
    submit(form, "not-an-email", "secret")
    assert form.is_visible("#error")
    assert form.inner_text("#error").strip() == "Enter a valid email address"


def test_empty_password_shows_error(form):
    submit(form, "a@example.com", "")
    assert form.is_visible("#error")
    assert form.inner_text("#error").strip() == "Password is required"


def test_email_error_takes_priority(form):
    submit(form, "bad", "")
    assert form.inner_text("#error").strip() == "Enter a valid email address"


def watch_submits(page: Page) -> None:
    """Record whether the page's own handler cancelled the submit event.

    This listener is added after the page's script has registered its handler, so it runs later and
    sees the final `defaultPrevented` value.
    """
    page.evaluate(
        """() => {
            window.__prevented = [];
            document.getElementById('login-form').addEventListener('submit', (e) => {
                window.__prevented.push(e.defaultPrevented);
            });
        }"""
    )


def test_invalid_submit_is_cancelled_and_valid_submit_is_not(form):
    watch_submits(form)
    submit(form, "bad", "secret")
    submit(form, "", "")
    submit(form, "a@example.com", "secret")
    assert form.evaluate("window.__prevented") == [True, True, False]


def test_valid_values_hide_the_error(form):
    submit(form, "bad", "secret")
    assert form.is_visible("#error")
    submit(form, "a@example.com", "secret")
    assert form.is_hidden("#error")
