import os
import re
import sys
from datetime import timedelta

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from seahorse import create_app  # noqa: E402
from seahorse.extensions import db  # noqa: E402
from seahorse.models import Slot, User, utcnow  # noqa: E402


@pytest.fixture()
def app(tmp_path):
    os.environ["PAYMENT_PROVIDER"] = "simulator"
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'test.db'}",
        "UPLOAD_FOLDER": str(tmp_path / "uploads"),
        "SERVER_NAME": "localhost",
        "PAYMENT_PROVIDER": "simulator",
        "PAYFAST_MERCHANT_ID": "10000100",
        "PAYFAST_MERCHANT_KEY": "46f0cd694581a",
        "PAYFAST_PASSPHRASE": "jt7NOE43FZPn",
    })
    with app.app_context():
        db.create_all()
        from seahorse.cli import seed
        seed(demo_prices=True, days=10)
        for email, role in [("super@test.io", "super_admin"), ("booker@test.io", "booking_admin"),
                            ("guide@test.io", "guide"), ("manager@test.io", "manager"),
                            ("thandi@example.com", "customer"), ("other@example.com", "customer")]:
            u = User(email=email, name=role.title(), role=role, email_verified=True)
            u.set_password("Password123")
            db.session.add(u)
        db.session.commit()
    yield app
    with app.app_context():
        db.session.remove()
        db.engine.dispose()


@pytest.fixture()
def client(app):
    return app.test_client()


def csrf(client, path="/"):
    html = client.get(path).get_data(as_text=True)
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    return m.group(1) if m else ""


def login(client, email, password="Password123", staff=True):
    token = csrf(client, "/account/staff/sign-in")
    return client.post("/account/staff/sign-in" if staff else "/account/sign-in",
                       data={"email": email, "password": password, "csrf_token": token})


def first_slot(app, offset_days=2, start="10:30"):
    with app.app_context():
        day = utcnow().date() + timedelta(days=offset_days)
        return Slot.query.filter_by(date=day, start_time=start).first().id


def booking_form(token, slot_id=None, riders=2, experience_id=1, **extra):
    data = {
        "csrf_token": token, "nonce": extra.pop("nonce", "n-" + os.urandom(4).hex()), "experience_id": str(experience_id),
        "adults": str(riders), "children": "0", "riders": str(riders), "spectators": "0",
        "contact_name": "Thandi Mokoena", "contact_email": "thandi@example.com", "contact_phone": "082 123 4567",
        "preferred_contact": "email", "accept_terms": "on",
        "location_choice": "Main beach — our usual meeting point", "horse_preference": "beginner",
    }
    if slot_id:
        data["slot_id"] = str(slot_id)
    for i in range(riders):
        data[f"rider_age_{i}"] = "18+"
        data[f"rider_exp_{i}"] = "never"
    data.update(extra)
    return data
