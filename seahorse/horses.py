"""Horse availability by type — overall and per ride session."""
from __future__ import annotations

from collections import Counter

from .extensions import db
from .models import HORSE_TYPES, Booking, Horse, Slot, booking_horses
from .workflow import HOLDS_CAPACITY


def available_by_type() -> dict[str, int]:
    """Horses currently in work (not resting, in maintenance or retired), counted per type."""
    counts = Counter(h.category for h in Horse.query.filter_by(status="available").all())
    return {k: counts.get(k, 0) for k in HORSE_TYPES}


def free_by_type(slot: Slot, base: dict[str, int] | None = None) -> dict[str, int]:
    """Horses of each type still free in a session (minus horses assigned to approved/confirmed bookings)."""
    base = dict(base or available_by_type())
    used = (db.session.query(Horse.category).join(booking_horses, booking_horses.c.horse_id == Horse.id)
            .join(Booking, Booking.id == booking_horses.c.booking_id)
            .filter(Booking.slot_id == slot.id, Booking.status.in_(HOLDS_CAPACITY), Horse.status == "available").all())
    for (cat,) in used:
        base[cat] = max(base.get(cat, 0) - 1, 0)
    return base


def type_summary() -> list[dict]:
    counts = available_by_type()
    total = Counter(h.category for h in Horse.query.filter(Horse.status != "retired").all())
    return [{"key": k, "label": label, "about": about, "available": counts.get(k, 0), "total": total.get(k, 0)}
            for k, (label, about) in HORSE_TYPES.items()]
