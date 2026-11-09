"""Paystack integration (South Africa, ZAR) — hosted checkout where the customer types card number, expiry and CVV.

Flow
1. Only after admin approval, the customer clicks Pay. The server asks Paystack (with the SECRET key, server to
   server) to start a transaction for the exact amount due and gets back Paystack's checkout address.
   The customer is sent there and enters the card details on Paystack's own page — they never touch this site.
2. Paystack sends the browser back to ``/payments/paystack/callback?reference=…``. That redirect proves nothing on
   its own, so the server asks Paystack's API for the transaction ("verify") and only trusts that answer.
3. Paystack also POSTs a webhook (``charge.success``) to ``/payments/paystack/webhook``. We check its
   HMAC-SHA512 signature (keyed with the secret key) before using it. Whichever arrives first confirms the booking;
   the second is ignored (idempotent).

Every confirmation checks: reference belongs to a pending payment → status is "success" → currency →
exact amount in cents. Test mode uses ``sk_test_…`` keys and Paystack's test cards — no real money moves.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Callable

import requests

API = "https://api.paystack.co"
# Paystack sends webhooks only from these addresses (same for test and live).
WEBHOOK_IPS = {"52.31.139.75", "52.49.173.169", "52.214.14.220"}


class PaystackError(Exception):
    """Paystack refused a request or could not be reached. The message is safe to show staff."""


@dataclass
class PaystackConfig:
    secret_key: str
    public_key: str = ""
    currency: str = "ZAR"

    @property
    def configured(self) -> bool:
        return self.secret_key.startswith(("sk_test_", "sk_live_"))

    @property
    def test_mode(self) -> bool:
        return self.secret_key.startswith("sk_test_")

    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.secret_key}", "Content-Type": "application/json",
                "User-Agent": "SeahorseRide/1.0"}


def config_from(app_config: dict) -> PaystackConfig:
    return PaystackConfig(secret_key=(app_config.get("PAYSTACK_SECRET_KEY") or "").strip(),
                          public_key=(app_config.get("PAYSTACK_PUBLIC_KEY") or "").strip(),
                          currency=(app_config.get("PAYSTACK_CURRENCY") or "ZAR").strip().upper())


# --------------------------------------------------------------------------- references
def attempt_reference(m_payment_id: str) -> str:
    """Paystack needs a new reference for every checkout attempt; ours = payment id + attempt stamp."""
    return f"{m_payment_id}-T{int(time.time() * 1000)}"


def payment_id_from_reference(reference: str) -> str:
    return reference.rsplit("-T", 1)[0] if "-T" in reference else reference


# --------------------------------------------------------------------------- API calls
def _call(method: str, url: str, cfg: PaystackConfig, **kw) -> dict:
    try:
        resp = requests.request(method, url, headers=cfg.headers(), timeout=20, **kw)
    except requests.RequestException as exc:
        raise PaystackError(f"Could not reach Paystack ({exc.__class__.__name__}).") from exc
    try:
        body = resp.json()
    except ValueError:
        raise PaystackError(f"Unexpected reply from Paystack (HTTP {resp.status_code}).")
    if resp.status_code >= 400 or not body.get("status"):
        raise PaystackError(body.get("message") or f"Paystack error (HTTP {resp.status_code}).")
    return body.get("data") or {}


def initialize(cfg: PaystackConfig, *, email: str, amount_cents: int, reference: str, callback_url: str,
               cancel_url: str, metadata: dict) -> str:
    """Start a transaction; returns Paystack's checkout URL (card number, expiry, CVV are typed there)."""
    meta = dict(metadata)
    meta["cancel_action"] = cancel_url  # where Paystack's "Cancel payment" button sends the customer
    data = _call("POST", f"{API}/transaction/initialize", cfg, json={
        "email": email, "amount": int(amount_cents), "currency": cfg.currency, "reference": reference,
        "callback_url": callback_url, "channels": ["card"], "metadata": meta})
    url = data.get("authorization_url")
    if not url or not url.startswith("https://"):
        raise PaystackError("Paystack did not return a checkout page.")
    return url


def verify(cfg: PaystackConfig, reference: str) -> dict:
    """Ask Paystack directly what happened to this transaction (the only answer we trust)."""
    safe = "".join(ch for ch in reference if ch.isalnum() or ch in "-_.=")
    return _call("GET", f"{API}/transaction/verify/{safe}", cfg)


def valid_signature(cfg: PaystackConfig, raw_body: bytes, header: str | None) -> bool:
    if not header or not cfg.secret_key:
        return False
    expected = hmac.new(cfg.secret_key.encode(), raw_body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected, header.strip().lower())


# --------------------------------------------------------------------------- applying a result
@dataclass
class Result:
    outcome: str
    detail: str = ""


def apply_transaction(data: dict, cfg: PaystackConfig, *, find_payment: Callable, on_complete: Callable,
                      on_failed: Callable, log: Callable) -> Result:
    """Checks a Paystack transaction (from verify or a signed webhook) and applies it. Pure, so it is unit tested."""
    payload = json.dumps(data, default=str)[:4000]
    reference = str(data.get("reference") or "")
    payment = find_payment(payment_id_from_reference(reference))
    if payment is None:
        log("rejected_unknown", None, f"Unknown reference {reference}", payload)
        return Result("rejected_unknown")
    status = str(data.get("status") or "").lower()
    if status == "success":
        if str(data.get("currency") or "").upper() != cfg.currency:
            log("rejected_currency", payment, f"Currency {data.get('currency')} is not {cfg.currency}", payload)
            return Result("rejected_currency")
        try:
            amount = int(data.get("amount"))
        except (TypeError, ValueError):
            amount = -1
        if amount != payment.amount_cents:
            log("rejected_amount", payment, f"Amount {amount} does not match expected {payment.amount_cents}", payload)
            return Result("rejected_amount")
        outcome = on_complete(payment, f"PS-{data.get('id') or reference}")
        log(f"complete_{outcome}", payment, "Payment verified with Paystack", payload)
        return Result(outcome)
    if status in {"failed", "reversed"}:
        on_failed(payment, "failed")
        log("payment_failed", payment, f"Paystack reported {status}", payload)
        return Result("failed", str(data.get("gateway_response") or ""))
    log("ignored_status", payment, f"Status {status or 'unknown'}", payload)
    return Result("pending" if status in {"ongoing", "pending", "processing", "queued"} else "not_paid",
                  str(data.get("gateway_response") or ""))
