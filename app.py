"""Seahorse Ride — run the website locally.

    python app.py

On start-up this:
  1. creates/updates the database from the migrations,
  2. loads the package catalogue and ride sessions on the very first run,
  3. creates the administrator account from ADMIN_EMAIL / ADMIN_PASSWORD in .env (if no admin exists yet),
  4. downloads the horse photos in the background (first run only),
  5. starts the web server on http://127.0.0.1:5000

Production: gunicorn "app:app" (see README).
"""
import os

from flask_migrate import upgrade

from seahorse import create_app
from seahorse.extensions import db
from seahorse.models import Experience, Horse, User

app = create_app()

DEV_ADMIN_EMAIL = "admin@seahorse.local"
DEV_ADMIN_PASSWORD = "Seahorse@2026"


def prepare_database(demo_prices: bool = True) -> None:
    with app.app_context():
        upgrade()  # applies migrations/ — the database schema is version-controlled
        if Experience.query.count() == 0:
            if os.environ.get("DEMO_DATA", "").strip().lower() in ("1", "true", "yes"):
                # Fresh live/demo site: load example bookings in every status so the system can be shown straight away.
                from seahorse.demo_data import populate_demo
                demo_password = os.environ.get("DEMO_PASSWORD") or os.environ.get("ADMIN_PASSWORD") or DEV_ADMIN_PASSWORD
                base = app.config.get("PUBLIC_SITE_URL") or "http://localhost:5000"
                with app.test_request_context("/", base_url=base):
                    populate_demo(demo_password)
                print("Demo data loaded: packages, horses, sessions, customers and bookings in every status.")
                print("  Demo accounts (admin@seahorse.test, customer@seahorse.test, …) use DEMO_PASSWORD"
                      " (or ADMIN_PASSWORD when DEMO_PASSWORD is not set).")
            else:
                from seahorse.cli import seed
                seed(demo_prices=demo_prices)
                print("Database seeded with the package catalogue and 45 days of ride sessions.")
        from seahorse.cli import seed_horses
        seed_horses()  # also fills empty starter-horse profiles
        from seahorse.cli import seed_gallery
        seed_gallery()
        sync_demo_passwords()
        ensure_admin()


DEMO_LOGINS = [  # the accounts listed in the user guide
    ("admin@seahorse.test", "Sam Admin", "super_admin"),
    ("guide@seahorse.test", "Gugu Guide", "guide"),
    ("customer@seahorse.test", "Thandi Mokoena", "customer"),
]


def sync_demo_passwords() -> None:
    """Makes sure the user-guide logins exist and work on every start, on any database (new or old):
    admin@seahorse.test uses ADMIN_PASSWORD; the other demo accounts (…@seahorse.test) use DEMO_PASSWORD.
    Missing accounts are created; existing ones get the configured password and role."""
    from seahorse.demo_data import DEMO_STAFF
    production = app.config["APP_ENV"] == "production"
    admin_pw = os.environ.get("ADMIN_PASSWORD") or ("" if production else DEV_ADMIN_PASSWORD)
    demo_pw = os.environ.get("DEMO_PASSWORD") or ("" if production else "Password123")
    wanted = {e: (n, r) for e, n, r in DEMO_STAFF}
    wanted.update({e: (n, r) for e, n, r in DEMO_LOGINS})
    changed = []
    for email, (name, role) in wanted.items():
        password = admin_pw if role == "super_admin" else demo_pw
        user = User.query.filter_by(email=email).first()
        if user is None:
            if email not in {e for e, _, _ in DEMO_LOGINS} or not password:
                continue  # only the three guide logins are created; other demo accounts are only kept in sync
            user = User(email=email, name=name, role=role, email_verified=True)
            db.session.add(user)
        elif not password:
            continue
        if user.role != role and email in {e for e, _, _ in DEMO_LOGINS}:
            user.role = role
        if not user.password_hash or not user.check_password(password):
            user.set_password(password)
            changed.append(email)
    if changed:
        db.session.commit()
        print("Demo logins ready: " + ", ".join(changed))


def ensure_admin() -> None:
    """Admins must sign in like everyone else; this makes sure there is always one admin account."""
    email = (os.environ.get("ADMIN_EMAIL") or "").strip().lower()
    password = os.environ.get("ADMIN_PASSWORD") or ""
    if email:
        existing = User.query.filter_by(email=email).first()
        reset = os.environ.get("RESET_ADMIN_PASSWORD", "").strip().lower() in ("1", "true", "yes")
        if existing is not None and existing.role == "super_admin" and not (reset and password):
            return  # already there — never reset a password the owner may have changed (unless asked to)
    elif User.query.filter_by(role="super_admin").count():
        return
    if app.config["APP_ENV"] == "production" and (not email or len(password) < 10):
        raise SystemExit("Set ADMIN_EMAIL and ADMIN_PASSWORD (10+ characters) to create the first administrator.")
    email, password = email or DEV_ADMIN_EMAIL, password or DEV_ADMIN_PASSWORD
    user = User.query.filter_by(email=email).first() or User(email=email, name="Administrator", email_verified=True)
    user.role = "super_admin"
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    production = app.config["APP_ENV"] == "production"
    site = app.config.get("PUBLIC_SITE_URL") or "http://127.0.0.1:5000"
    print("\n  ┌───────────────────────────────────────────────────────────")
    print("  │ Administrator account created")
    print(f"  │   Sign in at: {site}/account/staff/sign-in")
    print(f"  │   Email:      {email}")
    print(f"  │   Password:   {'(the ADMIN_PASSWORD you set)' if production else password}")
    print("  │ Change the password after signing in (Profile).")
    print("  └───────────────────────────────────────────────────────────\n")


def start_public_link(port: int) -> str | None:
    """Open a free public HTTPS address for this computer (Cloudflare quick tunnel), so anyone can open the site
    from their phone, and PayFast can confirm payments. Turn off with PUBLIC_LINK=false in .env.
    Not needed once the site is deployed (PUBLIC_SITE_URL set)."""
    cfg = app.config
    if cfg.get("PUBLIC_SITE_URL"):
        return None
    flag = os.environ.get("PUBLIC_LINK", os.environ.get("PAYFAST_TUNNEL", "true")).lower()
    if flag == "false":
        print("  Public link: off (PUBLIC_LINK=false) — the site is only on this computer.")
        return None
    from seahorse.tunnel import start_tunnel
    print("  Opening a public link so others can view the site (takes a few seconds)…")
    url = start_tunnel(port, log=lambda m: print("  " + m))
    if not url:
        print("  Public link: couldn't start — the site still works on this computer. "
              "For a permanent public address, deploy it (README → Going live).")
        return None
    os.environ["TUNNEL_URL"] = url
    cfg["TUNNEL_URL"] = url
    return url


def print_share_box(url: str) -> None:
    print("\n  ╔══════════════════════════════════════════════════════════════════╗")
    print("  ║  PUBLIC LINK — anyone can open this while this computer is on    ║")
    print(f"  ║  Website:        {url:<48}║")
    print(f"  ║  Business card:  {(url + '/card'):<48}║")
    print("  ╚══════════════════════════════════════════════════════════════════╝")
    print("  Scan to open the business card on a phone:\n")
    try:
        import qrcode
        qr = qrcode.QRCode(border=1)
        qr.add_data(url + "/card")
        qr.print_ascii(invert=True)
    except Exception:  # noqa: BLE001 — some consoles can't draw the blocks; the link above still works
        print("  (open the link above to see the QR code)")
    print("  The link changes each time you restart. For a permanent link, deploy the site (README → Going live).\n")


if __name__ == "__main__":
    prepare_database(demo_prices=os.environ.get("SEED_DEMO_PRICES", "true").lower() != "false")
    port = int(os.environ.get("PORT", "5000"))
    from seahorse.photos import fetch_in_background
    fetch_in_background(os.path.join(app.static_folder, "img", "photos"), app.logger)
    from seahorse.horse_photos import fetch_in_background as fetch_horses
    with app.app_context():
        names = [h.name for h in Horse.query.all()]
    fetch_horses(os.path.join(app.static_folder, "img", "horses"), names, app.logger)
    public = start_public_link(port)
    if app.config["PAYMENT_PROVIDER"] == "paystack":
        key = app.config.get("PAYSTACK_SECRET_KEY", "")
        mode = ("TEST mode (Paystack test cards, no real money)" if key.startswith("sk_test_")
                else "LIVE (real money)" if key.startswith("sk_live_") else "NOT SET UP — add PAYSTACK_SECRET_KEY")
    else:
        mode = "SANDBOX (test cards, no real money)" if app.config["PAYFAST_SANDBOX"] else "LIVE (real money)"
    print(f"  Payments: {app.config['PAYMENT_PROVIDER'].upper()} — {mode}")
    from seahorse.notify import provider as email_provider
    print(f"  Emails:   {email_provider(app.config) or 'NOT SET UP — only recorded in Admin → Email outbox (add SMTP_* or BREVO_API_KEY to .env)'}")
    if public:
        print_share_box(public)
    print(f"  Seahorse Ride is running → http://127.0.0.1:{port}   (Ctrl+C to stop)\n")
    # Developer/debug mode is switched OFF whenever the site is reachable from the internet: its interactive
    # debugger must never be exposed publicly.
    debug = os.environ.get("APP_ENV", "development") != "production" and not public
    app.run(host="0.0.0.0", port=port, debug=debug, use_reloader=debug)
