"""PayFast integration (South Africa) — hosted checkout + verified ITN (Instant Transaction Notification).

Flow
1. Only after admin approval, the customer opens the payment page. The server creates a pending Payment
   with a unique ``m_payment_id`` and returns a signed form that the browser posts to PayFast.
   Card details are entered on PayFast's page and never touch this site.
2. PayFast POSTs an ITN to ``/payments/payfast/notify``. We verify, in order: signature → merchant ID →
   PayFast server confirmation ("VALID") → payment reference → amount → status. Only then is the booking
   marked paid. Duplicate notifications are ignored (idempotent). The browser "return" redirect is never trusted.

``PAYMENT_PROVIDER=simulator`` replaces PayFast with a local look-alike page for development, because PayFast
cannot reach ``localhost``. It uses the same signing and the same ITN handler (only the remote "VALID"
confirmation is skipped). It refuses to run when ``APP_ENV=production``.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Callable
from urllib.parse import quote_plus

import requests

Pairs = list[tuple[str, str]]

SANDBOX_HOST = "sandbox.payfast.co.za"
LIVE_HOST = "www.payfast.co.za"


@dataclass
class PayfastConfig:
    provider: str  # payfast | simulator
    merchant_id: str
    merchant_key: str
    passphrase: str
    sandbox: bool
    site_url: str
    notify_url: str | None = None

    @property
    def host(self) -> str:
        return SANDBOX_HOST if self.sandbox else LIVE_HOST

    @property
    def process_url(self) -> str:
        return f"https://{self.host}/eng/process"

    @property
    def validate_url(self) -> str:
        return f"https://{self.host}/eng/query/validate"


def config_from(app_config: dict) -> PayfastConfig:
    return PayfastConfig(
        provider=app_config.get("PAYMENT_PROVIDER", "payfast"),
        merchant_id=app_config.get("PAYFAST_MERCHANT_ID", ""),
        merchant_key=app_config.get("PAYFAST_MERCHANT_KEY", ""),
        passphrase=app_config.get("PAYFAST_PASSPHRASE", ""),
        sandbox=bool(app_config.get("PAYFAST_SANDBOX", True)),
        site_url=(app_config.get("PUBLIC_SITE_URL") or "").rstrip("/"),
        notify_url=app_config.get("PAYFAST_NOTIFY_URL") or None,
    )


# --------------------------------------------------------------------------- signing
def pf_encode(value: str) -> str:
    """PHP ``urlencode`` semantics (uppercase hex, spaces as '+', '~' encoded) as PayFast requires."""
    return quote_plus(str(value).strip(), safe="").replace("~", "%7E")


def param_string(pairs: Pairs, passphrase: str = "") -> str:
    parts = [f"{k}={pf_encode(v)}" for k, v in pairs if v is not None and str(v) != "" and k != "signature"]
    if passphrase:
        parts.append(f"passphrase={pf_encode(passphrase)}")
    return "&".join(parts)


def sign(pairs: Pairs, passphrase: str = "") -> str:
    return hashlib.md5(param_string(pairs, passphrase).encode()).hexdigest()


def cents_to_decimal(cents: int) -> str:
    return f"{cents / 100:.2f}"


def decimal_to_cents(value: str) -> int:
    return int(round(float(value) * 100))


# --------------------------------------------------------------------------- checkout
def build_checkout(cfg: PayfastConfig, *, m_payment_id: str, amount_cents: int, item_name: str,
                   item_description: str, customer_name: str, email: str, return_url: str, cancel_url: str,
                   notify_url: str, booking_ref: str) -> dict:
    first, _, last = customer_name.strip().partition(" ")
    # Field order matters for the signature — this is PayFast's documented order.
    pairs: Pairs = [
        ("merchant_id", cfg.merchant_id),
        ("merchant_key", cfg.merchant_key),
        ("return_url", return_url),
        ("cancel_url", cancel_url),
        ("notify_url", notify_url),
        ("name_first", first[:100]),
        ("name_last", last[:100]),
        ("email_address", email[:100]),
        ("m_payment_id", m_payment_id),
        ("amount", cents_to_decimal(amount_cents)),
        ("item_name", item_name[:100]),
        ("item_description", item_description[:255]),
        ("custom_str1", booking_ref),
    ]
    fields = [(k, v) for k, v in pairs if v]
    fields.append(("signature", sign(fields, cfg.passphrase)))
    action = cfg.process_url if cfg.provider == "payfast" else "/payments/simulator"
    return {"action": action, "fields": fields}


# --------------------------------------------------------------------------- ITN
@dataclass
class ItnResult:
    http_status: int
    outcome: str
    detail: str = ""


def validate_with_payfast(cfg: PayfastConfig, pairs: Pairs) -> bool:
    """Ask PayFast to confirm it really sent this notification."""
    body = param_string(pairs)
    try:
        resp = requests.post(cfg.validate_url, data=body, timeout=15,
                             headers={"Content-Type": "application/x-www-form-urlencoded",
                                      "User-Agent": "SeahorseRide/1.0"})
        return resp.text.strip() == "VALID"
    except requests.RequestException:
        return False


def handle_itn(pairs: Pairs, cfg: PayfastConfig, *, find_payment: Callable, on_complete: Callable,
               on_failed: Callable, log: Callable, server_confirm: Callable[[Pairs], bool] | None = None) -> ItnResult:
    """Verify an ITN and apply it. Pure orchestration so it can be unit tested without HTTP."""
    data = dict(pairs)
    payload = json.dumps(data)
    received_sig = data.get("signature", "")

    if not received_sig or sign(pairs, cfg.passphrase) != received_sig:
        log("rejected_signature", None, "Signature mismatch", payload)
        return ItnResult(400, "rejected_signature", "Signature mismatch")
    if data.get("merchant_id") != cfg.merchant_id:
        log("rejected_merchant", None, "Merchant ID mismatch", payload)
        return ItnResult(400, "rejected_merchant")
    if server_confirm is not None and not server_confirm(pairs):
        log("rejected_unconfirmed", None, "PayFast did not confirm the notification", payload)
        return ItnResult(400, "rejected_unconfirmed")

    payment = find_payment(data.get("m_payment_id", ""))
    if payment is None:
        log("rejected_unknown", None, f"Unknown m_payment_id {data.get('m_payment_id')}", payload)
        return ItnResult(200, "rejected_unknown")  # 200 so PayFast stops retrying a notification we can't use

    try:
        gross = decimal_to_cents(data.get("amount_gross", "0"))
    except ValueError:
        gross = -1
    if abs(gross - payment.amount_cents) > 1:
        log("rejected_amount", payment, f"Amount {gross} does not match expected {payment.amount_cents}", payload)
        return ItnResult(200, "rejected_amount")

    status = data.get("payment_status", "").upper()
    if status == "COMPLETE":
        outcome = on_complete(payment, data.get("pf_payment_id"))
        log(f"complete_{outcome}", payment, "Payment verified", payload)
        return ItnResult(200, outcome)
    if status in {"FAILED", "CANCELLED"}:
        on_failed(payment, status.lower())
        log(f"payment_{status.lower()}", payment, "Gateway reported " + status, payload)
        return ItnResult(200, status.lower())
    log("ignored_status", payment, f"Status {status}", payload)
    return ItnResult(200, "ignored")
