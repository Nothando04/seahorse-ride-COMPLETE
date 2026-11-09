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

DEV_GUIDE_EMAIL = "guide@seahorse.test"
DEV_GUIDE_PASSWORD = "Password123"

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
        ensure_guide()


def sync_demo_passwords() -> None:
    """Keeps the demo logins matching the user guide on every start, even on a permanent database:
    admin@seahorse.test uses ADMIN_PASSWORD; the other demo accounts (…@seahorse.test) use DEMO_PASSWORD."""
    from seahorse.demo_data import DEMO_STAFF
    admin_pw = os.environ.get("ADMIN_PASSWORD") or ""
    demo_pw = os.environ.get("DEMO_PASSWORD") or ""
    changed = 0
    for email, _name, role in DEMO_STAFF:
        password = admin_pw if role == "super_admin" else demo_pw
        if not password:
            continue
        user = User.query.filter_by(email=email).first()
        if user is not None and not user.check_password(password):
            user.set_password(password)
            changed += 1
    if changed:
        db.session.commit()
        print(f"Demo account passwords updated ({changed}).")

def ensure_guide() -> None:
    """Creates the guide login if it doesn't exist yet."""
    if User.query.filter_by(email=DEV_GUIDE_EMAIL).first():
        return
    guide = User(email=DEV_GUIDE_EMAIL, name="Gugu Guide", role="guide", email_verified=True)
    guide.set_password(DEV_GUIDE_PASSWORD)
    db.session.add(guide)
    db.session.commit()
    print(f"Guide account created: {DEV_GUIDE_EMAIL} / {DEV_GUIDE_PASSWORD}")

def ensure_admin() -> None:
    """Admins must sign in like everyone else; this makes sure there is always one admin account."""
    email = (os.environ.get("ADMIN_EMAIL") or "").strip().lower()
    password = os.environ.get("ADMIN_PASSWORD") or ""
    if email:
        existing = User.query.filter_by(email=email).first()
        if existing is not None and existing.role == "super_admin":
            return  # already there — never reset a password the owner may have changed
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
    if public:
        print_share_box(public)
    print(f"  Seahorse Ride is running → http://127.0.0.1:{port}   (Ctrl+C to stop)\n")
    # Developer/debug mode is switched OFF whenever the site is reachable from the internet: its interactive
    # debugger must never be exposed publicly.
    debug = os.environ.get("APP_ENV", "development") != "production" and not public
    app.run(host="0.0.0.0", port=port, debug=debug, use_reloader=debug)
