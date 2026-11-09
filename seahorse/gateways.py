"""Payment gateways as a small class hierarchy (inheritance + polymorphism).

    PaymentGateway            (abstract parent: what every gateway must offer)
    ├── PaystackGateway       card checkout on Paystack's page (card number, expiry, CVV)
    └── PayfastGateway        signed form posted to PayFast
        └── SimulatorGateway  offline look-alike of PayFast for development

The payment route only ever does::

    gateway = get_gateway(current_app.config)
    return gateway.start_checkout(booking, payment)

Python looks up ``start_checkout`` on the object's own class at run time (dynamic dispatch), so the same
line sends the customer to Paystack, renders the PayFast form, or opens the simulator — without an ``if``.
Adding a new gateway (e.g. Yoco) means writing one subclass and adding it to ``GATEWAYS``.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from flask import current_app, flash, redirect, render_template, request, url_for

from . import payfast, paystack
from .extensions import db
from .models import Booking, Payment, PaymentEvent


def site_url(path: str) -> str:
    """Where the customer's browser comes back to (the address they are using)."""
    base = current_app.config.get("PUBLIC_SITE_URL") or request.host_url.rstrip("/")
    return base + path


def payfast_notify_url() -> str:
    """Where PayFast's servers send the payment notification — must be publicly reachable."""
    cfg = current_app.config
    if cfg.get("PAYFAST_NOTIFY_URL"):
        return cfg["PAYFAST_NOTIFY_URL"]
    base = cfg.get("PUBLIC_SITE_URL") or cfg.get("TUNNEL_URL") or request.host_url.rstrip("/")
    return base + url_for("payments.payfast_notify")


class PaymentGateway(ABC):
    """Parent class. Every gateway has a name and a label and must implement ``start_checkout``."""

    name: str = "gateway"          # stored in Payment.provider
    label: str = "Payment gateway"  # shown to customers ("Pay securely with …")

    def __init__(self, config: dict):
        self.config = config

    @property
    def ready(self) -> bool:
        """Can this gateway take payments right now? Children override when they need set-up."""
        return True

    def describe(self) -> str:
        """Shared behaviour, inherited unchanged by every child class."""
        return f"{self.label} — {'ready' if self.ready else 'not set up'}"

    @abstractmethod
    def start_checkout(self, booking: Booking, payment: Payment):
        """Send the customer to pay ``payment`` for ``booking``; returns a Flask response."""


class PaystackGateway(PaymentGateway):
    name = "paystack"
    label = "Paystack"

    def __init__(self, config: dict):
        super().__init__(config)                      # run the parent's constructor first
        self.cfg = paystack.config_from(config)

    @property
    def ready(self) -> bool:                          # overrides the parent: needs a secret key
        return self.cfg.configured

    def start_checkout(self, booking: Booking, payment: Payment):
        if not self.ready:
            current_app.logger.error("PAYMENT_PROVIDER=paystack but PAYSTACK_SECRET_KEY is missing or invalid")
            flash("Online card payment isn't available right now. Please try again later or contact us.", "error")
            return redirect(url_for("booking.view", ref=booking.reference))
        reference = paystack.attempt_reference(payment.m_payment_id)
        try:
            url = paystack.initialize(
                self.cfg, email=booking.contact_email, amount_cents=payment.amount_cents, reference=reference,
                callback_url=site_url(url_for("payments.paystack_callback")),
                cancel_url=site_url(url_for("payments.cancelled", ref=booking.reference)),
                metadata={"booking_reference": booking.reference, "payment_id": payment.m_payment_id,
                          "kind": payment.kind,
                          "custom_fields": [{"display_name": "Booking", "variable_name": "booking",
                                             "value": f"{booking.reference} — {booking.display_name}"}]})
        except paystack.PaystackError as exc:
            db.session.add(PaymentEvent(payment_id=payment.id, outcome="checkout_error", detail=str(exc)[:250],
                                        remote_ip="(outgoing)"))
            db.session.commit()
            flash("We couldn't open the secure payment page just now. Nothing was charged — please try again in a "
                  "minute.", "error")
            return redirect(url_for("booking.view", ref=booking.reference))
        return redirect(url, code=303)                # Paystack's card page


class PayfastGateway(PaymentGateway):
    name = "payfast"
    label = "PayFast"

    def __init__(self, config: dict):
        super().__init__(config)
        self.cfg = payfast.config_from(config)

    def checkout_fields(self, booking: Booking, payment: Payment) -> dict:
        return payfast.build_checkout(
            self.cfg, m_payment_id=payment.m_payment_id, amount_cents=payment.amount_cents,
            item_name=f"Seahorse Ride {booking.reference} ({payment.kind})", item_description=booking.display_name,
            customer_name=booking.contact_name, email=booking.contact_email, booking_ref=booking.reference,
            return_url=site_url(url_for("payments.returned", ref=booking.reference)),
            cancel_url=site_url(url_for("payments.cancelled", ref=booking.reference)),
            notify_url=payfast_notify_url())

    def start_checkout(self, booking: Booking, payment: Payment):
        from .routes.payments import notify_reachable
        return render_template("account/pay.html", b=booking, reason=None, payment=payment,
                               checkout=self.checkout_fields(booking, payment), can_confirm=notify_reachable())


class SimulatorGateway(PayfastGateway):
    """Grandchild class: reuses PayFast's signing and checkout form (inherited), but is never allowed live."""

    name = "simulator"
    label = "Offline simulator"

    def __init__(self, config: dict):
        if config.get("APP_ENV") == "production":
            raise RuntimeError("The payment simulator cannot run in production.")
        super().__init__(config)                      # PayfastGateway.__init__ → PaymentGateway.__init__


# Registry: provider name → class. A dictionary gives O(1) lookup and replaces an if/elif chain.
GATEWAYS: dict[str, type[PaymentGateway]] = {
    PaystackGateway.name: PaystackGateway,
    PayfastGateway.name: PayfastGateway,
    SimulatorGateway.name: SimulatorGateway,
}


def get_gateway(config: dict) -> PaymentGateway:
    """Factory: build the gateway object for the configured provider (PayFast if the name is unknown)."""
    return GATEWAYS.get(config.get("PAYMENT_PROVIDER", "payfast"), PayfastGateway)(config)
