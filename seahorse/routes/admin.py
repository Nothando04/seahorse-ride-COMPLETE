"""Staff area. Every route is protected server-side by role permissions (see models.PERMISSIONS)."""
from __future__ import annotations

import csv
import io
import json
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

import requests
from flask import Blueprint, Response, abort, current_app, flash, g, redirect, render_template, request, url_for
from sqlalchemy import func, or_

from ..extensions import db
from ..forms import EVENT_TYPES, Form
from ..models import (HORSE_TYPES, ROLE_LABELS, ROLES, AddOn, AuditLog, Booking, Enquiry, Experience, GalleryImage, Horse, Message,
                      Notification, Payment, PaymentEvent, Refund, Review, Slot, User, Waiver, WaiverSignature,
                      today, utcnow)
from .. import notify
from ..security import IMAGE_EXT, audit, save_upload, staff_required
from ..settings import DEFAULTS, OPS_STATUS_LABELS, all_settings, get_setting, int_setting, set_setting
from ..workflow import (ALL_STATUSES, APPROVED, CANCELLED, CHANGES, COMPLETED, CONFIRMED, DECLINED, EXPIRED,
                        HOLDS_CAPACITY, NEED_INFO, NO_SHOW, OPEN_REQUEST, PART_REFUNDED, PENDING, PROCESSING,
                        REFUND_PENDING, REFUNDED, RESCHED_REQ, RESCHEDULED, ApprovalInput, WorkflowError, approve,
                        extend_deadline, record_payment, remaining_capacity, reschedule, riders_booked, transition)

bp = Blueprint("admin", __name__, url_prefix="/admin")


# --------------------------------------------------------------------------- dashboard

@bp.route("/")
@staff_required()
def dashboard():
    t = today()
    week = t + timedelta(days=7)
    todays = (Booking.query.join(Slot).filter(Slot.date == t, Booking.status.in_(HOLDS_CAPACITY))
              .order_by(Slot.start_time).all())
    if g.user.role == "guide":
        todays = [b for b in todays if b.guide_id == g.user.id]
    upcoming_slots = Slot.query.filter(Slot.date >= t, Slot.date <= week, Slot.status == "open").all()
    capacity_alerts = [(s, riders_booked(s)) for s in upcoming_slots if s.capacity and riders_booked(s) >= 0.8 * s.capacity]
    widgets = {
        "new_requests": Booking.query.filter(Booking.status == PENDING,
                                             Booking.created_at >= utcnow() - timedelta(hours=48)).count(),
        "awaiting_approval": Booking.query.filter(Booking.status.in_(OPEN_REQUEST)).count(),
        "awaiting_payment": Booking.query.filter(Booking.status.in_([APPROVED, PROCESSING])).count(),
        "confirmed": Booking.query.filter(Booking.status.in_([CONFIRMED, RESCHEDULED]), Booking.preferred_date >= t).count(),
        "upcoming_events": Booking.query.filter(Booking.kind == "event", Booking.status.in_(HOLDS_CAPACITY),
                                                Booking.preferred_date >= t).count(),
        "failed_payments": Payment.query.filter(Payment.status == "failed",
                                                Payment.created_at >= utcnow() - timedelta(days=14)).count(),
        "cancellations": Booking.query.filter(Booking.status == CANCELLED,
                                              Booking.updated_at >= utcnow() - timedelta(days=14)).count(),
        "rescheduling": Booking.query.filter(Booking.status == RESCHED_REQ).count(),
        "expired": Booking.query.filter(Booking.status == EXPIRED).count(),
        "new_enquiries": Enquiry.query.filter_by(status="new").count(),
        "pending_reviews": Review.query.filter_by(status="pending").count(),
    }
    overdue = [b for b in Booking.query.filter(Booking.status.in_([CONFIRMED, RESCHEDULED]),
                                               Booking.preferred_date <= week).all() if b.balance_cents > 0]
    recent = Booking.query.filter(Booking.status.in_(OPEN_REQUEST)).order_by(Booking.created_at).limit(8).all()
    return render_template("admin/dashboard.html", todays=todays, widgets=widgets, capacity_alerts=capacity_alerts,
                           overdue=overdue, recent=recent)


# --------------------------------------------------------------------------- requests

@bp.route("/requests")
@staff_required("manage_requests")
def requests_list():
    q = Booking.query
    status = request.args.get("status", "")
    if status == "open":
        q = q.filter(Booking.status.in_(OPEN_REQUEST))
    elif status in ALL_STATUSES:
        q = q.filter(Booking.status == status)
    if (kind := request.args.get("kind")) in {"booking", "event"}:
        q = q.filter(Booking.kind == kind)
    if (et := request.args.get("event_type")):
        q = q.filter(Booking.event_type == et)
    if (pkg := request.args.get("package", type=int)):
        q = q.filter(Booking.experience_id == pkg)
    for name, op in (("from", ">="), ("to", "<=")):
        if (raw := request.args.get(name)):
            try:
                d = date.fromisoformat(raw)
                q = q.filter(Booking.preferred_date >= d if op == ">=" else Booking.preferred_date <= d)
            except ValueError:
                pass
    if (text := request.args.get("q", "").strip()):
        like = f"%{text}%"
        q = q.filter(or_(Booking.reference.ilike(like), Booking.contact_name.ilike(like),
                         Booking.contact_email.ilike(like), Booking.contact_phone.ilike(like)))
    bookings = q.order_by(Booking.created_at.desc()).limit(300).all()
    return render_template("admin/requests.html", bookings=bookings, statuses=ALL_STATUSES, event_types=EVENT_TYPES,
                           packages=Experience.query.order_by(Experience.sort_order).all(), args=request.args)


def _booking_or_404(booking_id: int) -> Booking:
    b = db.session.get(Booking, booking_id)
    if b is None:
        abort(404)
    return b


@bp.route("/requests/<int:booking_id>")
@staff_required("manage_requests")
def request_detail(booking_id: int):
    b = _booking_or_404(booking_id)
    for m in b.messages:
        if m.sender == "customer" and not m.read_by_admin:
            m.read_by_admin = True
    db.session.commit()
    start = max(b.preferred_date - timedelta(days=3), today() + timedelta(days=0))
    slots = (Slot.query.filter(Slot.date >= start, Slot.date <= b.preferred_date + timedelta(days=14))
             .order_by(Slot.date, Slot.start_time).all())
    slot_rows = [(s, remaining_capacity(s, exclude_booking_id=b.id)) for s in slots]
    preselect = next((s.id for s in slots if s.date == b.preferred_date and s.start_time == b.preferred_time), None)
    preselect = b.slot_id or preselect
    deadline_default = (utcnow() + timedelta(hours=int_setting("payment_deadline_hours", 48))).strftime("%Y-%m-%dT%H:%M")
    other_on_day = (Booking.query.filter(Booking.preferred_date == b.preferred_date, Booking.id != b.id,
                                         Booking.status.in_(HOLDS_CAPACITY | OPEN_REQUEST)).all())
    from ..horses import available_by_type, free_by_type
    pre_slot = db.session.get(Slot, preselect) if preselect else None
    horse_free = free_by_type(pre_slot) if pre_slot else available_by_type()
    return render_template("admin/request_detail.html", b=b, slot_rows=slot_rows, preselect=preselect,
                           horse_free=horse_free, horse_types_map=HORSE_TYPES,
                           horses=Horse.query.filter(Horse.status != "retired").order_by(Horse.name).all(),
                           guides=User.query.filter(User.role != "customer", User.is_active.is_(True)).order_by(User.name).all(),
                           deadline_default=deadline_default, deposit_percent=int_setting("deposit_percent", 50),
                           other_on_day=other_on_day, addons=AddOn.query.all(),
                           packages=Experience.query.filter_by(is_active=True).all())


@bp.route("/requests/<int:booking_id>/approve", methods=["POST"])
@staff_required("approve")
def request_approve(booking_id: int):
    b = _booking_or_404(booking_id)
    form = Form(request.form)
    slot = db.session.get(Slot, form.integer("slot_id", "Session", 1, 10**9) or 0)
    price = form.money_cents("final_price", "Final price")
    mode = form.choice("payment_mode", "Payment type", ["full", "deposit"])
    deposit = form.money_cents("deposit", "Deposit", required=(mode == "deposit"))
    deadline = form.datetime_local("deadline", "Payment deadline")
    if (pkg := request.form.get("experience_id", type=int)):
        b.experience = db.session.get(Experience, pkg) or b.experience
    if slot is None:
        form.errors["slot_id"] = "Choose the session for this booking."
    if not form.ok:
        flash(" ".join(form.errors.values()), "error")
        return redirect(url_for("admin.request_detail", booking_id=b.id) + "#approve")
    horses = Horse.query.filter(Horse.id.in_([int(x) for x in request.form.getlist("horses") if x.isdigit()])).all()
    guide = db.session.get(User, request.form.get("guide_id", type=int) or 0)
    if guide is not None and not guide.is_staff:
        guide = None
    checks = {c for c in ("date_time", "group_size", "price", "availability") if request.form.get(f"check_{c}")}
    try:
        approve(b, g.user, ApprovalInput(slot=slot, final_price_cents=price, payment_mode=mode, deposit_cents=deposit,
                                         deadline=deadline, note=(request.form.get("note") or "").strip()[:2000],
                                         horses=horses, guide=guide, confirmed_checks=checks))
        db.session.add(Message(booking=b, sender="system", read_by_admin=True,
                               body=f"Approved: {b.slot.label}, final price {notify.money(b.final_price_cents)}. "
                                    f"Pay {notify.money(b.amount_due_now_cents)} by {b.payment_deadline:%d %b %Y %H:%M} UTC."))
        notify.approved(b)
        audit("booking_approved", "booking", b.reference, f"price={price} slot={slot.id} mode={mode}")
        db.session.commit()
        flash(f"{b.reference} approved. The customer has been sent the price and secure payment link.", "success")
    except WorkflowError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("admin.request_detail", booking_id=booking_id))


@bp.route("/requests/<int:booking_id>/action", methods=["POST"])
@staff_required("manage_requests")
def request_action(booking_id: int):
    b = _booking_or_404(booking_id)
    action = request.form.get("action", "")
    note = (request.form.get("note") or "").strip()[:2000]
    try:
        if action in {"decline", "need_info", "propose_changes", "cancel"} and not g.user.can("approve"):
            abort(403)
        if action == "decline":
            if len(note) < 5:
                raise WorkflowError("Explain to the customer why the request is declined.")
            transition(b, DECLINED, g.user, note=note)
            _system_and_customer_msg(b, f"Request declined: {note}")
            notify.status_update(b, "We're unable to accept your request", note)
        elif action == "need_info":
            if len(note) < 5:
                raise WorkflowError("Write the question for the customer.")
            transition(b, NEED_INFO, g.user, note=note)
            _system_and_customer_msg(b, note)
            notify.status_update(b, "We need a little more information", note)
        elif action == "propose_changes":
            if len(note) < 5:
                raise WorkflowError("Describe the proposed changes (alternative date, package or price).")
            if b.status in {APPROVED} and b.amount_paid_cents:
                raise WorkflowError("This booking already has payments — reschedule it instead.")
            transition(b, CHANGES, g.user, note=note)
            b.approval_note = note
            _system_and_customer_msg(b, "Proposed changes: " + note)
            notify.status_update(b, "We've proposed changes to your request", note)
        elif action == "cancel":
            transition(b, CANCELLED, g.user, note=note or "Cancelled by staff.")
            for p in b.payments:
                if p.status == "pending":
                    p.status = "cancelled"
            if b.amount_paid_cents:
                transition(b, REFUND_PENDING, g.user, note="Payment received — refund to be processed per policy.")
            _system_and_customer_msg(b, "Booking cancelled. " + note)
            notify.status_update(b, "Your booking has been cancelled", note)
        elif action == "complete":
            transition(b, COMPLETED, g.user, note=note or "Ride completed.")
            notify.feedback_request(b)
        elif action == "no_show":
            transition(b, NO_SHOW, g.user, note=note or "Customer did not arrive.")
        elif action == "confirm_resched_declined":
            transition(b, CONFIRMED, g.user, note=note or "Rescheduling request declined; original date stands.")
            _system_and_customer_msg(b, "Your original booking date stands. " + note)
            notify.status_update(b, "Update on your rescheduling request", note)
        elif action == "extend":
            new = datetime.strptime(request.form.get("deadline", ""), "%Y-%m-%dT%H:%M")
            extend_deadline(b, g.user, new)
            notify.status_update(b, "Your payment deadline has been extended",
                                 f"New deadline: {new:%a %d %b %Y %H:%M} UTC. {note}")
        elif action == "reschedule":
            slot = db.session.get(Slot, request.form.get("slot_id", type=int) or 0)
            if slot is None:
                raise WorkflowError("Choose the new session.")
            reschedule(b, g.user, slot, note)
            _system_and_customer_msg(b, f"Booking moved to {slot.label}. {note}")
            notify.status_update(b, "Your booking has been rescheduled", f"New date: {slot.label}. {note}")
        elif action == "assign":
            b.horses = Horse.query.filter(Horse.id.in_([int(x) for x in request.form.getlist("horses") if x.isdigit()])).all()
            gid = request.form.get("guide_id", type=int)
            b.guide = db.session.get(User, gid) if gid else None
            if b.slot:
                from ..workflow import guide_conflict, horse_conflicts
                problems = horse_conflicts(b, b.slot, list(b.horses))
                if (gc := guide_conflict(b, b.slot, b.guide)):
                    problems.append(gc)
                if problems:
                    raise WorkflowError(" ".join(problems))
            flash("Horses and guide updated.", "success")
        elif action == "check_in":
            if not g.user.can("check_in"):
                abort(403)
            b.checked_in_at = utcnow()
        else:
            raise WorkflowError("Unknown action.")
        audit(f"booking_{action}", "booking", b.reference, note[:300] or None)
        db.session.commit()
        if action not in {"assign"}:
            flash(f"Done — {b.reference} is now '{b.status}'.", "success")
    except (WorkflowError, ValueError) as exc:
        db.session.rollback()
        flash(str(exc) if isinstance(exc, WorkflowError) else "Enter a valid date and time.", "error")
    return redirect(request.referrer or url_for("admin.request_detail", booking_id=booking_id))


def _system_and_customer_msg(b: Booking, body: str) -> None:
    db.session.add(Message(booking=b, sender="admin", user_id=g.user.id, body=body, read_by_admin=True))


@bp.route("/requests/<int:booking_id>/message", methods=["POST"])
@staff_required("message")
def request_message(booking_id: int):
    b = _booking_or_404(booking_id)
    body = (request.form.get("body") or "").strip()
    internal = request.form.get("internal") == "1"
    if not 1 <= len(body) <= 3000:
        flash("Write a message (up to 3000 characters).", "error")
    else:
        db.session.add(Message(booking=b, sender="admin", user_id=g.user.id, body=body, internal=internal,
                               read_by_admin=True, read_by_customer=internal))
        if not internal:
            notify.new_message(b, False, body)
        db.session.commit()
        flash("Private note saved." if internal else "Message sent to the customer.", "success")
    return redirect(url_for("admin.request_detail", booking_id=b.id) + "#messages")


@bp.route("/requests/<int:booking_id>/manual-payment", methods=["POST"])
@staff_required("manual_payments")
def manual_payment(booking_id: int):
    """Record an EFT/cash payment that a manager has verified in the business bank account.
    (Never on the strength of a customer's screenshot.)"""
    b = _booking_or_404(booking_id)
    form = Form(request.form)
    amount = form.money_cents("amount", "Amount")
    method = form.choice("method", "Method", ["EFT (verified on bank statement)", "Cash", "Card machine"])
    ref = form.text("bank_reference", "Bank / receipt reference", min_len=3, max_len=100)
    if not form.checked("verified"):
        form.errors["verified"] = "Confirm you have verified the funds yourself."
    if b.approved_at is None:
        form.errors["amount"] = "Payments can only be recorded on approved bookings."
    if form.ok and amount <= 0:
        form.errors["amount"] = "Enter an amount greater than zero."
    if not form.ok:
        flash(" ".join(form.errors.values()), "error")
        return redirect(url_for("admin.request_detail", booking_id=b.id) + "#payments")
    p = Payment(booking=b, m_payment_id=f"{b.reference}-M{len(b.payments) + 1}", provider="manual",
                kind="balance" if b.amount_paid_cents else ("deposit" if b.payment_mode == "deposit" else "full"),
                amount_cents=amount, method_note=f"{method}: {ref}", recorded_by_id=g.user.id)
    db.session.add(p)
    db.session.flush()
    try:
        record_payment(p, None, actor=g.user)
        notify.payment_received(b, amount)
        audit("manual_payment", "booking", b.reference, f"{amount} {method} {ref}")
        db.session.commit()
        flash("Payment recorded.", "success")
    except WorkflowError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("admin.request_detail", booking_id=b.id) + "#payments")


@bp.route("/requests/<int:booking_id>/refund", methods=["POST"])
@staff_required("refunds")
def refund(booking_id: int):
    b = _booking_or_404(booking_id)
    action = request.form.get("action")
    try:
        if action == "create":
            form = Form(request.form)
            amount = form.money_cents("amount", "Refund amount")
            reason = form.text("reason", "Reason", min_len=3, max_len=300)
            already = sum(r.amount_cents for r in b.refunds)
            if form.ok and (amount <= 0 or amount + already > b.amount_paid_cents):
                form.errors["amount"] = "Refunds can't exceed what was paid."
            if not form.ok:
                raise WorkflowError(" ".join(form.errors.values()))
            db.session.add(Refund(booking=b, amount_cents=amount, reason=reason, created_by_id=g.user.id))
            if b.status != REFUND_PENDING:
                if b.status in {CONFIRMED, RESCHEDULED, RESCHED_REQ}:
                    transition(b, CANCELLED, g.user, note="Cancelled for refund: " + reason)
                transition(b, REFUND_PENDING, g.user, note=f"Refund of {notify.money(amount)} started: {reason}")
            notify.status_update(b, "Your refund is being processed", f"Amount: {notify.money(amount)}. {reason}")
        elif action == "complete":
            r = db.session.get(Refund, request.form.get("refund_id", type=int) or 0)
            if r is None or r.booking_id != b.id or r.status == "completed":
                raise WorkflowError("Refund not found.")
            r.status, r.completed_at = "completed", utcnow()
            db.session.flush()
            new = REFUNDED if b.amount_refunded_cents >= b.amount_paid_cents else PART_REFUNDED
            transition(b, new, g.user, note=f"Refund of {notify.money(r.amount_cents)} completed (process it in the "
                                            "PayFast dashboard / bank first).")
            notify.status_update(b, "Your refund has been completed", f"Amount refunded: {notify.money(r.amount_cents)}.")
        audit(f"refund_{action}", "booking", b.reference)
        db.session.commit()
        flash("Refund updated.", "success")
    except WorkflowError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("admin.request_detail", booking_id=b.id) + "#payments")


# --------------------------------------------------------------------------- availability

@bp.route("/availability", methods=["GET", "POST"])
@staff_required("manage_availability")
def availability():
    if request.method == "POST":
        action = request.form.get("action")
        form = Form(request.form)
        if action == "bulk_create":
            start = form.date("start", "From date")
            end = form.date("end", "To date")
            times = [t.strip() for t in (request.form.get("times") or "").split(",") if t.strip()]
            cap = form.integer("capacity", "Capacity", 1, 100)
            weekdays = {int(d) for d in request.form.getlist("weekdays") if d.isdigit()}
            try:
                pairs = [tuple(t.split("-")) for t in times]
                assert pairs and all(len(p) == 2 and len(p[0]) == 5 and len(p[1]) == 5 for p in pairs)
            except AssertionError:
                form.errors["times"] = "Enter sessions like 08:00-09:30, 16:30-18:00"
            if form.ok and (end - start).days > 366:
                form.errors["end"] = "Create at most a year at a time."
            if form.ok:
                created = 0
                d = start
                while d <= end:
                    if not weekdays or d.weekday() in weekdays:
                        for s, e in pairs:
                            if not Slot.query.filter_by(date=d, start_time=s).first():
                                db.session.add(Slot(date=d, start_time=s, end_time=e, capacity=cap))
                                created += 1
                    d += timedelta(days=1)
                audit("slots_created", "slot", None, f"{created} sessions {start}–{end}")
                db.session.commit()
                flash(f"{created} session(s) created.", "success")
            else:
                flash(" ".join(form.errors.values()), "error")
        elif action in {"update", "block", "unblock", "delete"}:
            slot = db.session.get(Slot, request.form.get("slot_id", type=int) or 0)
            if slot is None:
                abort(404)
            if action == "update":
                cap = form.integer("capacity", "Capacity", 0, 100)
                booked = riders_booked(slot)
                if cap is not None and cap < booked:
                    flash(f"Capacity can't go below the {booked} riders already booked.", "error")
                elif form.ok:
                    slot.capacity = cap
                    flash("Session updated.", "success")
            elif action == "block":
                slot.status = "blocked"
                slot.block_reason = (request.form.get("reason") or "Closed").strip()[:200]
                n = Booking.query.filter(Booking.slot_id == slot.id, Booking.status.in_(HOLDS_CAPACITY)).count()
                flash(f"Session blocked. {n} booking(s) in it need rescheduling." if n else "Session blocked.",
                      "warning" if n else "success")
            elif action == "unblock":
                slot.status, slot.block_reason = "open", None
                flash("Session reopened.", "success")
            elif action == "delete":
                if Booking.query.filter_by(slot_id=slot.id).count():
                    flash("This session has bookings — block it instead of deleting.", "error")
                else:
                    db.session.delete(slot)
                    flash("Session deleted.", "success")
            audit(f"slot_{action}", "slot", slot.id)
            db.session.commit()
        elif action == "block_day":
            d = form.date("day", "Date")
            reason = (request.form.get("reason") or "Weather closure").strip()[:200]
            if d:
                for s in Slot.query.filter_by(date=d).all():
                    s.status, s.block_reason = "blocked", reason
                audit("day_blocked", "slot", str(d), reason)
                db.session.commit()
                flash(f"All sessions on {d:%d %b} blocked ({reason}).", "success")
        return redirect(url_for("admin.availability", week=request.form.get("week") or request.args.get("week")))

    try:
        week_start = date.fromisoformat(request.args.get("week", "")) if request.args.get("week") else today()
    except ValueError:
        week_start = today()
    week_start -= timedelta(days=week_start.weekday())
    days = [week_start + timedelta(days=i) for i in range(7)]
    slots = Slot.query.filter(Slot.date >= days[0], Slot.date <= days[-1]).order_by(Slot.start_time).all()
    grid = defaultdict(list)
    for s in slots:
        holders = Booking.query.filter(Booking.slot_id == s.id, Booking.status.in_(HOLDS_CAPACITY)).all()
        grid[s.date].append({"slot": s, "booked": sum(b.riders for b in holders), "bookings": holders})
    pending_by_day = Counter(b.preferred_date for b in Booking.query.filter(
        Booking.status.in_(OPEN_REQUEST), Booking.preferred_date >= days[0], Booking.preferred_date <= days[-1]).all())
    return render_template("admin/availability.html", days=days, grid=grid, week_start=week_start,
                           prev_week=week_start - timedelta(days=7), next_week=week_start + timedelta(days=7),
                           pending_by_day=pending_by_day,
                           horses=Horse.query.order_by(Horse.name).all())


# --------------------------------------------------------------------------- horses

@bp.route("/horses", methods=["GET", "POST"])
@staff_required("manage_horses")
def horses():
    if request.method == "POST":
        hid = request.form.get("horse_id", type=int)
        horse = db.session.get(Horse, hid) if hid else Horse()
        form = Form(request.form)
        horse.name = form.text("name", "Name", max_len=80)
        horse.temperament = form.text("temperament", "Temperament", required=False, max_len=200)
        horse.suitable_for = form.text("suitable_for", "Suitable for", required=False, max_len=200)
        horse.max_rider_weight_kg = form.integer("max_rider_weight_kg", "Max rider weight", 20, 200, required=False)
        horse.status = form.choice("status", "Status", ["available", "resting", "maintenance", "retired"])
        horse.category = form.choice("category", "Horse type", list(HORSE_TYPES))
        horse.breed = form.text("breed", "Breed", required=False, max_len=80) or None
        horse.colour = form.text("colour", "Colour", required=False, max_len=40) or None
        horse.sex = form.choice("sex", "Sex", ["mare", "gelding", "stallion"], required=False) or None
        horse.birth_year = form.integer("birth_year", "Year of birth", 1980, today().year, required=False)
        hh = form.text("height_hands", "Height", required=False, max_len=10)
        if hh and not __import__("re").match(r"^\d{1,2}(\.[0-3])?$", hh):
            form.errors["height_hands"] = "Height must be in hands, e.g. 15.2 (15 hands 2 inches)."
        horse.height_hands = hh or None
        horse.joined_year = form.integer("joined_year", "Joined year", 1980, today().year, required=False)
        horse.favourite_treat = form.text("favourite_treat", "Favourite treat", required=False, max_len=80) or None
        horse.fun_fact = form.text("fun_fact", "Fun fact", required=False, max_len=300) or None
        horse.history = form.text("history", "History", required=False, max_len=3000) or None
        horse.notes = form.text("notes", "Notes", required=False, max_len=2000)
        horse.show_publicly = form.checked("show_publicly")
        try:
            if (p := save_upload(request.files.get("photo"), "public/horses", IMAGE_EXT)):
                horse.photo_path = p
        except ValueError as exc:
            form.errors["photo"] = str(exc)
        if form.ok:
            if not hid:
                db.session.add(horse)
            audit("horse_saved", "horse", horse.name)
            db.session.commit()
            flash(f"{horse.name} saved.", "success")
        else:
            db.session.rollback()
            flash(" ".join(form.errors.values()), "error")
        return redirect(url_for("admin.horses"))
    edit = db.session.get(Horse, request.args.get("edit", type=int) or 0)
    return render_template("admin/horses.html", horses=Horse.query.order_by(Horse.category, Horse.name).all(), edit=edit,
                           types=HORSE_TYPES)


# --------------------------------------------------------------------------- packages & add-ons

@bp.route("/packages")
@staff_required("manage_packages")
def packages():
    return render_template("admin/packages.html", packages=Experience.query.order_by(Experience.sort_order).all(),
                           addons=AddOn.query.all())


@bp.route("/packages/<int:exp_id>", methods=["GET", "POST"])
@bp.route("/packages/new", methods=["GET", "POST"], defaults={"exp_id": None})
@staff_required("manage_packages")
def package_edit(exp_id):
    exp = db.session.get(Experience, exp_id) if exp_id else Experience(kind="package", price_basis="per_person")
    if exp_id and exp is None:
        abort(404)
    form = Form(request.form)
    if request.method == "POST":
        if request.form.get("action") == "delete" and exp_id:
            if Booking.query.filter_by(experience_id=exp.id).count():
                exp.is_active = False
                flash("This package has bookings, so it was deactivated instead of deleted.", "info")
            else:
                db.session.delete(exp)
                flash("Package removed.", "success")
            audit("package_deleted", "experience", exp_id)
            db.session.commit()
            return redirect(url_for("admin.packages"))
        exp.name = form.text("name", "Name", max_len=120)
        slug = form.text("slug", "URL name", max_len=80).lower().replace(" ", "-")
        clash = Experience.query.filter(Experience.slug == slug, Experience.id != (exp.id or 0)).first()
        if clash:
            form.errors["slug"] = "Another package uses that URL name."
        exp.slug = slug
        exp.kind = form.choice("kind", "Type", ["ride", "package"])
        exp.summary = form.text("summary", "Summary", max_len=300)
        exp.description = form.text("description", "Description", required=False, max_len=5000)
        exp.duration_minutes = form.integer("duration_minutes", "Duration", 10, 1440, required=False)
        exp.price_basis = form.choice("price_basis", "Price basis", ["per_person", "per_group", "quote"])
        exp.price_cents = form.money_cents("price", "Price", required=False) if exp.price_basis != "quote" else None
        exp.min_group = form.integer("min_group", "Minimum group", 1, 500) or 1
        exp.max_group = form.integer("max_group", "Maximum group", 1, 500, required=False)
        exp.min_age = form.integer("min_age", "Minimum age", 1, 99, required=False)
        for f, label, mx in [("difficulty", "Difficulty", 30), ("requirements", "Requirements", 3000),
                             ("inclusions", "Inclusions", 3000), ("exclusions", "Exclusions", 3000),
                             ("terms", "Terms", 3000), ("safety_notes", "Safety notes", 3000),
                             ("availability_note", "Availability", 200)]:
            setattr(exp, f, form.text(f, label, required=False, max_len=mx))
        exp.sort_order = form.integer("sort_order", "Order", 0, 10000, default=100)
        exp.is_active = form.checked("is_active")
        exp.is_featured = form.checked("is_featured")
        try:
            if (p := save_upload(request.files.get("image"), "public/packages", IMAGE_EXT)):
                exp.image_path = p
        except ValueError as exc:
            form.errors["image"] = str(exc)
        if form.ok:
            if not exp_id:
                db.session.add(exp)
            audit("package_saved", "experience", exp.slug, f"price={exp.price_cents} basis={exp.price_basis}")
            db.session.commit()
            flash(f"{exp.name} saved.", "success")
            return redirect(url_for("admin.packages"))
        db.session.rollback()
        flash("Please check the highlighted fields.", "error")
    return render_template("admin/package_edit.html", exp=exp, form=form)


@bp.route("/addons", methods=["POST"])
@staff_required("manage_packages")
def addons():
    form = Form(request.form)
    aid = request.form.get("addon_id", type=int)
    addon = db.session.get(AddOn, aid) if aid else AddOn()
    if request.form.get("action") == "toggle" and aid:
        addon.is_active = not addon.is_active
    else:
        addon.name = form.text("name", "Name", max_len=120)
        addon.description = form.text("description", "Description", required=False, max_len=300)
        addon.price_basis = form.choice("price_basis", "Price basis", ["per_booking", "per_person"])
        addon.price_cents = form.money_cents("price", "Price", required=False)
        if not form.ok:
            flash(" ".join(form.errors.values()), "error")
            return redirect(url_for("admin.packages") + "#addons")
        if not aid:
            db.session.add(addon)
    audit("addon_saved", "addon", addon.name)
    db.session.commit()
    flash("Add-on saved.", "success")
    return redirect(url_for("admin.packages") + "#addons")


# --------------------------------------------------------------------------- payments

@bp.route("/payments")
@staff_required("reports")
def payments():
    status = request.args.get("status", "")
    q = Payment.query
    if status:
        q = q.filter(Payment.status == status)
    from .payments import notify_reachable
    from flask import current_app
    gateway = {"provider": current_app.config["PAYMENT_PROVIDER"], "sandbox": current_app.config["PAYFAST_SANDBOX"],
               "merchant": current_app.config["PAYFAST_MERCHANT_ID"], "reachable": notify_reachable(),
               "notify": current_app.config.get("PAYFAST_NOTIFY_URL") or ((current_app.config.get("PUBLIC_SITE_URL")
                         or current_app.config.get("TUNNEL_URL") or "") + "/payments/payfast/notify"),
               "paystack_key": (current_app.config.get("PAYSTACK_SECRET_KEY") or "")[:8],
               "paystack_webhook": (current_app.config.get("PUBLIC_SITE_URL") or request.host_url.rstrip("/"))
                                   + "/payments/paystack/webhook"}
    return render_template("admin/payments.html", gateway=gateway, payments=q.order_by(Payment.id.desc()).limit(300).all(),
                           events=PaymentEvent.query.order_by(PaymentEvent.id.desc()).limit(50).all(),
                           refunds=Refund.query.order_by(Refund.id.desc()).limit(50).all(), status=status)


# --------------------------------------------------------------------------- communication

@bp.route("/messages")
@staff_required("message")
def messages():
    unread = (db.session.query(Booking, func.count(Message.id)).join(Message)
              .filter(Message.sender == "customer", Message.read_by_admin.is_(False))
              .group_by(Booking.id).all())
    latest = Message.query.filter(Message.internal.is_(False)).order_by(Message.id.desc()).limit(40).all()
    return render_template("admin/messages.html", unread=unread, latest=latest)


@bp.route("/enquiries", methods=["GET", "POST"])
@staff_required("message")
def enquiries():
    if request.method == "POST":
        enq = db.session.get(Enquiry, request.form.get("enquiry_id", type=int) or 0)
        if enq is None:
            abort(404)
        if request.form.get("action") == "close":
            enq.status = "closed"
        else:
            reply = (request.form.get("reply") or "").strip()
            if len(reply) < 2:
                flash("Write a reply.", "error")
                return redirect(url_for("admin.enquiries"))
            enq.reply, enq.status = reply, "replied"
            notify.queue(enq.email, f"Re: {enq.subject}", f"Hi {enq.name},\n\n{reply}\n\n---\nYou wrote:\n{enq.message}")
        db.session.commit()
        flash("Enquiry updated.", "success")
        return redirect(url_for("admin.enquiries"))
    return render_template("admin/enquiries.html", enquiries=Enquiry.query.order_by(Enquiry.id.desc()).limit(200).all())


@bp.route("/outbox", methods=["GET", "POST"])
@staff_required("message")
def outbox():
    if request.method == "POST":
        if request.form.get("action") == "test":
            to = (request.form.get("to") or g.user.email).strip()
            ok, message = notify.send_test(to)
            flash(message, "success" if ok else "error")
            return redirect(url_for("admin.outbox"))
        # Retry failed emails, and emails only recorded in the last 24 hours (before email was set up).
        recent = utcnow() - timedelta(hours=24)
        Notification.query.filter((Notification.status == "failed") |
                                  ((Notification.status == "logged") & (Notification.created_at >= recent))) \
            .update({"status": "queued", "attempts": 0}, synchronize_session=False)
        db.session.commit()
        sent = notify.deliver_pending(100)
        flash(f"Retry complete — {sent} sent." if notify.provider(current_app.config)
              else "No email service is set up yet, so the emails were recorded again but not sent.",
              "success" if sent else "info")
        return redirect(url_for("admin.outbox"))
    return render_template("admin/outbox.html", items=Notification.query.order_by(Notification.id.desc()).limit(150).all(),
                           provider=notify.provider(current_app.config))


# --------------------------------------------------------------------------- reviews, gallery, waivers

@bp.route("/reviews", methods=["GET", "POST"])
@staff_required("reviews")
def reviews():
    if request.method == "POST":
        r = db.session.get(Review, request.form.get("review_id", type=int) or 0)
        if r is None:
            abort(404)
        r.status = {"approve": "approved", "hide": "hidden"}.get(request.form.get("action"), r.status)
        audit("review_moderated", "review", r.id, r.status)
        db.session.commit()
        return redirect(url_for("admin.reviews"))
    return render_template("admin/reviews.html", reviews=Review.query.order_by(Review.id.desc()).all())


@bp.route("/gallery", methods=["GET", "POST"])
@staff_required("gallery")
def gallery():
    if request.method == "POST":
        action = request.form.get("action")
        if action == "upload":
            try:
                files = request.files.getlist("photos")
                saved = 0
                for f in files[:20]:
                    if (p := save_upload(f, "gallery", IMAGE_EXT)):
                        db.session.add(GalleryImage(path=p, caption=(request.form.get("caption") or "")[:200],
                                                    category=request.form.get("category", "rides"),
                                                    consent_confirmed=request.form.get("consent") == "on"))
                        saved += 1
                db.session.commit()
                flash(f"{saved} photo(s) uploaded. Publish them when ready.", "success")
            except ValueError as exc:
                db.session.rollback()
                flash(str(exc), "error")
        else:
            img = db.session.get(GalleryImage, request.form.get("image_id", type=int) or 0)
            if img is None:
                abort(404)
            if action == "publish":
                if not img.consent_confirmed and request.form.get("consent") != "on":
                    flash("Confirm the people in the photo agreed to publication first.", "error")
                else:
                    img.consent_confirmed, img.is_published = True, True
            elif action == "unpublish":
                img.is_published = False
            elif action == "caption":
                img.caption = (request.form.get("caption") or "")[:200]
            elif action == "delete":
                db.session.delete(img)
            db.session.commit()
        return redirect(url_for("admin.gallery"))
    return render_template("admin/gallery.html", images=GalleryImage.query.order_by(GalleryImage.id.desc()).all(),
                           hero=get_setting("hero_image"))


@bp.route("/waivers", methods=["GET", "POST"])
@staff_required("waivers")
def waivers():
    if request.method == "POST":
        form = Form(request.form)
        title = form.text("title", "Title", max_len=150)
        body = form.text("body", "Waiver text", min_len=50, max_len=20000)
        if form.ok:
            version = (db.session.query(func.max(Waiver.version)).scalar() or 0) + 1
            Waiver.query.update({"is_active": False})
            db.session.add(Waiver(version=version, title=title, body=body, is_active=True))
            audit("waiver_published", "waiver", version)
            db.session.commit()
            flash(f"Waiver version {version} published. New signatures record this version.", "success")
        else:
            flash(" ".join(form.errors.values()), "error")
        return redirect(url_for("admin.waivers"))
    return render_template("admin/waivers.html", waivers=Waiver.query.order_by(Waiver.version.desc()).all(),
                           signatures=WaiverSignature.query.order_by(WaiverSignature.id.desc()).limit(50).all())


# --------------------------------------------------------------------------- operations / weather
_weather_cache: dict = {}


def fetch_forecast(lat: str, lon: str):
    key = (lat, lon)
    if key in _weather_cache and time.time() - _weather_cache[key][0] < 1800:
        return _weather_cache[key][1], None
    try:
        resp = requests.get("https://api.open-meteo.com/v1/forecast", timeout=10, params={
            "latitude": lat, "longitude": lon, "timezone": "Africa/Johannesburg", "forecast_days": 7,
            "daily": "wind_speed_10m_max,wind_gusts_10m_max,precipitation_sum,temperature_2m_max,temperature_2m_min"})
        resp.raise_for_status()
        d = resp.json()["daily"]
        rows = [{"date": d["time"][i], "wind": d["wind_speed_10m_max"][i], "gust": d["wind_gusts_10m_max"][i],
                 "rain": d["precipitation_sum"][i], "tmax": d["temperature_2m_max"][i], "tmin": d["temperature_2m_min"][i]}
                for i in range(len(d["time"]))]
        _weather_cache[key] = (time.time(), rows)
        return rows, None
    except Exception as exc:  # noqa: BLE001
        return None, f"Forecast unavailable right now ({exc.__class__.__name__}). Use local observation."


def _flags(row: dict, s: dict) -> list[str]:
    flags = []
    checks = [("limit_wind_kmh", "wind", ">", "Wind"), ("limit_gust_kmh", "gust", ">", "Gusts"),
              ("limit_rain_mm", "rain", ">", "Rain"), ("limit_temp_max_c", "tmax", ">", "Max temp"),
              ("limit_temp_min_c", "tmin", "<", "Min temp")]
    for key, field, op, label in checks:
        try:
            limit = float(s.get(key) or "")
        except ValueError:
            continue
        v = row.get(field)
        if v is not None and ((op == ">" and v > limit) or (op == "<" and v < limit)):
            flags.append(f"{label} {v} vs limit {limit:g}")
    return flags


@bp.route("/operations", methods=["GET", "POST"])
@staff_required("weather")
def operations():
    s = all_settings()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "status":
            status = request.form.get("ops_status")
            if status in OPS_STATUS_LABELS:
                set_setting("ops_status", status)
                set_setting("ops_message", (request.form.get("ops_message") or "").strip()[:300])
                audit("ops_status", "settings", status)
                flash(f"Operations status set to {OPS_STATUS_LABELS[status]}.", "success")
        elif action == "limits":
            for k in ["weather_latitude", "weather_longitude", "limit_wind_kmh", "limit_gust_kmh", "limit_rain_mm",
                      "limit_temp_min_c", "limit_temp_max_c", "weather_template"]:
                set_setting(k, (request.form.get(k) or "").strip()[:1000])
            audit("weather_limits", "settings")
            flash("Weather settings saved.", "success")
        elif action == "notify_day":
            try:
                d = date.fromisoformat(request.form.get("day", ""))
            except ValueError:
                flash("Choose a date.", "error")
                return redirect(url_for("admin.operations"))
            affected = Booking.query.filter(Booking.preferred_date == d, Booking.status.in_(HOLDS_CAPACITY)).all()
            template = request.form.get("message") or s["weather_template"]
            for b in affected:
                body = template.format(name=b.contact_name, date=f"{d:%a %d %b}", reference=b.reference)
                db.session.add(Message(booking=b, sender="admin", user_id=g.user.id, body=body, read_by_admin=True))
                notify.queue(b.contact_email, f"Weather update for your ride on {d:%d %b} — {b.reference}", body, b)
                if request.form.get("request_reschedule") == "on" and b.status in {CONFIRMED, RESCHEDULED}:
                    transition(b, RESCHED_REQ, g.user, note="Weather — rescheduling needed.")
            audit("weather_notice", "booking", str(d), f"{len(affected)} bookings")
            flash(f"Weather notice sent to {len(affected)} booking(s).", "success")
        db.session.commit()
        return redirect(url_for("admin.operations"))
    forecast, error = (None, None)
    if s.get("weather_latitude") and s.get("weather_longitude"):
        forecast, error = fetch_forecast(s["weather_latitude"], s["weather_longitude"])
        for row in forecast or []:
            row["flags"] = _flags(row, s)
    return render_template("admin/operations.html", s=s, forecast=forecast, error=error, labels=OPS_STATUS_LABELS)


# --------------------------------------------------------------------------- check-in

@bp.route("/check-in")
@staff_required("check_in")
def check_in():
    try:
        d = date.fromisoformat(request.args.get("date", "")) if request.args.get("date") else today()
    except ValueError:
        d = today()
    q = Booking.query.join(Slot).filter(Slot.date == d, Booking.status.in_(HOLDS_CAPACITY))
    if g.user.role == "guide":
        q = q.filter(Booking.guide_id == g.user.id)
    return render_template("admin/check_in.html", bookings=q.order_by(Slot.start_time).all(), day=d)


# --------------------------------------------------------------------------- reports

def _report_data(start: date, end: date) -> dict:
    bookings = Booking.query.filter(Booking.created_at >= datetime.combine(start, datetime.min.time()),
                                    Booking.created_at <= datetime.combine(end, datetime.max.time())).all()
    pays = Payment.query.filter(Payment.status == "paid", Payment.paid_at >= datetime.combine(start, datetime.min.time()),
                                Payment.paid_at <= datetime.combine(end, datetime.max.time())).all()
    refunds = Refund.query.filter(Refund.created_at >= datetime.combine(start, datetime.min.time()),
                                  Refund.created_at <= datetime.combine(end, datetime.max.time())).all()
    by_status = Counter(b.status for b in bookings)
    by_package = Counter(b.display_name for b in bookings)
    paid_bookings = {p.booking_id for p in pays}
    booked_value = sum(b.final_price_cents or 0 for b in bookings if b.approved_at)
    outstanding = sum(b.balance_cents for b in Booking.query.filter(
        Booking.status.in_([APPROVED, PROCESSING, CONFIRMED, RESCHEDULED, RESCHED_REQ])).all())
    by_month = defaultdict(int)
    for p in pays:
        by_month[p.paid_at.strftime("%Y-%m")] += p.amount_cents
    slots = Slot.query.filter(Slot.date >= start, Slot.date <= end).all()
    cap_total = sum(s.capacity for s in slots if s.status == "open")
    cap_used = sum(riders_booked(s) for s in slots)
    return {
        "bookings": bookings, "requests": len(bookings), "by_status": by_status.most_common(),
        "by_package": by_package.most_common(), "conversion": (len(paid_bookings & {b.id for b in bookings}) / len(bookings) * 100) if bookings else 0,
        "booked_value": booked_value, "received": sum(p.amount_cents for p in pays),
        "refunded": sum(r.amount_cents for r in refunds if r.status == "completed"),
        "refunds_pending": sum(r.amount_cents for r in refunds if r.status == "pending"),
        "outstanding": outstanding, "by_month": sorted(by_month.items()),
        "cancellations": sum(1 for b in bookings if b.status in {CANCELLED, REFUND_PENDING, REFUNDED, PART_REFUNDED}),
        "utilisation": (cap_used / cap_total * 100) if cap_total else 0, "cap_used": cap_used, "cap_total": cap_total,
        "payments": pays,
    }


def _range():
    try:
        end = date.fromisoformat(request.args["to"]) if request.args.get("to") else today()
        start = date.fromisoformat(request.args["from"]) if request.args.get("from") else end - timedelta(days=90)
    except ValueError:
        end, start = today(), today() - timedelta(days=90)
    return start, end


@bp.route("/reports")
@staff_required("reports")
def reports():
    start, end = _range()
    data = _report_data(start, end)
    customers = (db.session.query(Booking.contact_email, Booking.contact_name, func.count(Booking.id))
                 .group_by(Booking.contact_email, Booking.contact_name).order_by(func.count(Booking.id).desc()).limit(15).all())
    return render_template("admin/reports.html", d=data, start=start, end=end, customers=customers)


class _SafeWriter:
    """Prevents spreadsheet formula injection from customer-entered text (=, +, -, @ at cell start)."""

    def __init__(self, writer):
        self.writer = writer

    @staticmethod
    def cell(v):
        s = "" if v is None else str(v)
        if s and s[0] in "=+-@\t\r":
            try:
                float(s)
            except ValueError:
                return "'" + s
        return s

    def writerow(self, row):
        self.writer.writerow([self.cell(v) for v in row])


@bp.route("/reports/export/<kind>.csv")
@staff_required("reports")
def export(kind: str):
    start, end = _range()
    data = _report_data(start, end)
    buf = io.StringIO()
    w = _SafeWriter(csv.writer(buf))
    if kind == "bookings":
        w.writerow(["Reference", "Created", "Type", "Experience", "Status", "Ride date", "Riders", "Group", "Customer",
                    "Email", "Phone", "Final price (R)", "Paid (R)", "Refunded (R)", "Balance (R)"])
        for b in data["bookings"]:
            w.writerow([b.reference, f"{b.created_at:%Y-%m-%d}", b.kind, b.display_name, b.status, b.preferred_date,
                        b.riders, b.group_size, b.contact_name, b.contact_email, b.contact_phone,
                        f"{(b.final_price_cents or 0) / 100:.2f}", f"{b.amount_paid_cents / 100:.2f}",
                        f"{b.amount_refunded_cents / 100:.2f}", f"{b.balance_cents / 100:.2f}"])
    elif kind == "payments":
        w.writerow(["Payment ref", "Booking", "Provider", "Type", "Amount (R)", "Status", "Gateway ID", "Paid at", "Note"])
        for p in data["payments"]:
            w.writerow([p.m_payment_id, p.booking.reference, p.provider, p.kind, f"{p.amount_cents / 100:.2f}", p.status,
                        p.gateway_payment_id or "", f"{p.paid_at:%Y-%m-%d %H:%M}" if p.paid_at else "", p.method_note or ""])
    else:
        abort(404)
    audit("export", "report", kind, f"{start}–{end}")
    db.session.commit()
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=seahorse-{kind}-{start}-{end}.csv"})


# --------------------------------------------------------------------------- users & settings (super admin)

@bp.route("/users", methods=["GET", "POST"])
@staff_required("users")
def users():
    if request.method == "POST":
        action = request.form.get("action")
        if action == "create":
            form = Form(request.form)
            email = form.email()
            name = form.text("name", "Name", min_len=2, max_len=120)
            role = form.choice("role", "Role", ROLES)
            pw = request.form.get("password") or ""
            if len(pw) < 10:
                form.errors["password"] = "Temporary password must be at least 10 characters."
            if form.ok and User.query.filter_by(email=email).first():
                form.errors["email"] = "That email already has an account — change its role below."
            if form.ok:
                u = User(email=email, name=name, role=role, email_verified=True)
                u.set_password(pw)
                db.session.add(u)
                audit("user_created", "user", email, role)
                db.session.commit()
                flash(f"{email} created as {ROLE_LABELS[role]}. Ask them to change the password.", "success")
            else:
                flash(" ".join(form.errors.values()), "error")
        else:
            u = db.session.get(User, request.form.get("user_id", type=int) or 0)
            if u is None:
                abort(404)
            if u.id == g.user.id:
                flash("You can't change your own role or access.", "error")
                return redirect(url_for("admin.users"))
            if action == "role" and request.form.get("role") in ROLES:
                u.role = request.form["role"]
                audit("role_changed", "user", u.email, u.role)
            elif action == "toggle":
                u.is_active = not u.is_active
                audit("user_toggled", "user", u.email, str(u.is_active))
            db.session.commit()
            flash("User updated.", "success")
        return redirect(url_for("admin.users"))
    q = User.query
    if request.args.get("staff"):
        q = q.filter(User.role != "customer")
    return render_template("admin/users.html", users=q.order_by(User.role.desc(), User.name).limit(500).all(),
                           roles=ROLES, labels=ROLE_LABELS)


@bp.route("/settings", methods=["GET", "POST"])
@staff_required("settings")
def settings():
    editable = ["business_name", "tagline", "contact_email", "contact_phone", "whatsapp_number", "address",
                "meeting_point", "operating_hours", "facebook_url", "instagram_url", "staff_alert_email",
                "deposit_percent", "payment_deadline_hours", "reminder_hours", "cancellation_policy", "ride_locations"]
    if request.method == "POST":
        for k in editable:
            set_setting(k, (request.form.get(k) or "").strip()[:5000])
        try:
            if (p := save_upload(request.files.get("hero_image"), "public/site", IMAGE_EXT)):
                set_setting("hero_image", p)
        except ValueError as exc:
            flash(str(exc), "error")
        if request.form.get("remove_hero"):
            set_setting("hero_image", "")
        audit("settings_saved", "settings")
        db.session.commit()
        flash("Settings saved.", "success")
        return redirect(url_for("admin.settings"))
    return render_template("admin/settings.html", s=all_settings(), editable=editable, defaults=DEFAULTS)


@bp.route("/audit")
@staff_required("audit")
def audit_log():
    return render_template("admin/audit.html", rows=AuditLog.query.order_by(AuditLog.id.desc()).limit(300).all())


# --------------------------------------------------------------------------- data structures (BST + BFS)

@bp.route("/data-structures")
@staff_required()
def data_structures():
    """Builds a binary search tree from values typed in (e.g. an examiner's list), searches it and shows the
    three depth-first traversals; also the real bookings as a BookingTree and a BFS over the status graph."""
    from ..structures import BinarySearchTree, BookingTree, parse_keys, status_path

    raw = request.args.get("values", "50, 30, 70, 20, 40, 60, 80")
    keys = parse_keys(raw)
    tree = BinarySearchTree()
    trace = [{"key": k, "path": tree.insert(k)} for k in keys]       # insertion order + comparisons made
    find_raw = request.args.get("find", "").strip()
    found = path = None
    if find_raw and keys:
        target = parse_keys(find_raw, limit=1)
        if target and type(target[0]) is type(keys[0]):
            found, path = tree.search(target[0])
            find_raw = target[0]
    nodes, edges = tree.layout()
    stats = {"size": len(tree), "height": tree.height(), "root": tree.root.key if tree.root else None,
             "leaves": tree.leaves(), "min": tree.minimum(), "max": tree.maximum(), "skewed": tree.is_skewed()}

    # The project's own use: upcoming rides that hold places, as a BST keyed by ride date and time.
    upcoming = (Booking.query.filter(Booking.status.in_(HOLDS_CAPACITY), Booking.preferred_date >= today()).all())
    btree = BookingTree.from_bookings(upcoming)
    week = btree.rides_between(today(), today() + timedelta(days=7))
    bnodes, bedges = btree.layout()

    s_from = request.args.get("from_status", PENDING)
    s_to = request.args.get("to_status", REFUNDED)
    s_path = status_path(s_from, s_to) if s_from in ALL_STATUSES and s_to in ALL_STATUSES else None
    return render_template("admin/data_structures.html", raw=raw, keys=keys, trace=trace, tree=tree, nodes=nodes,
                           edges=edges, stats=stats, find=find_raw, found=found, path=path or [], btree=btree,
                           bnodes=bnodes, bedges=bedges, week=week, statuses=ALL_STATUSES, s_from=s_from, s_to=s_to,
                           s_path=s_path)
