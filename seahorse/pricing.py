"""Indicative price estimates. The final price is always set by an administrator on approval."""
from __future__ import annotations

from .models import AddOn, Experience


def estimate_cents(experience: Experience | None, riders: int, addons: list[AddOn]) -> int | None:
    """Returns an estimate in cents, or None when any part is priced on request."""
    if experience is None or experience.price_cents is None or experience.price_basis == "quote":
        return None
    riders = max(int(riders or 0), 0)
    total = experience.price_cents * riders if experience.price_basis == "per_person" else experience.price_cents
    for a in addons:
        if a.price_cents is None:
            return None
        total += a.price_cents * riders if a.price_basis == "per_person" else a.price_cents
    return total


def price_label(price_cents: int | None, basis: str) -> str:
    if price_cents is None or basis == "quote":
        return "Price on request"
    amount = f"R{price_cents / 100:,.0f}".replace(",", " ")
    return {"per_person": f"{amount} per rider", "per_group": f"{amount} per group",
            "per_booking": f"{amount} per booking"}.get(basis, amount)
