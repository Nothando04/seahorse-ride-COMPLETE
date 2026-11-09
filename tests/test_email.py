"""Sign-up confirmation and password-reset emails are handed to the mail server (fake SMTP, no internet)."""
import re

import pytest

from seahorse import notify
from seahorse.models import Notification, User
from conftest import csrf, login

SENT: list = []


class FakeSMTP:
    def __init__(self, host, port, timeout=None, **kw):
        self.host, self.port = host, port
        self.login_args = None

    def starttls(self, context=None):
        pass

    def login(self, user, password):
        self.login_args = (user, password)
        FakeSMTP.last_login = (user, password)

    def send_message(self, msg):
        SENT.append(msg)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture()
def smtp(app, monkeypatch):
    SENT.clear()
    monkeypatch.setattr(notify.smtplib, "SMTP", FakeSMTP)
    app.config.update(SMTP_HOST="smtp.gmail.com", SMTP_PORT="587", SMTP_USERNAME="seahorse.demo@gmail.com",
                      SMTP_PASSWORD="abcd efgh ijkl mnop", MAIL_FROM="Seahorse Ride <seahorse.demo@gmail.com>",
                      BREVO_API_KEY="")
    return SENT


def test_sign_up_sends_confirmation_email(client, app, smtp):
    r = client.post("/account/sign-up", data={"csrf_token": csrf(client, "/account/sign-up"), "name": "Lerato Dlamini",
                                              "email": "lerato@example.com", "password": "Strong-Pass-2026",
                                              "accept_privacy": "on"})
    assert r.status_code == 302
    mail = [m for m in smtp if m["To"] == "lerato@example.com"]
    assert mail and mail[0]["Subject"] == "Confirm your email address"
    assert "/account/verify/" in mail[0].get_content()
    assert FakeSMTP.last_login == ("seahorse.demo@gmail.com", "abcdefghijklmnop")   # spaces removed for Gmail
    with app.app_context():
        assert Notification.query.filter_by(to_email="lerato@example.com").first().status == "sent"


def test_forgot_password_sends_working_reset_link(client, app, smtp):
    client.post("/account/forgot-password", data={"csrf_token": csrf(client, "/account/forgot-password"),
                                                  "email": "thandi@example.com"})
    mail = [m for m in smtp if m["Subject"] == "Reset your password"]
    assert mail and mail[0]["To"] == "thandi@example.com"
    link = re.search(r"http://localhost(/account/reset-password/\S+)", mail[0].get_content()).group(1)
    page = client.get(link)
    assert page.status_code == 200 and "password" in page.get_data(as_text=True).lower()


def test_admin_test_email_button(client, app, smtp):
    login(client, "super@test.io")
    r = client.post("/admin/outbox", data={"csrf_token": csrf(client, "/admin/outbox"), "action": "test",
                                           "to": "owner@example.com"}, follow_redirects=True)
    assert "Test email sent to owner@example.com" in r.get_data(as_text=True)
    assert smtp[-1]["Subject"] == "Seahorse Ride test email"


def test_without_email_service_the_page_says_so(client, app):
    app.config.update(SMTP_HOST="", BREVO_API_KEY="")
    login(client, "super@test.io")
    html = client.get("/admin/outbox").get_data(as_text=True)
    assert "Not set up" in html
