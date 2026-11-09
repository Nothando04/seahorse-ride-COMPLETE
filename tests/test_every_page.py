"""Opens every page of the site (public, customer, staff) and compiles every template, so a broken page
can never slip through again."""
import os

from conftest import booking_form, csrf, first_slot, login
from seahorse.models import Booking, Experience


def test_every_template_compiles(app):
    root = app.template_folder
    for folder, _, files in os.walk(root):
        for f in files:
            if f.endswith(".html"):
                app.jinja_env.get_template(os.path.relpath(os.path.join(folder, f), root).replace(os.sep, "/"))


PUBLIC = ["/", "/experiences", "/packages", "/gallery", "/contact", "/safety", "/privacy", "/terms",
          "/cancellation-policy", "/account/sign-in", "/account/sign-up", "/account/staff/sign-in",
          "/account/forgot-password", "/card", "/card/qr.png", "/card/qr.png?for=card", "/card/contact.vcf", "/robots.txt", "/sitemap.xml", "/api/slots?days=3", "/favicon.ico"]
ADMIN = ["/admin/", "/admin/requests", "/admin/availability", "/admin/horses", "/admin/packages", "/admin/packages/new",
         "/admin/payments", "/admin/messages", "/admin/enquiries", "/admin/outbox", "/admin/reviews", "/admin/gallery",
         "/admin/waivers", "/admin/operations", "/admin/check-in", "/admin/reports", "/admin/users", "/admin/settings",
         "/admin/audit", "/admin/reports/export/bookings.csv", "/admin/reports/export/payments.csv"]


def test_every_page_opens(app):
    c = app.test_client()
    with app.app_context():
        slugs = [e.slug for e in Experience.query.all()]
    for url in PUBLIC + [f"/experiences/{s}" for s in slugs]:
        assert c.get(url).status_code == 200, url

    customer = app.test_client()
    login(customer, "thandi@example.com", staff=False)
    for url in ["/book", "/plan-your-event", "/account/bookings", "/account/profile"]:
        assert customer.get(url).status_code == 200, url
    r = customer.post("/book", data=booking_form(csrf(customer, "/book"), first_slot(app)))
    ref = r.location.rsplit("/", 1)[-1]
    for url in [f"/booking/submitted/{ref}", f"/booking/{ref}", f"/booking/{ref}/pay"]:
        assert customer.get(url).status_code == 200, url

    staff = app.test_client()
    login(staff, "super@test.io")
    with app.app_context():
        bid = Booking.query.filter_by(reference=ref).one().id
        exp_id = Experience.query.first().id
    for url in ADMIN + [f"/admin/requests/{bid}", f"/admin/packages/{exp_id}", "/admin/horses?edit=1"]:
        assert staff.get(url).status_code == 200, url


def test_business_card_points_at_live_address(app):
    c = app.test_client()
    html = c.get("/card").get_data(as_text=True)
    assert "points to this computer only" in html  # running locally: warn that others can't scan it
    app.config["TUNNEL_URL"] = "https://sea-horse-demo.trycloudflare.com"
    html = c.get("/card").get_data(as_text=True)
    assert "sea-horse-demo.trycloudflare.com" in html and "points to this computer only" not in html
    assert 'href="https://sea-horse-demo.trycloudflare.com/book"' in html
    app.config["PUBLIC_SITE_URL"] = "https://seahorse-ride.onrender.com"   # deployed address wins
    assert "seahorse-ride.onrender.com" in c.get("/card").get_data(as_text=True)
    png = c.get("/card/qr.png")
    assert png.status_code == 200 and png.data[:4] == b"\x89PNG"
    from PIL import Image
    import io
    assert Image.open(io.BytesIO(png.data)).size == (900, 900)
    vcf = c.get("/card/contact.vcf").get_data(as_text=True)
    assert vcf.startswith("BEGIN:VCARD") and "URL:https://seahorse-ride.onrender.com/" in vcf
