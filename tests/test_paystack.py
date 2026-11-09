"""Paystack card checkout: initialise, verify on return, signed webhook, and every rejection path (no network)."""
import hashlib
import hmac
import json

import pytest

from seahorse import create_app, paystack
from seahorse.models import Booking, Payment, PaymentEvent
from seahorse.workflow import APPROVED, CONFIRMED
from conftest import csrf
from test_workflow import approve_via_admin, submit

SK = "sk_test_unit_secret_123"


class FakeResp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


@pytest.fixture()
def ps_app(app):
    app.config.update(PAYMENT_PROVIDER="paystack", PAYSTACK_SECRET_KEY=SK, PAYSTACK_CURRENCY="ZAR")
    return app


@pytest.fixture()
def gateway(monkeypatch):
    """Stands in for api.paystack.co. `state['verify']` is what the verify call returns."""
    state = {"init": [], "verify": None}

    def fake_request(method, url, headers=None, json=None, timeout=None, **kw):
        assert headers["Authorization"] == f"Bearer {SK}"
        if url.endswith("/transaction/initialize"):
            state["init"].append(json)
            return FakeResp(200, {"status": True, "data": {"authorization_url": "https://checkout.paystack.com/abc123",
                                                           "reference": json["reference"]}})
        if "/transaction/verify/" in url:
            return FakeResp(200, {"status": True, "data": state["verify"]})
        return FakeResp(404, {"status": False, "message": "not found"})

    monkeypatch.setattr(paystack.requests, "request", fake_request)
    return state


def _approved_booking(client, app, price="1800"):
    ref, slot_id = submit(client, app)
    approve_via_admin(client, app, ref, slot_id, price=price)
    return ref


def _start_checkout(client, ref):
    return client.post(f"/booking/{ref}/pay", data={"csrf_token": csrf(client, f"/booking/{ref}")})


def test_checkout_sends_customer_to_paystack_card_page(client, ps_app, gateway):
    ref = _approved_booking(client, ps_app)
    r = _start_checkout(client, ref)
    assert r.status_code == 303 and r.location == "https://checkout.paystack.com/abc123"
    sent = gateway["init"][0]
    assert sent["amount"] == 180000 and sent["currency"] == "ZAR" and sent["channels"] == ["card"]
    assert sent["email"] == "thandi@example.com" and sent["reference"].startswith(f"{ref}-P1-T")
    assert sent["callback_url"].endswith("/payments/paystack/callback")
    assert sent["metadata"]["cancel_action"].endswith(f"/booking/{ref}/payment-cancelled")
    # The pay page names Paystack and the card fields
    html = client.get(f"/booking/{ref}/pay").get_data(as_text=True)
    assert "securely with Paystack" in html and "card number, expiry date and CVV" in html


def test_return_is_verified_with_paystack_before_confirming(client, ps_app, gateway):
    ref = _approved_booking(client, ps_app)
    _start_checkout(client, ref)
    reference = gateway["init"][0]["reference"]
    gateway["verify"] = {"id": 991, "reference": reference, "status": "success", "amount": 180000, "currency": "ZAR"}
    r = client.get(f"/payments/paystack/callback?reference={reference}&trxref={reference}")
    assert r.status_code == 302 and r.location.endswith(f"/booking/{ref}/payment-return")
    with ps_app.app_context():
        b = Booking.query.filter_by(reference=ref).one()
        assert b.status == CONFIRMED and b.amount_paid_cents == 180000
        assert Payment.query.one().gateway_payment_id == "PS-991"
    # A second return (or the webhook) is ignored — no double payment
    client.get(f"/payments/paystack/callback?reference={reference}")
    with ps_app.app_context():
        assert Booking.query.filter_by(reference=ref).one().amount_paid_cents == 180000


@pytest.mark.parametrize("tamper, outcome", [({"amount": 100}, "rejected_amount"),
                                             ({"currency": "NGN"}, "rejected_currency")])
def test_wrong_amount_or_currency_is_rejected(client, ps_app, gateway, tamper, outcome):
    ref = _approved_booking(client, ps_app)
    _start_checkout(client, ref)
    reference = gateway["init"][0]["reference"]
    gateway["verify"] = {"id": 5, "reference": reference, "status": "success", "amount": 180000, "currency": "ZAR", **tamper}
    client.get(f"/payments/paystack/callback?reference={reference}")
    with ps_app.app_context():
        assert Booking.query.filter_by(reference=ref).one().status == APPROVED
        assert PaymentEvent.query.filter_by(outcome=outcome).count() == 1


def test_declined_card_leaves_booking_payable(client, ps_app, gateway):
    ref = _approved_booking(client, ps_app)
    _start_checkout(client, ref)
    reference = gateway["init"][0]["reference"]
    gateway["verify"] = {"id": 6, "reference": reference, "status": "failed", "amount": 180000, "currency": "ZAR",
                         "gateway_response": "Declined"}
    r = client.get(f"/payments/paystack/callback?reference={reference}", follow_redirects=True)
    assert "didn&#39;t go through" in r.get_data(as_text=True) or "didn't go through" in r.get_data(as_text=True)
    with ps_app.app_context():
        assert Booking.query.filter_by(reference=ref).one().status == APPROVED
    # The customer can try again with a fresh reference
    _start_checkout(client, ref)
    assert gateway["init"][1]["reference"] != reference


def _signed(body: dict, key=SK):
    raw = json.dumps(body).encode()
    return raw, hmac.new(key.encode(), raw, hashlib.sha512).hexdigest()


def test_webhook_requires_valid_signature_and_confirms(client, ps_app, gateway):
    ref = _approved_booking(client, ps_app)
    _start_checkout(client, ref)
    reference = gateway["init"][0]["reference"]
    event = {"event": "charge.success", "data": {"id": 77, "reference": reference, "status": "success",
                                                  "amount": 180000, "currency": "ZAR"}}
    raw, good_sig = _signed(event)
    _, forged = _signed(event, key="sk_test_attacker")
    hook = ps_app.test_client()  # Paystack's server: no session, no CSRF token
    assert hook.post("/payments/paystack/webhook", data=raw, content_type="application/json",
                     headers={"X-Paystack-Signature": forged}).status_code == 401
    assert hook.post("/payments/paystack/webhook", data=raw, content_type="application/json").status_code == 401
    with ps_app.app_context():
        assert Booking.query.filter_by(reference=ref).one().status == APPROVED
    r = hook.post("/payments/paystack/webhook", data=raw, content_type="application/json",
                  headers={"X-Paystack-Signature": good_sig, "X-Forwarded-For": "52.31.139.75"})
    assert r.status_code == 200
    with ps_app.app_context():
        assert Booking.query.filter_by(reference=ref).one().status == CONFIRMED


def test_missing_key_shows_friendly_message_not_crash(client, ps_app, gateway):
    ps_app.config["PAYSTACK_SECRET_KEY"] = ""
    ref = _approved_booking(client, ps_app)
    r = _start_checkout(client, ref)
    assert r.status_code == 302 and r.location.endswith(f"/booking/{ref}")
    assert gateway["init"] == []


def test_auto_provider_picks_paystack_only_with_a_key(monkeypatch, tmp_path):
    monkeypatch.setenv("PAYMENT_PROVIDER", "auto")
    monkeypatch.delenv("PAYSTACK_SECRET_KEY", raising=False)
    assert create_app({"TESTING": True}).config["PAYMENT_PROVIDER"] == "payfast"
    monkeypatch.setenv("PAYSTACK_SECRET_KEY", "sk_test_abc")
    assert create_app({"TESTING": True}).config["PAYMENT_PROVIDER"] == "paystack"
