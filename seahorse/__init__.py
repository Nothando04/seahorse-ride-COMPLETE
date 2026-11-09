"""Seahorse Ride — Flask application factory."""
from __future__ import annotations

import logging
import os
import time
from datetime import timedelta

from dotenv import load_dotenv
from flask import Flask, g, render_template, request, session, url_for
from sqlalchemy import event

from .extensions import db, migrate

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# PayFast's public sandbox test merchant (from PayFast's developer docs). Replace with your own sandbox
# account for real sandbox testing, and with live credentials for production.
PAYFAST_SANDBOX_DEFAULTS = {"PAYFAST_MERCHANT_ID": "10000100", "PAYFAST_MERCHANT_KEY": "46f0cd694581a",
                            "PAYFAST_PASSPHRASE": "jt7NOE43FZPn"}


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def load_config(app: Flask, overrides: dict | None = None) -> None:
    load_dotenv(os.path.join(BASE_DIR, ".env"))
    env = os.environ
    os.makedirs(app.instance_path, exist_ok=True)
    app_env = env.get("APP_ENV", "development")
    db_url = env.get("DATABASE_URL") or f"sqlite:///{os.path.join(app.instance_path, 'seahorse.db')}"
    # Hosts hand out postgres:// or postgresql:// URLs; use the installed psycopg (v3) driver for both.
    for prefix in ("postgres://", "postgresql://"):
        if db_url.startswith(prefix):
            db_url = "postgresql+psycopg://" + db_url[len(prefix):]
    provider = (env.get("PAYMENT_PROVIDER") or "auto").strip().lower()
    if provider == "auto":
        # Paystack (card number / expiry / CVV checkout) as soon as its secret key is set; PayFast sandbox until then.
        provider = "paystack" if (env.get("PAYSTACK_SECRET_KEY") or "").strip().startswith(("sk_test_", "sk_live_")) \
            else "payfast"
    sandbox = _bool(env.get("PAYFAST_SANDBOX"), True)

    app.config.update(
        APP_ENV=app_env,
        SECRET_KEY=env.get("SECRET_KEY") or ("dev-only-change-me" if app_env != "production" else None),
        SQLALCHEMY_DATABASE_URI=db_url,
        SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True},
        MAX_CONTENT_LENGTH=int(env.get("MAX_UPLOAD_MB", "5")) * 1024 * 1024,
        UPLOAD_FOLDER=env.get("UPLOAD_FOLDER") or os.path.join(app.instance_path, "uploads"),
        # Render sets RENDER_EXTERNAL_URL automatically, so PayFast links work even before you add a custom domain.
        PUBLIC_SITE_URL=(env.get("PUBLIC_SITE_URL") or env.get("RENDER_EXTERNAL_URL") or "").rstrip("/"),
        BREVO_API_KEY=env.get("BREVO_API_KEY", ""),
        PAYMENT_PROVIDER=provider,
        PAYFAST_SANDBOX=sandbox,
        PAYFAST_MERCHANT_ID=env.get("PAYFAST_MERCHANT_ID") or (PAYFAST_SANDBOX_DEFAULTS["PAYFAST_MERCHANT_ID"] if sandbox else ""),
        PAYFAST_MERCHANT_KEY=env.get("PAYFAST_MERCHANT_KEY") or (PAYFAST_SANDBOX_DEFAULTS["PAYFAST_MERCHANT_KEY"] if sandbox else ""),
        PAYFAST_PASSPHRASE=env.get("PAYFAST_PASSPHRASE") or (PAYFAST_SANDBOX_DEFAULTS["PAYFAST_PASSPHRASE"] if sandbox else ""),
        PAYFAST_NOTIFY_URL=env.get("PAYFAST_NOTIFY_URL", ""),
        TUNNEL_URL=env.get("TUNNEL_URL", ""),
        PAYFAST_VERIFY_SOURCE=_bool(env.get("PAYFAST_VERIFY_SOURCE"), True),
        # Paystack: card checkout (card number, expiry, CVV on Paystack's page). sk_test_… keys = test mode.
        PAYSTACK_SECRET_KEY=env.get("PAYSTACK_SECRET_KEY", ""),
        PAYSTACK_PUBLIC_KEY=env.get("PAYSTACK_PUBLIC_KEY", ""),
        PAYSTACK_CURRENCY=env.get("PAYSTACK_CURRENCY", "ZAR"),
        SMTP_HOST=env.get("SMTP_HOST", ""),
        SMTP_PORT=env.get("SMTP_PORT", "587"),
        SMTP_USERNAME=env.get("SMTP_USERNAME", ""),
        SMTP_PASSWORD=env.get("SMTP_PASSWORD", ""),
        MAIL_FROM=env.get("MAIL_FROM", ""),
        PERMANENT_SESSION_LIFETIME=timedelta(days=14),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=app_env == "production",
        PREFERRED_URL_SCHEME="https" if app_env == "production" else "http",
    )
    if overrides:
        app.config.update(overrides)

    if app.config["APP_ENV"] == "production":
        if not app.config["SECRET_KEY"] or app.config["SECRET_KEY"] == "dev-only-change-me":
            raise RuntimeError("Set a long random SECRET_KEY in production.")
        if app.config["PAYMENT_PROVIDER"] not in {"payfast", "paystack"}:
            raise RuntimeError("The payment simulator cannot run in production. "
                               "Set PAYMENT_PROVIDER=paystack or PAYMENT_PROVIDER=payfast.")
    if app.config.get("SERVER_NAME") is None and app.config["PUBLIC_SITE_URL"]:
        # Lets emails/CLI jobs build absolute links outside a request.
        from urllib.parse import urlparse
        parsed = urlparse(app.config["PUBLIC_SITE_URL"])
        app.config.setdefault("CLI_URL_HOST", parsed.netloc)
        app.config["PREFERRED_URL_SCHEME"] = parsed.scheme or app.config["PREFERRED_URL_SCHEME"]


def create_app(overrides: dict | None = None) -> Flask:
    app = Flask(__name__, instance_path=os.path.join(BASE_DIR, "instance"),
                template_folder=os.path.join(BASE_DIR, "templates"), static_folder=os.path.join(BASE_DIR, "static"))
    load_config(app, overrides)
    logging.basicConfig(level=logging.INFO)

    if app.config["APP_ENV"] == "production":
        # Hosts like Render terminate HTTPS at a proxy: trust its headers so links and cookies use https.
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    db.init_app(app)
    migrate.init_app(app, db, directory=os.path.join(BASE_DIR, "migrations"))

    with app.app_context():
        if db.engine.dialect.name == "sqlite":
            _sqlite_serialisable(db.engine)

    from . import models  # noqa: F401 — register models
    from .routes import account, admin, booking, payments, public
    app.register_blueprint(public.bp)
    app.register_blueprint(booking.bp)
    app.register_blueprint(account.bp)
    app.register_blueprint(payments.bp)
    app.register_blueprint(admin.bp)

    _register_hooks(app)
    _register_template_helpers(app)
    from .cli import register_cli
    register_cli(app)
    return app


def _sqlite_serialisable(engine) -> None:
    """SQLite: begin every transaction with BEGIN IMMEDIATE so two approvals can't oversell one slot."""

    @event.listens_for(engine, "connect")
    def _connect(dbapi_conn, _record):
        dbapi_conn.isolation_level = None
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=10000")
        cur.close()

    @event.listens_for(engine, "begin")
    def _begin(conn):
        conn.exec_driver_sql("BEGIN IMMEDIATE")


_last_jobs_run = {"t": 0.0}


def _register_hooks(app: Flask) -> None:
    from .notify import deliver_pending
    from .security import check_csrf, load_current_user

    @app.before_request
    def _before():
        load_current_user()
        check_csrf()
        # Light-weight scheduled jobs (also available as `flask run-jobs` for cron).
        if not app.config.get("TESTING") and time.time() - _last_jobs_run["t"] > 300:
            _last_jobs_run["t"] = time.time()
            from .jobs import run_jobs
            try:
                run_jobs()
            except Exception:  # noqa: BLE001
                app.logger.exception("Background jobs failed")
                db.session.rollback()

    @app.after_request
    def _after(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "geolocation=(), camera=(), microphone=()")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; script-src 'self'; frame-ancestors 'none'; "
            "form-action 'self' https://sandbox.payfast.co.za https://www.payfast.co.za https://checkout.paystack.com; "
            "base-uri 'self'")
        if app.config["APP_ENV"] == "production":
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response

    @app.teardown_request
    def _deliver(exc):
        if exc is None and not app.config.get("TESTING_NO_MAIL_FLUSH"):
            try:
                deliver_pending()
            except Exception:  # noqa: BLE001
                app.logger.exception("Email delivery failed")
                db.session.rollback()

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(413)
    @app.errorhandler(429)
    @app.errorhandler(500)
    def _error(err):
        code = getattr(err, "code", 500) or 500
        if code == 500:
            db.session.rollback()
        messages = {400: "Something about that request wasn't right.", 403: "You don't have access to that page.",
                    404: "We couldn't find that page.", 413: "That file is too large.",
                    429: "Too many attempts. Please wait a few minutes and try again.",
                    500: "Something went wrong on our side. Please try again."}
        detail = getattr(err, "description", None) if code == 400 else None
        return render_template("public/error.html", code=code, message=detail or messages.get(code, "Error")), code


def _register_template_helpers(app: Flask) -> None:
    from .models import Message
    from .notify import money
    from .pricing import price_label
    from .security import csrf_token
    from .settings import OPS_STATUS_LABELS, all_settings
    from .workflow import CUSTOMER_EXPLANATION, STATUS_STYLE

    app.jinja_env.filters["money"] = money
    app.jinja_env.filters["price_label"] = lambda e: price_label(e.price_cents, e.price_basis)
    app.jinja_env.filters["lines"] = lambda text: [ln.strip(" -•\t") for ln in (text or "").splitlines() if ln.strip(" -•\t")]
    app.jinja_env.filters["dt"] = lambda d, fmt="%d %b %Y, %H:%M": d.strftime(fmt) if d else "—"
    # Globals (not context processors) so imported macros can use them too.
    from .photos import PhotoLibrary
    library = PhotoLibrary(os.path.join(app.static_folder, "img", "photos"))
    app.extensions["photos"] = library

    def _photo_url(f):
        return url_for("static", filename=f"img/photos/{f}") if f else None

    def photo(role):
        """Photo for a page area: admin-uploaded hero first, then downloaded/added photos, else None (illustration)."""
        if role == "hero" and all_settings().get("hero_image"):
            return url_for("public.media", rel=all_settings()["hero_image"])
        return _photo_url(library.for_role(role))

    def exp_photo(e):
        if getattr(e, "image_path", None):
            return url_for("public.media", rel=e.image_path)
        return _photo_url(library.for_package(e.slug or "", e.id or 0))

    from .horses import type_summary
    from .horse_profiles import photo_key
    from .models import HORSE_TYPES

    from .horse_photos import HorsePhotoLibrary
    horse_lib = HorsePhotoLibrary(os.path.join(app.static_folder, "img", "horses"))
    app.extensions["horse_photos"] = horse_lib

    own_dir = os.path.join(app.static_folder, "img", "horses", "own")

    def own_horse_photo(name: str):
        """Your own photo in static/img/horses/own/, named after the horse (e.g. willow.jpg, sea-biscuit.png)."""
        from .horse_photos import slug
        for ext in (".jpg", ".jpeg", ".png", ".webp", ".JPG", ".JPEG", ".PNG", ".WEBP"):
            if os.path.isfile(os.path.join(own_dir, slug(name) + ext)):
                return f"img/horses/own/{slug(name)}{ext}"
        return None

    def horse_photo(h):
        """Uploaded photo (Admin → Horses) → your own photo in static/img/horses/own → the business's photo in
        static/img/photos → a downloaded breed photo in static/img/horses → None ('Photo coming soon')."""
        if h.photo_path:
            return url_for("public.media", rel=h.photo_path)
        own = own_horse_photo(h.name)
        if own:
            return url_for("static", filename=own)
        key = photo_key(h.name)
        if key and library.find(key):
            return _photo_url(library.find(key))
        f = horse_lib.file_for(h.name)
        return url_for("static", filename=f"img/horses/{f}") if f else None

    def horse_credit(h):
        """Photographer/licence for downloaded breed photos (only when that photo is the one shown)."""
        if h.photo_path or own_horse_photo(h.name) or (photo_key(h.name) and library.find(photo_key(h.name))) or not horse_lib.file_for(h.name):
            return None
        return horse_lib.credit(h.name)

    COATS = {"chestnut": ("#a0522d", "#6e3518"), "bay": ("#7a4a24", "#1f140c"), "dark bay": ("#4a2c18", "#140c07"),
             "brown": ("#6b4226", "#2b1a0e"), "grey": ("#bdbbb4", "#6f6d68"), "palomino": ("#d9a95b", "#f4e7c7"),
             "dun": ("#c9a46a", "#3d2a18"), "spotted dun": ("#cfae78", "#3d2a18"), "black": ("#2b2b2b", "#0d0d0d"),
             "black and white": ("#2b2b2b", "#0d0d0d"), "bay roan": ("#8a6a5a", "#2b1a12")}

    def horse_coat(h):
        return COATS.get((h.colour or "").lower(), ("#8b5a2b", "#3b2412"))

    app.jinja_env.globals.update(horse_photo=horse_photo, horse_coat=horse_coat, horse_credit=horse_credit)
    app.jinja_env.globals.update(horse_types=type_summary, horse_type_label=lambda k: HORSE_TYPES.get(k, ("No preference",))[0] if k else "No preference")
    app.jinja_env.globals.update(photo=photo, exp_photo=exp_photo, photo_n=lambda i: _photo_url(library.for_index(i)),
                                 photo_count=lambda: len(library.files()),
                                 photo_alt=lambda url: library.alt((url or "").rsplit("/", 1)[-1]))
    app.jinja_env.globals.update(csrf_token=csrf_token, status_style=lambda s: STATUS_STYLE.get(s, "grey"),
                                 status_explanation=lambda s: CUSTOMER_EXPLANATION.get(s, ""))

    @app.context_processor
    def _ctx():
        settings = all_settings()
        unread = 0
        user = g.get("user")
        if user is not None and user.is_staff:
            unread = Message.query.filter_by(sender="customer", read_by_admin=False).count()
        return {
            "site": settings,
            "ops_label": OPS_STATUS_LABELS.get(settings.get("ops_status"), "Normal Operations"),
            "current_user": user,
            "admin_unread": unread,
            "payment_provider": app.config["PAYMENT_PROVIDER"],
            "gateway_name": "Paystack" if app.config["PAYMENT_PROVIDER"] == "paystack" else "PayFast",
            "cookie_ok": request.cookies.get("cookie_notice") == "1",
            "session": session,
        }
