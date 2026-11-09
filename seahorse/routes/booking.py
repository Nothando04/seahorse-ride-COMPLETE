"""Booking requests, event enquiries and the customer's booking page."""
from __future__ import annotations

import json
import secrets

from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for

from ..extensions import db
from ..forms import AGE_GROUPS, AGE_MIN, CONTACT_METHODS, EVENT_TYPES, EXPERIENCE_LEVELS, Form
from ..models import HORSE_TYPES, AddOn, Booking, Experience, Message, Review, Slot, StatusHistory, User, Waiver, WaiverSignature, utcnow
from .. import notify
from ..horses import type_summary
from ..settings import get_setting
from ..pricing import estimate_cents
from ..security import (IMAGE_EXT, can_view_booking, login_required, rate_limited, read_token, save_upload)
from ..workflow import (CANCELLED, CHANGES, COMPLETED, CONFIRMED, NEED_INFO, PENDING, RESCHED_REQ, RESCHEDULED,
                        WorkflowError, new_reference, payable_reason, remaining_capacity, transition)

bp = Blueprint("booking", __name__)


def _active_experiences():
    return Experience.query.filter_by(is_active=True).order_by(Experience.sort_order).all()


def _nonce() -> str:
    n = secrets.token_urlsafe(16)
    session.setdefault("nonces", {})
    return n


def _already_submitted(nonce: str):
    ref = session.get("nonces", {}).get(nonce)
    return Booking.query.filter_by(reference=ref).first() if ref else None


def _remember_nonce(nonce: str, ref: str) -> None:
    nonces = dict(session.get("nonces", {}))
    nonces[nonce] = ref
    session["nonces"] = dict(list(nonces.items())[-10:])


def _parse_people(form: Form, require_riders=True):
    adults = form.integer("adults", "Adults", 0, 200, default=0)
    children = form.integer("children", "Children", 0, 200, default=0)
    riders = form.integer("riders", "Riders", 0 if not require_riders else 1, 200)
    spectators = form.integer("spectators", "Non-riding guests", 0, 200, default=0)
    if adults is not None and children is not None and adults + children < 1:
        form.errors["adults"] = "Add at least one adult or child."
    if riders is not None and adults is not None and children is not None and riders > adults + children:
        form.errors["riders"] = "Riders can't be more than the number of adults and children."
    if children and not adults:
        form.errors["adults"] = "Children must be accompanied by at least one adult."
    rider_details = []
    for i in range(min(riders or 0, 30)):
        age = request.form.get(f"rider_age_{i}", "")
        exp = request.form.get(f"rider_exp_{i}", "")
        if age not in AGE_MIN or exp not in dict(EXPERIENCE_LEVELS):
            form.errors["riders_detail"] = "Choose an age group and experience level for every rider."
            break
        rider_details.append({"age_group": age, "experience": exp})
    return adults or 0, children or 0, riders or 0, spectators or 0, rider_details


def _parse_addons() -> list[AddOn]:
    ids = [int(x) for x in request.form.getlist("addons") if x.isdigit()]
    return AddOn.query.filter(AddOn.id.in_(ids), AddOn.is_active.is_(True)).all() if ids else []


def ride_locations() -> list[str]:
    return [ln.strip() for ln in get_setting("ride_locations").splitlines() if ln.strip()]


def _parse_place_and_horses(form: Form, booking: Booking) -> None:
    """Where the customer would like to ride (a suggestion or their own idea) and their horse-type preference."""
    choice = (request.form.get("location_choice") or "").strip()
    other = form.text("location_other", "Your suggested location", required=False, max_len=200)
    if choice == "__other__":
        if len(other) < 3:
            form.errors["location_other"] = "Tell us where you'd like to ride."
        booking.location = f"Customer suggestion: {other}" if other else None
    elif choice:
        if choice not in ride_locations():
            form.errors["location_choice"] = "Choose one of the suggested locations or suggest your own."
        booking.location = choice
    else:
        form.errors["location_choice"] = "Choose where you'd like to ride, or suggest a place."
    pref = (request.form.get("horse_preference") or "").strip()
    if pref and pref not in HORSE_TYPES:
        form.errors["horse_preference"] = "Choose a valid horse type."
    booking.horse_preference = pref or None


def _parse_contact(form: Form, booking: Booking) -> None:
    booking.contact_name = form.text("contact_name", "Full name", min_len=2, max_len=120)
    booking.contact_email = g.user.email  # bookings always belong to the signed-in account
    booking.contact_phone = form.phone("contact_phone", "Mobile number")
    booking.preferred_contact = form.choice("preferred_contact", "Preferred contact method", CONTACT_METHODS)
    booking.accessibility = form.text("accessibility", "Accessibility requirements", required=False, max_len=1000) or None
    booking.special_requests = form.text("special_requests", "Special requests", required=False, max_len=2000) or None
    if not form.checked("accept_terms"):
        form.errors["accept_terms"] = "Please accept the booking terms and cancellation policy."
    booking.terms_accepted_at = utcnow()


def _finish_submission(booking: Booking, nonce: str):
    booking.reference = new_reference()
    booking.user_id = g.user.id
    db.session.add(booking)
    db.session.flush()
    db.session.add(StatusHistory(booking=booking, from_status=None, to_status=PENDING,
                                 changed_by_id=g.user.id if g.user else None,
                                 actor_label=f"{booking.contact_name} (customer)", note="Request submitted online."))
    db.session.add(Message(booking=booking, sender="system", read_by_customer=True, read_by_admin=True,
                           body="Request received. Nothing has been charged. Our team is reviewing availability, "
                                "horses, guides and safety before confirming a final price."))
    notify.request_received(booking)
    db.session.commit()
    _remember_nonce(nonce, booking.reference)
    return redirect(url_for("booking.submitted", ref=booking.reference))


# --------------------------------------------------------------------------- booking request

@bp.route("/book", methods=["GET", "POST"])
@login_required
def book():
    experiences = _active_experiences()
    addons = AddOn.query.filter_by(is_active=True).all()
    form = Form(request.form)
    if request.method == "POST":
        nonce = request.form.get("nonce", "")
        if (existing := _already_submitted(nonce)):
            flash("This request was already submitted — here it is.", "info")
            return redirect(url_for("booking.view", ref=existing.reference))
        if rate_limited("book", 8, 3600):
            abort(429)
        booking = Booking(kind="booking")
        exp_id = form.integer("experience_id", "Experience", 1, 10**9)
        exp = db.session.get(Experience, exp_id) if exp_id else None
        if exp is None or not exp.is_active:
            form.errors["experience_id"] = "Choose an experience or package."
        booking.experience = exp
        slot_id = form.integer("slot_id", "Session", 1, 10**9, required=False)
        slot = db.session.get(Slot, slot_id) if slot_id else None
        booking.preferred_date = slot.date if slot else form.date("preferred_date", "Preferred date", future=True)
        booking.preferred_time = slot.start_time if slot else form.time("preferred_time", "Preferred time")
        booking.alternative_date = form.date("alternative_date", "Alternative date", required=False, future=True)
        adults, children, riders, spectators, rider_details = _parse_people(form)
        booking.adults, booking.children, booking.riders, booking.spectators = adults, children, riders, spectators
        booking.rider_details = json.dumps(rider_details)
        addons_chosen = _parse_addons()
        booking.addon_ids = json.dumps([a.id for a in addons_chosen])
        _parse_place_and_horses(form, booking)
        _parse_contact(form, booking)

        if exp and riders:
            if exp.max_group and riders > exp.max_group:
                form.errors["riders"] = (f"{exp.name} is for up to {exp.max_group} riders. Choose a group package or "
                                         "send an event enquiry.")
            if exp.min_group and booking.group_size < exp.min_group:
                form.errors["adults"] = f"{exp.name} needs at least {exp.min_group} people."
            if exp.min_age and any(AGE_MIN[r["age_group"]] < exp.min_age for r in rider_details):
                form.errors["riders_detail"] = (f"Riders on {exp.name} must be at least {exp.min_age}. Choose a family "
                                                "package or tell us more in an event enquiry.")
        if slot is not None:
            if slot.date <= utcnow().date():
                form.errors["slot_id"] = "That session is no longer available."
            elif slot.status != "open":
                form.errors["slot_id"] = "That session is closed. Please choose another time."
            elif remaining_capacity(slot) < riders:
                form.errors["slot_id"] = (f"Only {remaining_capacity(slot)} place(s) left in that session. Choose another "
                                          "time, or send a custom enquiry.")
            else:
                booking.slot = None  # the session is only allocated when an admin approves
                booking.preferred_time = slot.start_time
        if form.ok:
            booking.estimate_cents = estimate_cents(exp, riders, addons_chosen)
            return _finish_submission(booking, nonce)
        db.session.rollback()
        flash("Please check the highlighted fields.", "error")
    preselect = request.args.get("experience", type=int)
    return render_template("public/book.html", experiences=experiences, addons=addons, form=form,
                           nonce=request.form.get("nonce") or _nonce(), preselect=preselect,
                           age_groups=AGE_GROUPS, levels=EXPERIENCE_LEVELS, contact_methods=CONTACT_METHODS,
                           locations=ride_locations(), horse_types=type_summary(),
                           exp_json=json.dumps([{"id": e.id, "name": e.name, "price": e.price_cents,
                                                 "basis": e.price_basis, "min": e.min_group, "max": e.max_group,
                                                 "min_age": e.min_age} for e in experiences]),
                           addon_json=json.dumps([{"id": a.id, "price": a.price_cents, "basis": a.price_basis}
                                                  for a in addons]))


@bp.route("/plan-your-event", methods=["GET", "POST"])
@login_required
def plan_event():
    experiences = _active_experiences()
    addons = AddOn.query.filter_by(is_active=True).all()
    form = Form(request.form)
    if request.method == "POST":
        nonce = request.form.get("nonce", "")
        if (existing := _already_submitted(nonce)):
            return redirect(url_for("booking.view", ref=existing.reference))
        if rate_limited("book", 8, 3600):
            abort(429)
        booking = Booking(kind="event")
        booking.event_type = form.choice("event_type", "Event type", EVENT_TYPES)
        exp_id = form.integer("experience_id", "Preferred package", 1, 10**9, required=False)
        booking.experience = db.session.get(Experience, exp_id) if exp_id else None
        booking.preferred_date = form.date("preferred_date", "Preferred date", future=True)
        booking.alternative_date = form.date("alternative_date", "Alternative date", required=False, future=True)
        booking.preferred_time = form.time("preferred_time", "Preferred time")
        adults, children, riders, spectators, rider_details = _parse_people(form, require_riders=False)
        booking.adults, booking.children, booking.riders, booking.spectators = adults, children, riders, spectators
        booking.rider_details = json.dumps(rider_details)
        booking.budget = form.text("budget", "Budget", required=False, max_len=60) or None
        booking.duration_wish = form.text("duration_wish", "Duration", required=False, max_len=60) or None
        booking.activities = form.text("activities", "Activities", required=False, max_len=2000) or None
        addons_chosen = _parse_addons()
        booking.addon_ids = json.dumps([a.id for a in addons_chosen])
        _parse_place_and_horses(form, booking)
        _parse_contact(form, booking)
        if booking.event_type == "School or youth group" and not booking.special_requests:
            form.errors["special_requests"] = ("For school or youth groups, tell us the supervising adults and "
                                               "safeguarding arrangements.")
        try:
            booking.attachment_path = save_upload(request.files.get("attachment"), "private/events", IMAGE_EXT | {"pdf"})
        except ValueError as exc:
            form.errors["attachment"] = str(exc)
        if form.ok:
            booking.estimate_cents = estimate_cents(booking.experience, riders, addons_chosen) if booking.experience else None
            return _finish_submission(booking, nonce)
        db.session.rollback()
        flash("Please check the highlighted fields.", "error")
    return render_template("public/plan_event.html", experiences=experiences, addons=addons, form=form,
                           nonce=request.form.get("nonce") or _nonce(), event_types=EVENT_TYPES,
                           age_groups=AGE_GROUPS, levels=EXPERIENCE_LEVELS, contact_methods=CONTACT_METHODS,
                           locations=ride_locations(), horse_types=type_summary())


@bp.route("/booking/submitted/<ref>")
@login_required
def submitted(ref: str):
    booking = Booking.query.filter_by(reference=ref).first_or_404()
    if not can_view_booking(booking):
        abort(404)
    return render_template("public/submitted.html", b=booking)


# --------------------------------------------------------------------------- secure access & lookup

@bp.route("/booking/access/<token>")
def access(token: str):
    """Links in emails. The customer must still sign in to the account that owns the booking."""
    data = read_token("booking-access", token, max_age=60 * 60 * 24 * 90)
    booking = db.session.get(Booking, data.get("b")) if isinstance(data, dict) else None
    if booking is None or booking.access_key[:12] != data.get("k"):
        flash("That link has expired or is invalid. Sign in to see your bookings.", "error")
        return redirect(url_for("account.sign_in"))
    if g.user is None:
        flash("Please sign in to open your booking.", "info")
        return redirect(url_for("account.sign_in", next=url_for("booking.view", ref=booking.reference)))
    return redirect(url_for("booking.view", ref=booking.reference))


@bp.route("/find-my-booking")
def lookup():
    return redirect(url_for("account.my_bookings") if g.user else url_for("account.sign_in"))


# --------------------------------------------------------------------------- customer booking page

def _get_viewable(ref: str) -> Booking:
    booking = Booking.query.filter_by(reference=ref.upper()).first()
    if booking is None or not can_view_booking(booking):
        abort(404)
    return booking


@bp.route("/booking/<ref>")
@login_required
def view(ref: str):
    b = _get_viewable(ref)
    if not (g.user and g.user.is_staff):
        for m in b.messages:
            if m.sender == "admin" and not m.read_by_customer:
                m.read_by_customer = True
        db.session.commit()
    waiver = Waiver.query.filter_by(is_active=True).order_by(Waiver.version.desc()).first()
    return render_template("account/booking.html", b=b, pay_block=payable_reason(b), waiver=waiver,
                           messages=[m for m in b.messages if not m.internal], age_groups=dict(
                               (k, label) for k, label, _ in AGE_GROUPS), levels=dict(EXPERIENCE_LEVELS))


@bp.route("/booking/<ref>/message", methods=["POST"])
@login_required
def post_message(ref: str):
    b = _get_viewable(ref)
    if rate_limited("msg", 20, 600):
        abort(429)
    body = (request.form.get("body") or "").strip()
    if not 1 <= len(body) <= 3000:
        flash("Write a message (up to 3000 characters).", "error")
        return redirect(url_for("booking.view", ref=b.reference) + "#messages")
    db.session.add(Message(booking=b, sender="customer", user_id=g.user.id if g.user else None, body=body,
                           read_by_customer=True))
    if b.status == NEED_INFO:
        transition(b, PENDING, g.user, note="Customer replied with more information.",
                   actor_label=f"{b.contact_name} (customer)")
    notify.new_message(b, True, body)
    db.session.commit()
    flash("Message sent.", "success")
    return redirect(url_for("booking.view", ref=b.reference) + "#messages")


@bp.route("/booking/<ref>/respond", methods=["POST"])
@login_required
def respond(ref: str):
    """Customer actions: accept proposed changes, request reschedule, cancel."""
    b = _get_viewable(ref)
    action = request.form.get("action")
    note = (request.form.get("note") or "").strip()[:1000]
    who = f"{b.contact_name} (customer)"
    try:
        if action == "accept_changes" and b.status == CHANGES:
            transition(b, PENDING, g.user, note="Customer accepted the proposed changes.", actor_label=who)
            db.session.add(Message(booking=b, sender="customer", body="I accept the proposed changes. " + note,
                                   read_by_customer=True))
            notify.queue_staff(f"{b.reference}: customer accepted proposed changes", note or "No extra note.", b)
            flash("Thanks — our team will finalise the approval.", "success")
        elif action == "reschedule" and b.status in {CONFIRMED, RESCHEDULED}:
            if len(note) < 5:
                raise WorkflowError("Tell us which dates would work for you.")
            transition(b, RESCHED_REQ, g.user, note=note, actor_label=who)
            db.session.add(Message(booking=b, sender="customer", body="Rescheduling request: " + note, read_by_customer=True))
            notify.queue_staff(f"{b.reference}: rescheduling requested", note, b)
            flash("Rescheduling request sent.", "success")
        elif action == "cancel":
            if b.amount_paid_cents == 0 and b.status in {PENDING, NEED_INFO, CHANGES, "Approved — Awaiting Payment",
                                                          "Payment Expired", "Draft"}:
                transition(b, CANCELLED, g.user, note=note or "Cancelled by customer before payment.", actor_label=who)
                notify.status_update(b, "Your booking request has been cancelled")
                notify.queue_staff(f"{b.reference} cancelled by customer", note or "No reason given.", b)
                flash("Your request has been cancelled. Nothing was charged.", "success")
            else:
                # Paid bookings: cancellations and refunds follow the policy, so staff handle them.
                db.session.add(Message(booking=b, sender="customer", read_by_customer=True,
                                       body="Cancellation request: " + (note or "(no reason given)")))
                notify.queue_staff(f"{b.reference}: cancellation requested (paid booking)", note or "No reason given.", b)
                flash("Cancellation request sent. Our team will reply about refunds under the cancellation policy.", "success")
        else:
            raise WorkflowError("That action isn't available for this booking right now.")
        db.session.commit()
    except WorkflowError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("booking.view", ref=b.reference))


@bp.route("/booking/<ref>/waiver", methods=["POST"])
@login_required
def sign_waiver(ref: str):
    b = _get_viewable(ref)
    waiver = Waiver.query.filter_by(is_active=True).order_by(Waiver.version.desc()).first()
    if waiver is None or b.status not in {CONFIRMED, RESCHEDULED, RESCHED_REQ, "Approved — Awaiting Payment"}:
        flash("The waiver can be completed once your booking is approved.", "error")
        return redirect(url_for("booking.view", ref=b.reference))
    form = Form(request.form)
    rider = form.text("rider_name", "Rider name", min_len=2, max_len=120)
    minor = form.checked("rider_is_minor")
    signer = form.text("signer_name", "Signed by", min_len=2, max_len=120)
    em_name = form.text("emergency_name", "Emergency contact name", min_len=2, max_len=120)
    em_phone = form.phone("emergency_phone", "Emergency contact phone")
    medical = form.text("medical_notes", "Medical notes", required=False, max_len=500)
    if not form.checked("agree"):
        form.errors["agree"] = "You must read and accept the waiver."
    if minor and signer.lower() == rider.lower():
        form.errors["signer_name"] = "A parent or legal guardian must sign for riders under 18."
    if len(b.signatures) >= max(b.riders, 1):
        form.errors["rider_name"] = "All riders on this booking have already signed."
    if not form.ok:
        flash(" ".join(form.errors.values()), "error")
        return redirect(url_for("booking.view", ref=b.reference) + "#waiver")
    db.session.add(WaiverSignature(booking=b, waiver=waiver, rider_name=rider, rider_is_minor=minor, signer_name=signer,
                                   emergency_name=em_name, emergency_phone=em_phone, medical_notes=medical or None,
                                   ip_address=request.remote_addr))
    notify.queue(b.contact_email, f"Waiver signed for {rider} — {b.reference}",
                 f"This confirms {signer} accepted '{waiver.title}' (version {waiver.version}) for rider {rider} "
                 f"on booking {b.reference} at {utcnow():%d %b %Y %H:%M} UTC.\n\nWaiver text:\n\n{waiver.body}", b)
    db.session.commit()
    flash(f"Waiver signed for {rider}. A copy has been emailed to you.", "success")
    return redirect(url_for("booking.view", ref=b.reference) + "#waiver")


@bp.route("/booking/<ref>/review", methods=["POST"])
@login_required
def review(ref: str):
    b = _get_viewable(ref)
    if b.status != COMPLETED or b.review is not None:
        flash("Reviews can be left once after a completed ride.", "error")
        return redirect(url_for("booking.view", ref=b.reference))
    form = Form(request.form)
    rating = form.integer("rating", "Rating", 1, 5)
    comment = form.text("comment", "Comment", min_len=10, max_len=1500)
    name = form.text("display_name", "Display name", min_len=2, max_len=60)
    if form.ok:
        db.session.add(Review(booking=b, rating=rating, comment=comment, display_name=name))
        db.session.commit()
        flash("Thank you! Your review will appear once our team has checked it.", "success")
    else:
        flash(" ".join(form.errors.values()), "error")
    return redirect(url_for("booking.view", ref=b.reference))


@bp.route("/booking/<ref>/receipt")
@login_required
def receipt(ref: str):
    b = _get_viewable(ref)
    if b.amount_paid_cents <= 0:
        abort(404)
    return render_template("account/receipt.html", b=b)


@bp.route("/booking/<ref>/calendar.ics")
@login_required
def calendar(ref: str):
    b = _get_viewable(ref)
    if b.status not in {CONFIRMED, RESCHEDULED} or not b.slot:
        abort(404)
    start = f"{b.slot.date:%Y%m%d}T{b.slot.start_time.replace(':', '')}00"
    end = f"{b.slot.date:%Y%m%d}T{b.slot.end_time.replace(':', '')}00"
    ics = "\r\n".join(["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Seahorse Ride//EN", "BEGIN:VEVENT",
                       f"UID:{b.reference}@seahorseride", f"DTSTAMP:{utcnow():%Y%m%dT%H%M%SZ}",
                       f"DTSTART;TZID=Africa/Johannesburg:{start}", f"DTEND;TZID=Africa/Johannesburg:{end}",
                       f"SUMMARY:Seahorse Ride — {b.display_name}", f"DESCRIPTION:Booking {b.reference}",
                       "END:VEVENT", "END:VCALENDAR"])
    return ics, 200, {"Content-Type": "text/calendar", "Content-Disposition": f"attachment; filename={b.reference}.ics"}
