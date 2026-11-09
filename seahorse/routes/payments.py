"""Payment pages: Paystack card checkout (verify API + signed webhook) and PayFast checkout (verified ITN)."""
from __future__ import annotations

import ipaddress
import json
import socket

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for

from ..extensions import db
from ..models import Booking, Payment, PaymentEvent, utcnow
from .. import notify, payfast, paystack
from ..gateways import get_gateway, site_url as _site
from ..security import CSRF_EXEMPT, can_view_booking, login_required, rate_limited
from ..workflow import APPROVED, PROCESSING, payable_reason, record_payment, transition

bp = Blueprint("payments", __name__)
CSRF_EXEMPT.add("payments.simulator")  # receives the same signed POST PayFast would; signature is verified
CSRF_EXEMPT.add("payments.paystack_webhook")  # server-to-server from Paystack; HMAC-SHA512 signature is verified


def _cfg() -> payfast.PayfastConfig:
    return payfast.config_from(current_app.config)


def notify_reachable() -> bool:
    cfg = current_app.config
    if cfg.get("PAYMENT_PROVIDER") == "paystack":
        return True  # the customer's return is verified with Paystack's API directly, so no inbound call is needed
    url = cfg.get("PAYFAST_NOTIFY_URL") or cfg.get("PUBLIC_SITE_URL") or cfg.get("TUNNEL_URL") or ""
    return url.startswith("https://") and "localhost" not in url and "127.0.0.1" not in url


@bp.route("/booking/<ref>/pay", methods=["GET", "POST"])
@login_required
def pay(ref: str):
    b = Booking.query.filter_by(reference=ref.upper()).first()
    if b is None or not can_view_booking(b):
        abort(404)
    reason = payable_reason(b)
    gateway = get_gateway(current_app.config)          # a PaystackGateway, PayfastGateway or SimulatorGateway object
    if request.method == "GET":
        return render_template("account/pay.html", b=b, reason=reason, checkout=None, can_confirm=notify_reachable(),
                               paystack_ready=gateway.ready)
    if reason:
        flash(reason, "error")
        return redirect(url_for("booking.view", ref=b.reference))
    if rate_limited("checkout", 10, 600):
        abort(429)
    amount = b.amount_due_now_cents
    # Re-use an open pending checkout for the same amount instead of creating duplicates.
    payment = (Payment.query.filter_by(booking_id=b.id, status="pending", amount_cents=amount)
               .order_by(Payment.id.desc()).first())
    if payment is None:
        n = len(b.payments) + 1
        kind = ("deposit" if b.payment_mode == "deposit" and b.amount_paid_cents == 0 and b.deposit_cents
                else ("balance" if b.amount_paid_cents else "full"))
        payment = Payment(booking=b, m_payment_id=f"{b.reference}-P{n}", amount_cents=amount, kind=kind,
                          provider=gateway.name)
        db.session.add(payment)
    payment.provider = gateway.name
    db.session.commit()
    # Polymorphism: one call, and each gateway class runs its own start_checkout (dynamic dispatch).
    return gateway.start_checkout(b, payment)


@bp.route("/booking/<ref>/payment-return")
@login_required
def returned(ref: str):
    b = Booking.query.filter_by(reference=ref.upper()).first_or_404()
    if not can_view_booking(b):
        abort(404)
    # The return redirect proves nothing — we wait for the server-to-server notification.
    return render_template("account/payment_return.html", b=b)


@bp.route("/booking/<ref>/payment-cancelled")
def cancelled(ref: str):
    flash("Payment was cancelled. Nothing was charged — you can try again before the deadline.", "info")
    return redirect(url_for("booking.view", ref=ref))


@bp.route("/booking/<ref>/status.json")
@login_required
def status_json(ref: str):
    b = Booking.query.filter_by(reference=ref.upper()).first_or_404()
    if not can_view_booking(b):
        abort(404)
    return {"status": b.status, "paid_cents": b.amount_paid_cents}


# --------------------------------------------------------------------------- ITN

# PayFast's published notification source ranges (plus whatever their hostnames currently resolve to).
PAYFAST_NETWORKS = ["197.97.145.144/28", "41.74.179.192/27", "102.216.36.0/28", "102.216.36.128/28",
                    "144.126.193.139/32"]


def _payfast_ip_ok(ip: str) -> bool:
    """Check the notification comes from PayFast's servers (one of PayFast's recommended checks)."""
    if not current_app.config.get("PAYFAST_VERIFY_SOURCE", True):
        return True
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if any(addr in ipaddress.ip_network(n) for n in PAYFAST_NETWORKS):
        return True
    for h in ["www.payfast.co.za", "sandbox.payfast.co.za", "w1w.payfast.co.za", "w2w.payfast.co.za"]:
        try:
            if any(ipaddress.ip_address(i[4][0]) == addr for i in socket.getaddrinfo(h, None)):
                return True
        except (OSError, ValueError):
            continue
    return False


def _find_payment(m_id):
    return Payment.query.filter_by(m_payment_id=m_id).first()


def _on_complete(payment: Payment, gateway_id):
    outcome = record_payment(payment, gateway_id)
    if outcome in {"confirmed", "paid", "paid_needs_attention"}:
        notify.payment_received(payment.booking, payment.amount_cents)
    return outcome


def _on_failed(payment: Payment, status):
    if payment.status != "paid":
        payment.status = "failed" if status == "failed" else "cancelled"
        b = payment.booking
        if b.status == PROCESSING:
            transition(b, APPROVED, None, note="Payment failed at the gateway — customer may try again.",
                       actor_label="Payment gateway")
        notify.status_update(b, "Your payment did not go through",
                             "No money was taken. You can try again from your booking page before the deadline.")


def _logger(remote_ip: str):
    def log(outcome, payment, detail, payload):
        db.session.add(PaymentEvent(payment_id=payment.id if payment else None, outcome=outcome, detail=detail,
                                    payload=payload, remote_ip=remote_ip))
    return log


def process_itn(pairs, remote_ip: str, server_confirm) -> payfast.ItnResult:
    result = payfast.handle_itn(pairs, _cfg(), find_payment=_find_payment, on_complete=_on_complete,
                                on_failed=_on_failed, log=_logger(remote_ip), server_confirm=server_confirm)
    db.session.commit()
    return result


# --------------------------------------------------------------------------- Paystack (card checkout)

def _paystack_cfg() -> paystack.PaystackConfig:
    return paystack.config_from(current_app.config)


def _apply_paystack(data: dict, remote_ip: str) -> paystack.Result:
    result = paystack.apply_transaction(data, _paystack_cfg(), find_payment=_find_payment, on_complete=_on_complete,
                                        on_failed=_on_failed, log=_logger(remote_ip))
    db.session.commit()
    return result


@bp.route("/payments/paystack/callback")
def paystack_callback():
    """Where Paystack sends the customer back. We ask Paystack's API what happened — never trust the redirect."""
    reference = (request.args.get("reference") or request.args.get("trxref") or "")[:80]
    payment = _find_payment(paystack.payment_id_from_reference(reference)) if reference else None
    if payment is None or current_app.config["PAYMENT_PROVIDER"] != "paystack":
        abort(404)
    b = payment.booking
    if payment.status != "paid":
        try:
            result = _apply_paystack(paystack.verify(_paystack_cfg(), reference), "(verify API)")
        except paystack.PaystackError as exc:
            current_app.logger.warning("Paystack verify failed for %s: %s", reference, exc)
            result = None  # the signed webhook will still confirm it; the return page keeps checking
        if result is not None and result.outcome == "failed":
            flash("Your card payment didn't go through" + (f" ({result.detail})" if result.detail else "") +
                  ". No money was taken — you can try again.", "error")
            return redirect(url_for("booking.view", ref=b.reference))
        if result is not None and result.outcome == "not_paid":
            flash("The payment wasn't completed. Nothing was charged — you can try again before the deadline.", "info")
            return redirect(url_for("booking.view", ref=b.reference))
    return redirect(url_for("payments.returned", ref=b.reference))


@bp.route("/payments/paystack/webhook", methods=["POST"])
def paystack_webhook():
    """Paystack's server-to-server notification. Signature (HMAC-SHA512 with the secret key) is checked first."""
    cfg = _paystack_cfg()
    if current_app.config["PAYMENT_PROVIDER"] != "paystack" or not cfg.configured:
        abort(404)
    remote_ip = (request.headers.get("Cf-Connecting-Ip") or request.headers.get("X-Forwarded-For", "")
                 or request.remote_addr or "").split(",")[0].strip()
    raw = request.get_data()
    if not paystack.valid_signature(cfg, raw, request.headers.get("X-Paystack-Signature")):
        db.session.add(PaymentEvent(outcome="rejected_signature", detail="Bad or missing Paystack signature",
                                    remote_ip=remote_ip, payload=raw[:2000].decode("utf-8", "replace")))
        db.session.commit()
        return "", 401
    if remote_ip not in paystack.WEBHOOK_IPS:
        current_app.logger.warning("Paystack webhook from unlisted address %s (signature valid)", remote_ip)
    try:
        event = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        event = {}
    if not isinstance(event, dict):
        event = {}
    if event.get("event") == "charge.success" and isinstance(event.get("data"), dict):
        _apply_paystack(event["data"], remote_ip)
    return "", 200


@bp.route("/payments/payfast/notify", methods=["POST"])
def payfast_notify():
    """PayFast ITN webhook. Order of checks: source IP → signature → PayFast confirmation → amount → status."""
    # Behind the Cloudflare tunnel / a proxy the real sender is in these headers.
    remote_ip = (request.headers.get("Cf-Connecting-Ip") or request.headers.get("X-Forwarded-For", "")
                 or request.remote_addr or "").split(",")[0].strip()
    pairs = list(request.form.items(multi=True))
    cfg = _cfg()
    if cfg.provider != "payfast":
        abort(404)
    if not _payfast_ip_ok(remote_ip) and cfg.sandbox:
        # Sandbox senders change more often; the signature + PayFast "VALID" confirmation below still protect us.
        current_app.logger.warning("PayFast sandbox ITN from unlisted address %s — continuing with signature checks", remote_ip)
    elif not _payfast_ip_ok(remote_ip):
        db.session.add(PaymentEvent(outcome="rejected_source", detail="Not a PayFast server", remote_ip=remote_ip,
                                    payload=str(dict(pairs))[:4000]))
        db.session.commit()
        return "", 400
    result = process_itn(pairs, remote_ip, server_confirm=lambda p: payfast.validate_with_payfast(cfg, p))
    return "", result.http_status


# --------------------------------------------------------------------------- local simulator (development only)

@bp.route("/payments/simulator", methods=["POST"])
def simulator():
    cfg = _cfg()
    if cfg.provider != "simulator" or current_app.config["APP_ENV"] == "production":
        abort(404)
    pairs = list(request.form.items(multi=True))
    data = dict(pairs)
    if payfast.sign(pairs, cfg.passphrase) != data.get("signature"):
        abort(400, "Invalid checkout signature.")
    payment = Payment.query.filter_by(m_payment_id=data.get("m_payment_id")).first_or_404()
    return render_template("account/simulator.html", data=data, payment=payment)


@bp.route("/payments/simulator/complete", methods=["POST"])
def simulator_complete():
    """Builds the exact ITN PayFast would send, signs it, and runs it through the real ITN handler."""
    cfg = _cfg()
    if cfg.provider != "simulator" or current_app.config["APP_ENV"] == "production":
        abort(404)
    payment = Payment.query.filter_by(m_payment_id=request.form.get("m_payment_id", "")).first_or_404()
    outcome = request.form.get("outcome", "COMPLETE")
    status = {"COMPLETE": "COMPLETE", "FAILED": "FAILED"}.get(outcome, "CANCELLED")
    b = payment.booking
    pairs = [("m_payment_id", payment.m_payment_id), ("pf_payment_id", f"SIM{payment.id}{int(utcnow().timestamp())}"),
             ("payment_status", status), ("item_name", f"Seahorse Ride {b.reference}"),
             ("amount_gross", payfast.cents_to_decimal(payment.amount_cents)),
             ("amount_fee", payfast.cents_to_decimal(-round(payment.amount_cents * 0.035))),
             ("amount_net", payfast.cents_to_decimal(payment.amount_cents - round(payment.amount_cents * 0.035))),
             ("custom_str1", b.reference), ("name_first", b.contact_name.split(" ")[0]),
             ("email_address", b.contact_email), ("merchant_id", cfg.merchant_id)]
    pairs.append(("signature", payfast.sign(pairs, cfg.passphrase)))
    if status == "CANCELLED":
        return redirect(url_for("payments.cancelled", ref=b.reference))
    process_itn(pairs, "127.0.0.1 (simulator)", server_confirm=None)
    if status == "COMPLETE":
        return redirect(url_for("payments.returned", ref=b.reference))
    return redirect(url_for("booking.view", ref=b.reference))
