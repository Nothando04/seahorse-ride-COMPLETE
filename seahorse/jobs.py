"""Scheduled jobs: payment-deadline expiry, ride reminders and email retries.

Run automatically every few minutes while the site is being used, and via ``flask run-jobs`` from cron
(recommended in production, e.g. every 15 minutes).
"""
from __future__ import annotations

from datetime import timedelta

from .extensions import db
from .models import Booking, utcnow
from .settings import int_setting
from .workflow import CONFIRMED, RESCHEDULED, expire_overdue
from . import notify


def run_jobs() -> dict:
    expired = expire_overdue()
    for b in expired:
        notify.expired(b)
    db.session.commit()

    hours = int_setting("reminder_hours", 24)
    now = utcnow()
    soon = (now + timedelta(hours=hours)).date()
    upcoming = Booking.query.filter(Booking.status.in_([CONFIRMED, RESCHEDULED]), Booking.reminder_sent_at.is_(None),
                                    Booking.preferred_date >= now.date(), Booking.preferred_date <= soon).all()
    for b in upcoming:
        notify.reminder(b)
        b.reminder_sent_at = now
    db.session.commit()
    sent = notify.deliver_pending()
    return {"expired": len(expired), "reminders": len(upcoming), "emails_sent": sent}
