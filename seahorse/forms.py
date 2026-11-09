"""Small, explicit server-side validation helpers (every form is validated here, never only in the browser)."""
from __future__ import annotations

import re
from datetime import date, datetime

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")
PHONE_RE = re.compile(r"^[+0-9 ()\-]{7,30}$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

AGE_GROUPS = [("under-8", "Under 8", 0), ("8-11", "8–11", 8), ("12-15", "12–15", 12), ("16-17", "16–17", 16),
              ("18+", "18 and over", 18)]
AGE_MIN = {k: m for k, _, m in AGE_GROUPS}
EXPERIENCE_LEVELS = [("never", "Never ridden"), ("some", "A few rides"), ("confident", "Confident rider")]
EVENT_TYPES = ["Birthday", "Anniversary", "Proposal", "Family gathering", "Friends' outing",
               "School or youth group", "Tourist group", "Corporate event", "Private experience", "Other"]
CONTACT_METHODS = ["email", "phone", "whatsapp"]


class Form:
    def __init__(self, data):
        self.data = data
        self.errors: dict[str, str] = {}

    def text(self, name: str, label: str, required=True, min_len=0, max_len=200) -> str:
        value = (self.data.get(name) or "").strip()
        if not value:
            if required:
                self.errors[name] = f"{label} is required."
            return ""
        if len(value) < min_len:
            self.errors[name] = f"{label} must be at least {min_len} characters."
        elif len(value) > max_len:
            self.errors[name] = f"{label} must be at most {max_len} characters."
        return value

    def email(self, name="email", label="Email") -> str:
        value = self.text(name, label, max_len=254).lower()
        if value and not EMAIL_RE.match(value):
            self.errors[name] = "Enter a valid email address."
        return value

    def phone(self, name="phone", label="Mobile number", required=True) -> str:
        value = self.text(name, label, required=required, max_len=30)
        if value and not PHONE_RE.match(value):
            self.errors[name] = "Enter a valid phone number (digits, spaces, + and brackets only)."
        return value

    def integer(self, name: str, label: str, lo: int, hi: int, required=True, default: int | None = None) -> int | None:
        raw = (self.data.get(name) or "").strip()
        if raw == "":
            if required and default is None:
                self.errors[name] = f"{label} is required."
            return default
        try:
            value = int(raw)
        except ValueError:
            self.errors[name] = f"{label} must be a whole number."
            return default
        if not lo <= value <= hi:
            self.errors[name] = f"{label} must be between {lo} and {hi}."
        return value

    def money_cents(self, name: str, label: str, required=True) -> int | None:
        raw = (self.data.get(name) or "").strip().replace(" ", "").replace(",", "").lstrip("R")
        if raw == "":
            if required:
                self.errors[name] = f"{label} is required."
            return None
        try:
            value = round(float(raw) * 100)
        except ValueError:
            self.errors[name] = f"{label} must be an amount like 1250.00."
            return None
        if value < 0 or value > 100_000_000:
            self.errors[name] = f"{label} is out of range."
        return value

    def date(self, name: str, label: str, required=True, future=False) -> date | None:
        raw = (self.data.get(name) or "").strip()
        if not raw:
            if required:
                self.errors[name] = f"{label} is required."
            return None
        try:
            value = datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            self.errors[name] = f"{label} must be a valid date."
            return None
        if future:
            from .models import today
            if value <= today():
                self.errors[name] = f"{label} must be from tomorrow onwards."
            elif (value - today()).days > 400:
                self.errors[name] = f"{label} is too far ahead — contact us for bookings over a year away."
        return value

    def datetime_local(self, name: str, label: str) -> datetime | None:
        raw = (self.data.get(name) or "").strip()
        if not raw:
            self.errors[name] = f"{label} is required."
            return None
        try:
            return datetime.strptime(raw, "%Y-%m-%dT%H:%M")
        except ValueError:
            self.errors[name] = f"{label} must be a valid date and time."
            return None

    def time(self, name: str, label: str, required=False) -> str | None:
        value = (self.data.get(name) or "").strip()
        if not value:
            if required:
                self.errors[name] = f"{label} is required."
            return None
        if not TIME_RE.match(value):
            self.errors[name] = f"{label} must look like 09:30."
        return value

    def choice(self, name: str, label: str, options, required=True) -> str:
        value = (self.data.get(name) or "").strip()
        if not value:
            if required:
                self.errors[name] = f"Choose {label.lower()}."
            return ""
        if value not in options:
            self.errors[name] = f"Choose a valid {label.lower()}."
        return value

    def checked(self, name: str) -> bool:
        return self.data.get(name) in {"on", "1", "true", "yes"}

    @property
    def ok(self) -> bool:
        return not self.errors
