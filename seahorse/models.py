"""Database models for Seahorse Ride.

Money is always stored as integer cents (ZAR by default) to avoid rounding errors.
"""
from __future__ import annotations

import json
import secrets
from datetime import date, datetime, timezone

from sqlalchemy import CheckConstraint, Index, UniqueConstraint
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db


def utcnow() -> datetime:
    # Naive UTC timestamps keep SQLite and PostgreSQL behaviour identical.
    return datetime.now(timezone.utc).replace(tzinfo=None)


# --------------------------------------------------------------------------- roles
ROLES = ["customer", "guide", "booking_admin", "manager", "super_admin"]
ROLE_LABELS = {
    "customer": "Customer",
    "guide": "Staff / Guide",
    "booking_admin": "Booking Administrator",
    "manager": "Manager",
    "super_admin": "Super Administrator",
}
# Least-privilege permission sets. Every admin route checks one of these on the server.
PERMISSIONS = {
    "guide": {"view_rides", "check_in"},
    "booking_admin": {"view_rides", "check_in", "manage_requests", "approve", "manage_availability",
                      "message", "manage_horses"},
    "manager": {"view_rides", "check_in", "manage_requests", "approve", "manage_availability", "message",
                "manage_horses", "manage_packages", "reports", "refunds", "manual_payments", "gallery",
                "reviews", "waivers", "weather"},
    "super_admin": {"*"},
}


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(254), unique=True, nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(30))
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="customer")
    email_verified = db.Column(db.Boolean, nullable=False, default=False)
    marketing_opt_in = db.Column(db.Boolean, nullable=False, default=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    last_login_at = db.Column(db.DateTime)

    __table_args__ = (CheckConstraint(f"role in ({', '.join(repr(r) for r in ROLES)})", name="role_valid"),)

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    @property
    def is_staff(self) -> bool:
        return self.role != "customer"

    def can(self, perm: str) -> bool:
        perms = PERMISSIONS.get(self.role, set())
        return "*" in perms or perm in perms

    @property
    def role_label(self) -> str:
        return ROLE_LABELS.get(self.role, self.role)


# --------------------------------------------------------------------------- catalogue
class Experience(db.Model):
    """A ride or a package. Admins manage everything here; prices may be empty ("quote on request")."""

    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(80), unique=True, nullable=False)
    kind = db.Column(db.String(12), nullable=False, default="package")  # ride | package
    name = db.Column(db.String(120), nullable=False)
    summary = db.Column(db.String(300), nullable=False, default="")
    description = db.Column(db.Text, nullable=False, default="")
    duration_minutes = db.Column(db.Integer)
    price_cents = db.Column(db.Integer)  # None = price confirmed on quote
    price_basis = db.Column(db.String(12), nullable=False, default="per_person")  # per_person | per_group | quote
    min_group = db.Column(db.Integer, nullable=False, default=1)
    max_group = db.Column(db.Integer)
    min_age = db.Column(db.Integer)
    difficulty = db.Column(db.String(30), default="All levels")
    requirements = db.Column(db.Text, default="")
    inclusions = db.Column(db.Text, default="")  # one per line
    exclusions = db.Column(db.Text, default="")
    terms = db.Column(db.Text, default="")
    safety_notes = db.Column(db.Text, default="")
    availability_note = db.Column(db.String(200), default="")
    image_path = db.Column(db.String(255))
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    is_featured = db.Column(db.Boolean, nullable=False, default=False)
    sort_order = db.Column(db.Integer, nullable=False, default=100)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    __table_args__ = (
        CheckConstraint("price_cents is null or price_cents >= 0", name="price_nonneg"),
        CheckConstraint("min_group >= 1", name="min_group_pos"),
    )

    @staticmethod
    def lines(text: str | None) -> list[str]:
        return [ln.strip(" -•\t") for ln in (text or "").splitlines() if ln.strip(" -•\t")]


class AddOn(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.String(300), default="")
    price_cents = db.Column(db.Integer)
    price_basis = db.Column(db.String(12), nullable=False, default="per_booking")  # per_booking | per_person
    is_active = db.Column(db.Boolean, nullable=False, default=True)


# --------------------------------------------------------------------------- operations
class Slot(db.Model):
    """A ride session. Capacity = maximum riders. Blocked slots accept no bookings."""

    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, index=True)
    start_time = db.Column(db.String(5), nullable=False)  # HH:MM
    end_time = db.Column(db.String(5), nullable=False)
    capacity = db.Column(db.Integer, nullable=False, default=8)
    status = db.Column(db.String(12), nullable=False, default="open")  # open | blocked
    block_reason = db.Column(db.String(200))  # weather closure, private event, horse rest ...
    experience_id = db.Column(db.Integer, db.ForeignKey("experience.id"))  # optional: slot only for one experience
    experience = db.relationship("Experience")

    __table_args__ = (
        UniqueConstraint("date", "start_time", name="uq_slot_date_start"),
        CheckConstraint("capacity >= 0", name="capacity_nonneg"),
    )

    @property
    def label(self) -> str:
        return f"{self.date:%a %d %b %Y} · {self.start_time}–{self.end_time}"


booking_horses = db.Table(
    "booking_horses",
    db.Column("booking_id", db.Integer, db.ForeignKey("booking.id", ondelete="CASCADE"), primary_key=True),
    db.Column("horse_id", db.Integer, db.ForeignKey("horse.id", ondelete="CASCADE"), primary_key=True),
)


HORSE_TYPES = {
    "kids": ("Kid-friendly ponies", "Gentle, smaller ponies for children (led by a guide)."),
    "nervous": ("Extra-calm horses", "Our steadiest horses for nervous or first-time riders."),
    "beginner": ("Beginner-friendly horses", "Calm, experienced beach horses for most new riders."),
    "confident": ("Horses for confident riders", "More forward-going horses for riders who can trot and canter."),
    "sturdy": ("Sturdy horses for taller riders", "Larger, strong horses suited to taller or heavier riders."),
}


class Horse(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    category = db.Column(db.String(20), nullable=False, default="beginner", server_default="beginner")
    temperament = db.Column(db.String(200), default="")
    suitable_for = db.Column(db.String(200), default="")  # e.g. "Beginners, children (with guide)"
    max_rider_weight_kg = db.Column(db.Integer)
    status = db.Column(db.String(20), nullable=False, default="available")  # available | resting | maintenance | retired
    notes = db.Column(db.Text, default="")
    photo_path = db.Column(db.String(255))
    show_publicly = db.Column(db.Boolean, nullable=False, default=True)
    # Public profile ("Meet our horses")
    breed = db.Column(db.String(80))
    colour = db.Column(db.String(40))  # e.g. Bay, Chestnut, Grey, Palomino, Black, Dun
    sex = db.Column(db.String(10))  # mare | gelding | stallion
    birth_year = db.Column(db.Integer)
    height_hands = db.Column(db.String(10))  # horsemen's notation, e.g. "15.2" = 15 hands 2 inches
    joined_year = db.Column(db.Integer)
    history = db.Column(db.Text)
    fun_fact = db.Column(db.String(300))
    favourite_treat = db.Column(db.String(80))

    @property
    def age(self) -> int | None:
        return (today().year - self.birth_year) if self.birth_year else None

    @property
    def height_cm(self) -> int | None:
        """Hands to centimetres (1 hand = 4 in = 10.16 cm; the digit after the point is inches)."""
        try:
            hands, _, inches = (self.height_hands or "").partition(".")
            return round((int(hands) * 4 + int(inches or 0)) * 2.54)
        except ValueError:
            return None


# --------------------------------------------------------------------------- bookings
class Booking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    reference = db.Column(db.String(16), unique=True, nullable=False, index=True)
    access_key = db.Column(db.String(64), unique=True, nullable=False, default=lambda: secrets.token_urlsafe(32))
    kind = db.Column(db.String(10), nullable=False, default="booking")  # booking | event
    status = db.Column(db.String(40), nullable=False, default="Pending Admin Approval", index=True)

    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), index=True)
    user = db.relationship("User", foreign_keys=[user_id], backref="bookings")

    experience_id = db.Column(db.Integer, db.ForeignKey("experience.id"))
    experience = db.relationship("Experience")
    slot_id = db.Column(db.Integer, db.ForeignKey("slot.id"), index=True)
    slot = db.relationship("Slot", backref="bookings")

    preferred_date = db.Column(db.Date, nullable=False, index=True)
    preferred_time = db.Column(db.String(5))
    alternative_date = db.Column(db.Date)
    adults = db.Column(db.Integer, nullable=False, default=1)
    children = db.Column(db.Integer, nullable=False, default=0)
    riders = db.Column(db.Integer, nullable=False, default=1)
    spectators = db.Column(db.Integer, nullable=False, default=0)
    rider_details = db.Column(db.Text, default="[]")  # JSON list of {age_group, experience}
    addon_ids = db.Column(db.Text, default="[]")  # JSON list

    # Event / enquiry fields
    event_type = db.Column(db.String(60))
    location = db.Column(db.String(200))
    horse_preference = db.Column(db.String(20))  # a HORSE_TYPES key, or None for no preference
    budget = db.Column(db.String(60))
    duration_wish = db.Column(db.String(60))
    activities = db.Column(db.Text)
    attachment_path = db.Column(db.String(255))

    contact_name = db.Column(db.String(120), nullable=False)
    contact_email = db.Column(db.String(254), nullable=False, index=True)
    contact_phone = db.Column(db.String(30), nullable=False)
    preferred_contact = db.Column(db.String(20), default="email")
    accessibility = db.Column(db.Text)
    special_requests = db.Column(db.Text)
    terms_accepted_at = db.Column(db.DateTime)

    # Pricing (set by admin on approval)
    estimate_cents = db.Column(db.Integer)
    final_price_cents = db.Column(db.Integer)
    currency = db.Column(db.String(3), nullable=False, default="ZAR")
    payment_mode = db.Column(db.String(10), default="full")  # full | deposit
    deposit_cents = db.Column(db.Integer)
    payment_deadline = db.Column(db.DateTime)
    approval_note = db.Column(db.Text)  # explanation of price / changes shown to customer
    approved_at = db.Column(db.DateTime)
    approved_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    guide_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    guide = db.relationship("User", foreign_keys=[guide_id])
    horses = db.relationship("Horse", secondary=booking_horses, backref="bookings")
    reminder_sent_at = db.Column(db.DateTime)
    checked_in_at = db.Column(db.DateTime)

    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        CheckConstraint("riders >= 0 and adults >= 0 and children >= 0 and spectators >= 0", name="counts_nonneg"),
        CheckConstraint("final_price_cents is null or final_price_cents >= 0", name="final_nonneg"),
        Index("ix_booking_status_date", "status", "preferred_date"),
    )

    # -- helpers
    @property
    def rider_list(self) -> list[dict]:
        try:
            return json.loads(self.rider_details or "[]")
        except ValueError:
            return []

    @property
    def addon_id_list(self) -> list[int]:
        try:
            return [int(x) for x in json.loads(self.addon_ids or "[]")]
        except ValueError:
            return []

    @property
    def addons(self) -> list[AddOn]:
        ids = self.addon_id_list
        return AddOn.query.filter(AddOn.id.in_(ids)).all() if ids else []

    @property
    def group_size(self) -> int:
        return (self.adults or 0) + (self.children or 0)

    @property
    def amount_paid_cents(self) -> int:
        return sum(p.amount_cents for p in self.payments if p.status == "paid")

    @property
    def amount_refunded_cents(self) -> int:
        return sum(r.amount_cents for r in self.refunds if r.status == "completed")

    @property
    def amount_due_now_cents(self) -> int:
        """What the customer must pay next (deposit first, then balance)."""
        if not self.final_price_cents:
            return 0
        paid = self.amount_paid_cents
        if self.payment_mode == "deposit" and self.deposit_cents and paid < self.deposit_cents:
            return self.deposit_cents - paid
        return max(self.final_price_cents - paid, 0)

    @property
    def balance_cents(self) -> int:
        return max((self.final_price_cents or 0) - self.amount_paid_cents, 0)

    @property
    def display_name(self) -> str:
        if self.experience:
            return self.experience.name
        return (self.event_type or "Custom enquiry").replace("-", " ").title()


class StatusHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, db.ForeignKey("booking.id", ondelete="CASCADE"), nullable=False, index=True)
    booking = db.relationship("Booking", backref=db.backref("history", order_by="StatusHistory.id", cascade="all, delete-orphan"))
    from_status = db.Column(db.String(40))
    to_status = db.Column(db.String(40), nullable=False)
    changed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    changed_by = db.relationship("User")
    actor_label = db.Column(db.String(60), default="System")
    note = db.Column(db.Text)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, db.ForeignKey("booking.id", ondelete="CASCADE"), nullable=False, index=True)
    booking = db.relationship("Booking", backref=db.backref("messages", order_by="Message.id", cascade="all, delete-orphan"))
    sender = db.Column(db.String(10), nullable=False)  # customer | admin | system
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    user = db.relationship("User")
    body = db.Column(db.Text, nullable=False)
    internal = db.Column(db.Boolean, nullable=False, default=False)  # private admin note, never shown to customer
    read_by_customer = db.Column(db.Boolean, nullable=False, default=False)
    read_by_admin = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class Payment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, db.ForeignKey("booking.id"), nullable=False, index=True)
    booking = db.relationship("Booking", backref=db.backref("payments", order_by="Payment.id"))
    m_payment_id = db.Column(db.String(40), unique=True, nullable=False)  # our reference sent to the gateway
    provider = db.Column(db.String(20), nullable=False, default="payfast")  # payfast | simulator | manual
    kind = db.Column(db.String(10), nullable=False, default="full")  # full | deposit | balance
    amount_cents = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(12), nullable=False, default="pending")  # pending | processing | paid | failed | cancelled
    gateway_payment_id = db.Column(db.String(60), unique=True)
    method_note = db.Column(db.String(200))
    recorded_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    paid_at = db.Column(db.DateTime)

    __table_args__ = (CheckConstraint("amount_cents > 0", name="amount_pos"),)


class PaymentEvent(db.Model):
    """Every gateway notification (ITN) is logged, accepted or not."""

    id = db.Column(db.Integer, primary_key=True)
    payment_id = db.Column(db.Integer, db.ForeignKey("payment.id"))
    outcome = db.Column(db.String(30), nullable=False)
    detail = db.Column(db.String(300))
    payload = db.Column(db.Text)
    remote_ip = db.Column(db.String(64))
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class Refund(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, db.ForeignKey("booking.id"), nullable=False, index=True)
    booking = db.relationship("Booking", backref=db.backref("refunds", order_by="Refund.id"))
    amount_cents = db.Column(db.Integer, nullable=False)
    reason = db.Column(db.String(300))
    status = db.Column(db.String(12), nullable=False, default="pending")  # pending | completed
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    completed_at = db.Column(db.DateTime)


# --------------------------------------------------------------------------- comms
class Notification(db.Model):
    """Email outbox. Every email is stored first, then sent; failures are retried."""

    id = db.Column(db.Integer, primary_key=True)
    to_email = db.Column(db.String(254), nullable=False)
    subject = db.Column(db.String(200), nullable=False)
    body_text = db.Column(db.Text, nullable=False)
    channel = db.Column(db.String(10), nullable=False, default="email")
    status = db.Column(db.String(10), nullable=False, default="queued")  # queued | sent | failed | logged
    attempts = db.Column(db.Integer, nullable=False, default=0)
    last_error = db.Column(db.String(300))
    booking_id = db.Column(db.Integer, db.ForeignKey("booking.id"))
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    sent_at = db.Column(db.DateTime)


class Enquiry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(254), nullable=False)
    phone = db.Column(db.String(30))
    subject = db.Column(db.String(150), nullable=False)
    message = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(10), nullable=False, default="new")  # new | replied | closed
    reply = db.Column(db.Text)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)


# --------------------------------------------------------------------------- waivers, reviews, gallery
class Waiver(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    version = db.Column(db.Integer, nullable=False, unique=True)
    title = db.Column(db.String(150), nullable=False)
    body = db.Column(db.Text, nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class WaiverSignature(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, db.ForeignKey("booking.id", ondelete="CASCADE"), nullable=False, index=True)
    booking = db.relationship("Booking", backref=db.backref("signatures", cascade="all, delete-orphan"))
    waiver_id = db.Column(db.Integer, db.ForeignKey("waiver.id"), nullable=False)
    waiver = db.relationship("Waiver")
    rider_name = db.Column(db.String(120), nullable=False)
    rider_is_minor = db.Column(db.Boolean, nullable=False, default=False)
    signer_name = db.Column(db.String(120), nullable=False)
    emergency_name = db.Column(db.String(120), nullable=False)
    emergency_phone = db.Column(db.String(30), nullable=False)
    medical_notes = db.Column(db.String(500))
    ip_address = db.Column(db.String(64))
    signed_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class Review(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, db.ForeignKey("booking.id"), unique=True, nullable=False)
    booking = db.relationship("Booking", backref=db.backref("review", uselist=False))
    display_name = db.Column(db.String(60), nullable=False)
    rating = db.Column(db.Integer, nullable=False)
    comment = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(10), nullable=False, default="pending")  # pending | approved | hidden
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    __table_args__ = (CheckConstraint("rating between 1 and 5", name="rating_range"),)


class GalleryImage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    path = db.Column(db.String(255), nullable=False)
    caption = db.Column(db.String(200), default="")
    category = db.Column(db.String(30), default="rides")
    consent_confirmed = db.Column(db.Boolean, nullable=False, default=False)
    is_published = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)


# --------------------------------------------------------------------------- settings & audit
class Setting(db.Model):
    key = db.Column(db.String(60), primary_key=True)
    value = db.Column(db.Text, nullable=False, default="")


class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    user = db.relationship("User")
    action = db.Column(db.String(60), nullable=False)
    entity = db.Column(db.String(40))
    entity_id = db.Column(db.String(40))
    detail = db.Column(db.Text)
    ip_address = db.Column(db.String(64))
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)


def today() -> date:
    return utcnow().date()
