"""Public website pages."""
from __future__ import annotations

import os
from datetime import timedelta

from flask import (Blueprint, Response, abort, current_app, flash, g, jsonify, make_response, redirect,
                   render_template, request, send_from_directory, url_for)

from ..extensions import db
from ..forms import Form
from ..models import AddOn, Enquiry, Experience, GalleryImage, Horse, Review, Slot, today
from .. import notify
from ..security import rate_limited
from ..workflow import remaining_capacity

bp = Blueprint("public", __name__)

FAQS = [
    ("Do I pay when I submit a booking request?",
     "No. Submitting a request is free and doesn't reserve a place. Our team checks availability, horses, guides and "
     "conditions first. Once approved, you receive the final price and a secure payment link."),
    ("I've never ridden before — can I still come?",
     "Most of our experiences are suitable for first-time riders. Tell us each rider's experience on the booking form "
     "and we'll match horses and guides accordingly."),
    ("Are there age, height or weight limits?",
     "Yes. Limits depend on the horses available and our safety policy, and are confirmed for each booking during review. "
     "Please note them honestly on the form so we can plan safely."),
    ("What happens if the weather is bad?",
     "Our guides assess conditions on the day. If a ride can't go ahead safely we'll contact you to reschedule — "
     "see our cancellation policy for details."),
    ("How do I pay?",
     "After approval you pay online by card or Instant EFT through PayFast, a South African payment provider. "
     "We never see or store your card details."),
    ("Can I change or cancel my booking?",
     "Yes — request a change or cancellation from your booking page and our team will reply. Refunds follow the "
     "published cancellation policy."),
]


def public_experiences(kind: str | None = None):
    q = Experience.query.filter_by(is_active=True)
    if kind:
        q = q.filter_by(kind=kind)
    return q.order_by(Experience.sort_order, Experience.name).all()


@bp.route("/")
def home():
    featured = Experience.query.filter_by(is_active=True, is_featured=True).order_by(Experience.sort_order).limit(3).all()
    packages = public_experiences("package")[:4]
    gallery = (GalleryImage.query.filter_by(is_published=True, consent_confirmed=True)
               .order_by(GalleryImage.id.desc()).limit(6).all())
    reviews = Review.query.filter_by(status="approved").order_by(Review.id.desc()).limit(3).all()
    return render_template("public/home.html", featured=featured, packages=packages, gallery=gallery,
                           reviews=reviews, faqs=FAQS)


@bp.route("/experiences")
def experiences():
    horses = Horse.query.filter(Horse.show_publicly.is_(True), Horse.status != "retired").all()
    return render_template("public/experiences.html", rides=public_experiences("ride"),
                           packages=public_experiences("package"), horses=horses)


@bp.route("/packages")
def packages():
    return render_template("public/packages.html", packages=public_experiences("package"),
                           addons=AddOn.query.filter_by(is_active=True).all())


@bp.route("/experiences/<slug>")
def experience_detail(slug: str):
    exp = Experience.query.filter_by(slug=slug, is_active=True).first_or_404()
    return render_template("public/experience_detail.html", exp=exp,
                           addons=AddOn.query.filter_by(is_active=True).all())


@bp.route("/gallery")
def gallery():
    from ..models import HORSE_TYPES
    images = (GalleryImage.query.filter_by(is_published=True, consent_confirmed=True)
              .order_by(GalleryImage.id.desc()).all())
    order = {k: i for i, k in enumerate(HORSE_TYPES)}
    horses = sorted(Horse.query.filter(Horse.show_publicly.is_(True), Horse.status != "retired").all(),
                    key=lambda h: (order.get(h.category, 99), h.name))
    return render_template("public/gallery.html", images=images, horses=horses, types=HORSE_TYPES)


@bp.route("/contact", methods=["GET", "POST"])
def contact():
    form = Form(request.form)
    if request.method == "POST":
        if request.form.get("website"):  # honeypot field, hidden from people
            return redirect(url_for("public.contact"))
        if rate_limited("contact", 5, 600):
            abort(429)
        name = form.text("name", "Name", min_len=2, max_len=120)
        email = form.email()
        phone = form.phone(required=False)
        subject = form.text("subject", "Subject", max_len=150)
        message = form.text("message", "Message", min_len=10, max_len=4000)
        if form.ok:
            enq = Enquiry(name=name, email=email, phone=phone, subject=subject, message=message)
            db.session.add(enq)
            notify.queue(email, "We've received your message", f"Hi {name},\n\nThanks for contacting Seahorse Ride. "
                         f"We'll reply as soon as we can.\n\nYour message:\n{message}")
            notify.queue_staff(f"New enquiry: {subject}", f"From {name} <{email}> {phone}\n\n{message}\n\n"
                               f"Reply: {url_for('admin.enquiries', _external=True)}")
            db.session.commit()
            flash("Thanks — your message has been sent. We'll reply by email.", "success")
            return redirect(url_for("public.contact"))
    return render_template("public/contact.html", form=form, faqs=FAQS)


@bp.route("/safety")
def safety():
    return render_template("public/legal.html", page="safety")


@bp.route("/privacy")
def privacy():
    return render_template("public/legal.html", page="privacy")


@bp.route("/terms")
def terms():
    return render_template("public/legal.html", page="terms")


@bp.route("/cancellation-policy")
def cancellation():
    return render_template("public/legal.html", page="cancellation")


@bp.route("/cookies/accept", methods=["POST"])
def accept_cookies():
    resp = make_response(redirect(request.referrer or url_for("public.home")))
    resp.set_cookie("cookie_notice", "1", max_age=60 * 60 * 24 * 365, samesite="Lax", httponly=True)
    return resp


# --------------------------------------------------------------------------- availability API

@bp.route("/api/slots")
def api_slots():
    """Open sessions with remaining capacity, used by the booking form calendar."""
    try:
        start = today() + timedelta(days=1)
        days = min(int(request.args.get("days", 21)), 60)
        if request.args.get("from"):
            from datetime import date
            start = max(date.fromisoformat(request.args["from"]), start)
    except ValueError:
        return jsonify({"error": "bad request"}), 400
    exp_id = request.args.get("experience", type=int)
    slots = (Slot.query.filter(Slot.date >= start, Slot.date < start + timedelta(days=days))
             .order_by(Slot.date, Slot.start_time).all())
    from ..horses import available_by_type, free_by_type
    base = available_by_type()
    out = []
    for s in slots:
        if s.experience_id and exp_id and s.experience_id != exp_id:
            continue
        out.append({"id": s.id, "date": s.date.isoformat(), "start": s.start_time, "end": s.end_time,
                    "remaining": remaining_capacity(s), "blocked": s.status != "open",
                    "reason": s.block_reason if s.status != "open" else None, "horses": free_by_type(s, base)})
    return jsonify(out)


# --------------------------------------------------------------------------- media & SEO

@bp.route("/media/<path:rel>")
def media(rel: str):
    folder = rel.split("/", 1)[0]
    if folder == "public":
        pass
    elif folder == "gallery":
        img = GalleryImage.query.filter_by(path=rel).first()
        if img is None or not ((img.is_published and img.consent_confirmed) or (g.user and g.user.is_staff)):
            abort(404)
    else:  # private uploads (event briefs) — staff only
        if not (g.user and g.user.is_staff):
            abort(404)
    upload_path = os.path.join(current_app.config["UPLOAD_FOLDER"], rel)
    bundled = os.path.join(current_app.static_folder, "img", "photos", os.path.basename(rel))
    if folder == "gallery" and not os.path.exists(upload_path) and os.path.exists(bundled):
        # Demo database gallery rows point at the photos shipped with the site.
        return send_from_directory(os.path.dirname(bundled), os.path.basename(rel), max_age=86400)
    resp = send_from_directory(current_app.config["UPLOAD_FOLDER"], rel, max_age=86400 if folder != "private" else 0)
    if folder == "private":
        resp.headers["Content-Disposition"] = "attachment"
    return resp


# --------------------------------------------------------------------------- digital business card

@bp.route("/card")
def card():
    from ..card import is_local, public_base, qr_svg
    base = public_base()
    return render_template("public/card.html", base=base, local=is_local(base), qr=qr_svg(base + "/"))


@bp.route("/card/qr.png")
def card_qr_png():
    from ..card import public_base, qr_png
    target = request.args.get("for", "site")
    base = public_base()
    data = base + ("/card" if target == "card" else "/")
    size = max(300, min(request.args.get("size", 900, type=int), 2000))
    return Response(qr_png(data, size), mimetype="image/png", headers={
        "Content-Disposition": f'attachment; filename="seahorse-ride-qr-{target}.png"', "Cache-Control": "no-store"})


@bp.route("/card/contact.vcf")
def card_vcard():
    from ..card import public_base, vcard
    from ..settings import all_settings
    return Response(vcard(all_settings(), public_base() + "/"), mimetype="text/vcard",
                    headers={"Content-Disposition": 'attachment; filename="seahorse-ride.vcf"'})


@bp.route("/robots.txt")
def robots():
    body = f"User-agent: *\nDisallow: /account\nDisallow: /booking/\nSitemap: {url_for('public.sitemap', _external=True)}\n"
    return Response(body, mimetype="text/plain")


@bp.route("/sitemap.xml")
def sitemap():
    urls = [url_for(e, _external=True) for e in ["public.home", "public.card", "public.experiences", "public.packages",
                                                   "booking.book", "booking.plan_event", "public.gallery",
                                                   "public.contact", "public.safety", "public.cancellation"]]
    urls += [url_for("public.experience_detail", slug=e.slug, _external=True) for e in public_experiences()]
    xml = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    xml += [f"<url><loc>{u}</loc></url>" for u in urls]
    xml.append("</urlset>")
    return Response("\n".join(xml), mimetype="application/xml")


@bp.route("/favicon.ico")
def favicon():
    return send_from_directory(os.path.join(current_app.static_folder, "img"), "favicon.ico", mimetype="image/x-icon")
