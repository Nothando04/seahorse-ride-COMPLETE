"""The live-site start-up: demo data on an empty database, the owner's admin account and database URLs."""
import os

from seahorse import create_app
from seahorse.extensions import db
from seahorse.models import Booking, Notification, User


def test_demo_data_fills_an_empty_database(tmp_path):
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'live.db'}",
                      "SERVER_NAME": "localhost"})
    with app.app_context():
        db.create_all()
        from seahorse.demo_data import populate_demo
        counts = populate_demo("DemoPass2026")
        assert len(counts) >= 12 and Booking.query.count() == 14
        assert User.query.filter_by(email="admin@seahorse.test").first().check_password("DemoPass2026")
        # Example e-mails must never go out to the made-up addresses
        pending = Notification.query.filter(Notification.status.in_(["queued", "failed"]), Notification.attempts < 5)
        assert pending.count() == 0
        db.session.remove()
        db.drop_all()


def test_postgres_urls_use_installed_driver(monkeypatch):
    for url in ("postgres://u:p@h/db", "postgresql://u:p@h/db"):
        monkeypatch.setenv("DATABASE_URL", url)
        app = create_app({"TESTING": True})
        assert app.config["SQLALCHEMY_DATABASE_URI"] == "postgresql+psycopg://u:p@h/db"
    monkeypatch.delenv("DATABASE_URL")


def test_owner_admin_is_created_even_when_demo_admin_exists(app, monkeypatch):
    import app as entry
    monkeypatch.setenv("ADMIN_EMAIL", "owner@example.com")
    monkeypatch.setenv("ADMIN_PASSWORD", "LongPassword2026")
    monkeypatch.setattr(entry, "app", app)
    with app.app_context():
        assert User.query.filter_by(role="super_admin").count() == 1  # super@test.io from the fixture
        entry.ensure_admin()
        owner = User.query.filter_by(email="owner@example.com").first()
        assert owner.role == "super_admin" and owner.check_password("LongPassword2026")
        owner.set_password("ChangedByOwner99")
        db.session.commit()
        entry.ensure_admin()  # a restart must not reset the owner's new password
        assert User.query.filter_by(email="owner@example.com").first().check_password("ChangedByOwner99")
