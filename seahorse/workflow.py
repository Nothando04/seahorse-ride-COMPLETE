"""Booking status workflow, capacity control and the admin-approval-before-payment rules.

All status changes go through ``transition`` so every change is validated and recorded in the
timestamped history with who made it.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func

from .extensions import db
from .models import Booking, Horse, Payment, Slot, StatusHistory, User, booking_horses, utcnow

DRAFT = "Draft"
PENDING = "Pending Admin Approval"
NEED_INFO = "Awaiting Customer Information"
CHANGES = "Changes Proposed"
APPROVED = "Approved — Awaiting Payment"
PROCESSING = "Payment Processing"
CONFIRMED = "Confirmed — Paid"
DECLINED = "Declined"
CANCELLED = "Cancelled"
RESCHED_REQ = "Rescheduling Requested"
RESCHEDULED = "Rescheduled"
COMPLETED = "Completed"
NO_SHOW = "No-Show"
REFUND_PENDING = "Refund Pending"
PART_REFUNDED = "Partially Refunded"
REFUNDED = "Refunded"
EXPIRED = "Payment Expired"

ALL_STATUSES = [DRAFT, PENDING, NEED_INFO, CHANGES, APPROVED, PROCESSING, CONFIRMED, DECLINED, CANCELLED,
                RESCHED_REQ, RESCHEDULED, COMPLETED, NO_SHOW, REFUND_PENDING, PART_REFUNDED, REFUNDED, EXPIRED]

TRANSITIONS: dict[str, set[str]] = {
    DRAFT: {PENDING, CANCELLED},
    PENDING: {NEED_INFO, CHANGES, APPROVED, DECLINED, CANCELLED},
    NEED_INFO: {PENDING, CHANGES, APPROVED, DECLINED, CANCELLED},
    CHANGES: {PENDING, DECLINED, CANCELLED},
    APPROVED: {PROCESSING, CONFIRMED, EXPIRED, CANCELLED, CHANGES},
    PROCESSING: {CONFIRMED, APPROVED, EXPIRED, CANCELLED},
    EXPIRED: {APPROVED, CONFIRMED, CANCELLED},
    CONFIRMED: {RESCHED_REQ, RESCHEDULED, COMPLETED, NO_SHOW, CANCELLED, REFUND_PENDING},
    RESCHED_REQ: {RESCHEDULED, CONFIRMED, CANCELLED, REFUND_PENDING},
    RESCHEDULED: {RESCHED_REQ, RESCHEDULED, COMPLETED, NO_SHOW, CANCELLED, REFUND_PENDING},
    CANCELLED: {REFUND_PENDING},
    NO_SHOW: {REFUND_PENDING},
    REFUND_PENDING: {PART_REFUNDED, REFUNDED},
    PART_REFUNDED: {REFUND_PENDING, REFUNDED},
    DECLINED: set(),
    COMPLETED: set(),
    REFUNDED: set(),
}

# Statuses that occupy ride capacity. Pending requests never do.
HOLDS_CAPACITY = {APPROVED, PROCESSING, CONFIRMED, RESCHED_REQ, RESCHEDULED}
# Statuses where an approved, unpaid amount may be paid.
PAYABLE = {APPROVED, PROCESSING, CONFIRMED, RESCHED_REQ, RESCHEDULED}
OPEN_REQUEST = {PENDING, NEED_INFO, CHANGES}

STATUS_STYLE = {
    PENDING: "amber", NEED_INFO: "amber", CHANGES: "amber", DRAFT: "grey",
    APPROVED: "blue", PROCESSING: "blue", EXPIRED: "red",
    CONFIRMED: "green", RESCHEDULED: "green", RESCHED_REQ: "amber", COMPLETED: "teal",
    DECLINED: "red", CANCELLED: "red", NO_SHOW: "red",
    REFUND_PENDING: "amber", PART_REFUNDED: "grey", REFUNDED: "grey",
}

CUSTOMER_EXPLANATION = {
    PENDING: "We've received your request. Nothing has been charged. Our team is checking availability, horses, "
             "guides and safety conditions before confirming a final price.",
    NEED_INFO: "We need a little more information before we can review your request. Please reply in the messages below.",
    CHANGES: "We've proposed some changes to your request. Please review them and accept or reply below.",
    APPROVED: "Good news — your booking is approved. Pay the amount shown before the deadline to secure your place.",
    PROCESSING: "Your payment is being processed by the payment provider. This page updates once it is verified.",
    CONFIRMED: "Your booking is confirmed and paid. See you on the beach!",
    EXPIRED: "The payment deadline passed, so the provisional booking was released. Message us if you'd still like to ride.",
    DECLINED: "Unfortunately we can't accommodate this request. See the messages for details.",
    CANCELLED: "This booking has been cancelled.",
    RESCHED_REQ: "Your rescheduling request is with our team.",
    RESCHEDULED: "Your booking has been moved to the new date shown.",
    COMPLETED: "Thank you for riding with us! We'd love your feedback.",
    NO_SHOW: "This booking was marked as a no-show.",
    REFUND_PENDING: "A refund is being processed.",
    PART_REFUNDED: "Part of your payment has been refunded.",
    REFUNDED: "Your payment has been refunded.",
}


class WorkflowError(Exception):
    """A business rule prevented the action. The message is safe to show to users."""


# --------------------------------------------------------------------------- core
def can_transition(current: str, new: str) -> bool:
    return new in TRANSITIONS.get(current, set())


def transition(booking: Booking, new_status: str, actor: User | None = None, note: str | None = None,
               actor_label: str | None = None) -> None:
    if new_status not in ALL_STATUSES:
        raise WorkflowError(f"Unknown status {new_status!r}.")
    current = booking.status
    if current == new_status and new_status != RESCHEDULED:
        return
    if not can_transition(current, new_status):
        raise WorkflowError(f"A booking that is '{current}' cannot be changed to '{new_status}'.")
    booking.status = new_status
    label = actor_label or (f"{actor.name} ({actor.role_label})" if actor else "System")
    db.session.add(StatusHistory(booking=booking, from_status=current, to_status=new_status,
                                 changed_by_id=actor.id if actor else None, actor_label=label, note=note))


def new_reference() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I confusion
    for _ in range(20):
        ref = "SR-" + "".join(secrets.choice(alphabet) for _ in range(6))
        if not Booking.query.filter_by(reference=ref).first():
            return ref
    raise RuntimeError("Could not generate a unique booking reference")


# --------------------------------------------------------------------------- capacity
def lock_slot(slot_id: int) -> Slot | None:
    """Row-lock the slot (PostgreSQL ``SELECT … FOR UPDATE``). SQLite transactions are opened with
    ``BEGIN IMMEDIATE`` (see app factory) so concurrent writers are serialised there too."""
    return db.session.query(Slot).filter_by(id=slot_id).with_for_update().one_or_none()


def riders_booked(slot: Slot, exclude_booking_id: int | None = None) -> int:
    q = db.session.query(func.coalesce(func.sum(Booking.riders), 0)).filter(
        Booking.slot_id == slot.id, Booking.status.in_(HOLDS_CAPACITY))
    if exclude_booking_id:
        q = q.filter(Booking.id != exclude_booking_id)
    return int(q.scalar() or 0)


def remaining_capacity(slot: Slot, exclude_booking_id: int | None = None) -> int:
    if slot.status != "open":
        return 0
    return max(slot.capacity - riders_booked(slot, exclude_booking_id), 0)


def ensure_capacity(booking: Booking, slot: Slot) -> None:
    if slot.status != "open":
        raise WorkflowError(f"The {slot.label} session is blocked ({slot.block_reason or 'closed'}).")
    left = remaining_capacity(slot, exclude_booking_id=booking.id)
    if booking.riders > left:
        raise WorkflowError(f"Not enough space in {slot.label}: {left} rider place(s) left, "
                            f"{booking.riders} needed. Choose another session or adjust the group.")


def horse_conflicts(booking: Booking, slot: Slot, horses: list[Horse]) -> list[str]:
    problems = []
    for horse in horses:
        if horse.status != "available":
            problems.append(f"{horse.name} is marked '{horse.status}'.")
            continue
        clash = (db.session.query(Booking.reference).join(booking_horses, booking_horses.c.booking_id == Booking.id)
                 .filter(booking_horses.c.horse_id == horse.id, Booking.slot_id == slot.id,
                         Booking.status.in_(HOLDS_CAPACITY), Booking.id != booking.id).first())
        if clash:
            problems.append(f"{horse.name} is already assigned to booking {clash[0]} in this session.")
    return problems


def guide_conflict(booking: Booking, slot: Slot, guide: User | None) -> str | None:
    if not guide:
        return None
    clash = (Booking.query.filter(Booking.guide_id == guide.id, Booking.slot_id == slot.id,
                                  Booking.status.in_(HOLDS_CAPACITY), Booking.id != booking.id).first())
    return f"{guide.name} is already guiding booking {clash.reference} in this session." if clash else None


# --------------------------------------------------------------------------- approval
@dataclass
class ApprovalInput:
    slot: Slot
    final_price_cents: int
    payment_mode: str  # full | deposit
    deposit_cents: int | None
    deadline: datetime
    note: str
    horses: list[Horse]
    guide: User | None
    confirmed_checks: set[str]


REQUIRED_CHECKS = {"date_time", "group_size", "price", "availability"}


def approve(booking: Booking, admin: User, data: ApprovalInput) -> None:
    """Approve a request. Payment only becomes possible after this succeeds."""
    if booking.status not in {PENDING, NEED_INFO, EXPIRED}:
        raise WorkflowError(f"Only requests awaiting review can be approved (this one is '{booking.status}').")
    missing = REQUIRED_CHECKS - data.confirmed_checks
    if missing:
        raise WorkflowError("Confirm the final date/time, group size, price and operational availability before approving.")
    if data.final_price_cents <= 0:
        raise WorkflowError("Enter a final price greater than zero.")
    if data.payment_mode == "deposit":
        if not data.deposit_cents or not (0 < data.deposit_cents < data.final_price_cents):
            raise WorkflowError("The deposit must be more than zero and less than the final price.")
    if data.deadline <= utcnow():
        raise WorkflowError("The payment deadline must be in the future.")

    slot = lock_slot(data.slot.id)
    if slot is None:
        raise WorkflowError("That session no longer exists.")
    ensure_capacity(booking, slot)
    problems = horse_conflicts(booking, slot, data.horses)
    if (g := guide_conflict(booking, slot, data.guide)):
        problems.append(g)
    if problems:
        raise WorkflowError(" ".join(problems))

    booking.slot = slot
    booking.preferred_date = slot.date
    booking.preferred_time = slot.start_time
    booking.final_price_cents = data.final_price_cents
    booking.payment_mode = data.payment_mode
    booking.deposit_cents = data.deposit_cents if data.payment_mode == "deposit" else None
    booking.payment_deadline = data.deadline
    booking.approval_note = data.note
    booking.horses = data.horses
    booking.guide = data.guide
    booking.approved_at = utcnow()
    booking.approved_by_id = admin.id
    transition(booking, APPROVED, admin, note=data.note or "Approved after availability and safety review.")


def payable_reason(booking: Booking, now: datetime | None = None) -> str | None:
    """Returns None if the customer may pay now, otherwise the reason they can't."""
    now = now or utcnow()
    if booking.approved_at is None or booking.final_price_cents is None:
        return "This booking has not been approved by our team yet, so payment is not available."
    if booking.status not in PAYABLE:
        return f"Payment is not available while the booking is '{booking.status}'."
    if booking.amount_due_now_cents <= 0:
        return "Nothing is due on this booking right now."
    if booking.status in {APPROVED, PROCESSING} and booking.payment_deadline and booking.payment_deadline < now:
        return "The payment deadline has passed."
    return None


# --------------------------------------------------------------------------- payments
def record_payment(payment: Payment, gateway_id: str | None, actor: User | None = None,
                   actor_label: str = "Payment gateway") -> str:
    """Marks a payment paid (idempotent) and confirms the booking if the required amount is covered.

    Only called from the verified ITN handler or by a manager recording a bank-verified manual payment.
    """
    if payment.status == "paid":
        return "duplicate"
    booking = payment.booking
    payment.status = "paid"
    payment.paid_at = utcnow()
    if gateway_id:
        payment.gateway_payment_id = gateway_id
    db.session.flush()

    required = booking.deposit_cents if booking.payment_mode == "deposit" and booking.deposit_cents else booking.final_price_cents
    covered = booking.amount_paid_cents >= (required or 0)
    note = f"Payment of R{payment.amount_cents / 100:,.2f} verified ({payment.m_payment_id})."
    if booking.status in {APPROVED, PROCESSING, EXPIRED} and covered:
        if booking.status == EXPIRED and booking.slot:
            # Late payment: only confirm if the capacity is still free.
            if remaining_capacity(booking.slot, exclude_booking_id=booking.id) < booking.riders:
                db.session.add(StatusHistory(booking=booking, from_status=booking.status, to_status=booking.status,
                                             actor_label=actor_label, note=note + " Capacity no longer available — "
                                             "staff must contact the customer about a new date or refund."))
                return "paid_needs_attention"
        transition(booking, CONFIRMED, actor, note=note, actor_label=None if actor else actor_label)
        return "confirmed"
    db.session.add(StatusHistory(booking=booking, from_status=booking.status, to_status=booking.status,
                                 changed_by_id=actor.id if actor else None,
                                 actor_label=(f"{actor.name}" if actor else actor_label), note=note))
    return "paid"


def expire_overdue(now: datetime | None = None) -> list[Booking]:
    """Release approved bookings whose payment deadline passed without the required payment."""
    now = now or utcnow()
    overdue = Booking.query.filter(Booking.status.in_([APPROVED, PROCESSING]),
                                   Booking.payment_deadline.isnot(None), Booking.payment_deadline < now).all()
    expired = []
    for b in overdue:
        if b.amount_paid_cents > 0:
            continue
        transition(b, EXPIRED, None, note="Payment deadline passed — provisional booking released.")
        expired.append(b)
    return expired


def extend_deadline(booking: Booking, admin: User, new_deadline: datetime) -> None:
    if new_deadline <= utcnow():
        raise WorkflowError("The new deadline must be in the future.")
    if booking.status == EXPIRED:
        if booking.slot:
            slot = lock_slot(booking.slot.id)
            ensure_capacity(booking, slot)
        transition(booking, APPROVED, admin, note=f"Payment deadline extended to {new_deadline:%d %b %Y %H:%M}.")
    elif booking.status not in {APPROVED, PROCESSING}:
        raise WorkflowError("Only bookings awaiting payment can have their deadline extended.")
    booking.payment_deadline = new_deadline


def reschedule(booking: Booking, admin: User, slot: Slot, note: str) -> None:
    if booking.status not in {CONFIRMED, RESCHED_REQ, RESCHEDULED}:
        raise WorkflowError("Only confirmed bookings can be rescheduled.")
    locked = lock_slot(slot.id)
    ensure_capacity(booking, locked)
    problems = horse_conflicts(booking, locked, list(booking.horses))
    if problems:
        booking.horses = []  # horses must be reassigned for the new session
    old = booking.slot.label if booking.slot else str(booking.preferred_date)
    booking.slot = locked
    booking.preferred_date = locked.date
    booking.preferred_time = locked.start_time
    booking.reminder_sent_at = None
    transition(booking, RESCHEDULED, admin, note=f"Moved from {old} to {locked.label}. {note}".strip())


def default_deadline(hours: int) -> datetime:
    return utcnow() + timedelta(hours=hours)
