"""End-to-end tests of the booking → approval → payment workflow and its safety rules."""
import threading
from datetime import timedelta

import pytest

from seahorse import payfast
from seahorse.extensions import db
from seahorse.models import Booking, Notification, Payment, PaymentEvent, Slot, User, utcnow
from seahorse.workflow import (APPROVED, CONFIRMED, EXPIRED, PENDING, ApprovalInput, WorkflowError, approve,
                               expire_overdue, payable_reason)
from conftest import booking_form, csrf, first_slot, login


def as_customer(client, email="thandi@example.com"):
    login(client, email, staff=False)
    return client


def submit(client, app, riders=2, slot_offset=2, **extra):
    as_customer(client)
    token = csrf(client, "/book")
    slot_id = first_slot(app, slot_offset)
    r = client.post("/book", data=booking_form(token, slot_id, riders=riders, **extra))
    assert r.status_code == 302, r.get_data(as_text=True)[:2000]
    ref = r.location.rsplit("/", 1)[-1]
    return ref, slot_id


def approve_via_admin(_customer_client, app, ref, slot_id, price="1500.00", mode="full", deposit="", checks=True):
    client = app.test_client()  # staff use their own signed-in session
    login(client, "booker@test.io")
    with app.app_context():
        b = Booking.query.filter_by(reference=ref).one()
        bid = b.id
    token = csrf(client, f"/admin/requests/{bid}")
    data = {"csrf_token": token, "slot_id": str(slot_id), "final_price": price, "payment_mode": mode, "deposit": deposit,
            "deadline": (utcnow() + timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M"), "note": "Looking forward to it"}
    if checks:
        data.update({f"check_{c}": "on" for c in ("date_time", "group_size", "price", "availability")})
    return client.post(f"/admin/requests/{bid}/approve", data=data), bid


def itn(app, payment: Payment, status="COMPLETE", amount=None, passphrase=None, merchant="10000100"):
    pairs = [("m_payment_id", payment.m_payment_id), ("pf_payment_id", f"PF{payment.id}"), ("payment_status", status),
             ("item_name", "x"), ("amount_gross", payfast.cents_to_decimal(amount or payment.amount_cents)),
             ("merchant_id", merchant)]
    pairs.append(("signature", payfast.sign(pairs, passphrase if passphrase is not None else app.config["PAYFAST_PASSPHRASE"])))
    return pairs


# --------------------------------------------------------------------------- submission

def test_request_is_pending_and_never_charges(client, app):
    ref, _ = submit(client, app)
    with app.app_context():
        b = Booking.query.filter_by(reference=ref).one()
        assert b.status == PENDING
        assert b.slot_id is None  # pending requests don't take capacity
        assert b.payments == []
        assert b.estimate_cents == 2 * 65000  # demo price per rider
        assert payable_reason(b) is not None
        subjects = [n.subject for n in Notification.query.all()]
        assert any(ref in s for s in subjects)  # customer confirmation + staff alert
    # Payment page refuses before approval
    r = client.post(f"/booking/{ref}/pay", data={"csrf_token": csrf(client, f"/booking/{ref}")})
    assert r.status_code == 302
    with app.app_context():
        assert Payment.query.count() == 0


def test_validation_errors_are_reported(client, app):
    as_customer(client)
    token = csrf(client, "/book")
    bad = booking_form(token, None, riders=3)
    bad.update({"adults": "1", "contact_phone": "abc", "accept_terms": ""})
    r = client.post("/book", data=bad)
    html = r.get_data(as_text=True)
    assert r.status_code == 200
    assert "Riders can" in html and "valid phone" in html and "accept the booking terms" in html
    with app.app_context():
        assert Booking.query.count() == 0


def test_duplicate_submission_returns_same_booking(client, app):
    as_customer(client)
    token = csrf(client, "/book")
    data = booking_form(token, first_slot(app), nonce="same-nonce")
    r1 = client.post("/book", data=data)
    r2 = client.post("/book", data=data)
    assert r1.status_code == r2.status_code == 302
    with app.app_context():
        assert Booking.query.count() == 1


def test_min_age_rule_enforced(client, app):
    as_customer(client)
    token = csrf(client, "/book")
    data = booking_form(token, first_slot(app), riders=1, experience_id=2)  # sunset ride min age 12
    data["rider_age_0"] = "8-11"
    r = client.post("/book", data=data)
    assert "must be at least 12" in r.get_data(as_text=True)


# --------------------------------------------------------------------------- approval + payment

def test_full_lifecycle_with_verified_itn(client, app):
    ref, slot_id = submit(client, app)
    r, bid = approve_via_admin(client, app, ref, slot_id)
    assert r.status_code == 302
    with app.app_context():
        b = db.session.get(Booking, bid)
        assert b.status == APPROVED and b.slot_id == slot_id and b.final_price_cents == 150000
        assert any("Approved" in n.subject for n in Notification.query.all())

    # customer pays from their account (the emailed link requires signing in first)
    guest = app.test_client()
    with app.app_context():
        from seahorse.security import make_token
        b = db.session.get(Booking, bid)
        token = make_token("booking-access", {"b": b.id, "k": b.access_key[:12]})
    assert "/account/sign-in" in guest.get(f"/booking/access/{token}").location
    as_customer(guest)
    r = guest.post(f"/booking/{ref}/pay", data={"csrf_token": csrf(guest, f"/booking/{ref}")})
    html = r.get_data(as_text=True)
    assert 'name="signature"' in html and "150000" not in html and "1500.00" in html

    with app.app_context():
        p = Payment.query.one()
        pairs = itn(app, p)
    # Signed ITN through the real handler (simulator mode skips only the remote "VALID" check)
    with app.test_request_context():
        from seahorse.routes.payments import process_itn
        res = process_itn(pairs, "1.2.3.4", server_confirm=None)
        assert res.outcome == "confirmed"
        again = process_itn(pairs, "1.2.3.4", server_confirm=None)  # duplicate notification
        assert again.outcome == "duplicate"
    with app.app_context():
        b = db.session.get(Booking, bid)
        assert b.status == CONFIRMED and b.amount_paid_cents == 150000 and b.balance_cents == 0
        assert Payment.query.filter_by(status="paid").count() == 1
        assert [h.to_status for h in b.history][-1] == CONFIRMED
    assert guest.get(f"/booking/{ref}/receipt").status_code == 200


def test_simulator_flow_end_to_end(client, app):
    ref, slot_id = submit(client, app)
    approve_via_admin(client, app, ref, slot_id, price="900")
    r = client.post(f"/booking/{ref}/pay", data={"csrf_token": csrf(client, f"/booking/{ref}")})
    assert "/payments/simulator" in r.get_data(as_text=True)
    with app.app_context():
        p = Payment.query.one()
        cfg = payfast.config_from(app.config)
        fields = payfast.build_checkout(cfg, m_payment_id=p.m_payment_id, amount_cents=p.amount_cents, item_name=f"Seahorse Ride {ref} ({p.kind})",
                                        item_description="Guided Beach Ride", customer_name="Thandi Mokoena", email="thandi@example.com",
                                        return_url="x", cancel_url="y", notify_url="z", booking_ref=ref)["fields"]
    # tampering with the amount breaks the signature
    tampered = [(k, "1.00" if k == "amount" else v) for k, v in fields]
    assert client.post("/payments/simulator", data=dict(tampered)).status_code == 400
    assert client.post("/payments/simulator", data=dict(fields)).status_code == 200
    r = client.post("/payments/simulator/complete", data={"csrf_token": csrf(client, f"/booking/{ref}"),
                                                         "m_payment_id": p.m_payment_id, "outcome": "COMPLETE"})
    assert r.status_code == 302
    with app.app_context():
        assert Booking.query.filter_by(reference=ref).one().status == CONFIRMED


def test_itn_rejects_forgery_wrong_amount_and_merchant(client, app):
    ref, slot_id = submit(client, app)
    approve_via_admin(client, app, ref, slot_id)
    client.post(f"/booking/{ref}/pay", data={"csrf_token": csrf(client, f"/booking/{ref}")})
    with app.app_context():
        p = Payment.query.one()
        forged = itn(app, p, passphrase="wrong")
        cheap = itn(app, p, amount=100)
        other = itn(app, p, merchant="999")
    with app.test_request_context():
        from seahorse.routes.payments import process_itn
        assert process_itn(forged, "x", None).outcome == "rejected_signature"
        assert process_itn(cheap, "x", None).outcome == "rejected_amount"
        assert process_itn(other, "x", None).outcome == "rejected_merchant"
        assert process_itn(forged, "x", None).http_status == 400
    with app.app_context():
        assert Booking.query.filter_by(reference=ref).one().status == APPROVED
        assert PaymentEvent.query.count() == 4


def test_itn_requires_payfast_confirmation(client, app):
    ref, slot_id = submit(client, app)
    approve_via_admin(client, app, ref, slot_id)
    client.post(f"/booking/{ref}/pay", data={"csrf_token": csrf(client, f"/booking/{ref}")})
    with app.app_context():
        pairs = itn(app, Payment.query.one())
    with app.test_request_context():
        from seahorse.routes.payments import process_itn
        assert process_itn(pairs, "x", server_confirm=lambda p: False).outcome == "rejected_unconfirmed"


def test_customer_return_url_does_not_mark_paid(client, app):
    ref, slot_id = submit(client, app)
    approve_via_admin(client, app, ref, slot_id)
    client.get(f"/booking/{ref}/payment-return")
    with app.app_context():
        assert Booking.query.filter_by(reference=ref).one().status == APPROVED


def test_deposit_then_balance(client, app):
    ref, slot_id = submit(client, app)
    approve_via_admin(client, app, ref, slot_id, price="2000", mode="deposit", deposit="500")
    with app.test_request_context():
        from seahorse.routes.payments import process_itn
        client.post(f"/booking/{ref}/pay", data={"csrf_token": csrf(client, f"/booking/{ref}")})
        p1 = Payment.query.one()
        assert p1.amount_cents == 50000 and p1.kind == "deposit"
        process_itn(itn(app, p1), "x", None)
        b = Booking.query.filter_by(reference=ref).one()
        assert b.status == CONFIRMED and b.balance_cents == 150000 and b.amount_due_now_cents == 150000
    client.post(f"/booking/{ref}/pay", data={"csrf_token": csrf(client, f"/booking/{ref}")})
    with app.test_request_context():
        from seahorse.routes.payments import process_itn
        p2 = Payment.query.filter_by(kind="balance").one()
        assert p2.amount_cents == 150000
        process_itn(itn(app, p2), "x", None)
        assert Booking.query.filter_by(reference=ref).one().balance_cents == 0


def test_approval_requires_all_confirmations(client, app):
    ref, slot_id = submit(client, app)
    r, bid = approve_via_admin(client, app, ref, slot_id, checks=False)
    with app.app_context():
        assert db.session.get(Booking, bid).status == PENDING


# --------------------------------------------------------------------------- capacity

def test_capacity_prevents_overbooking(client, app):
    with app.app_context():
        slot = db.session.get(Slot, first_slot(app))
        slot.capacity = 3
        db.session.commit()
        sid = slot.id
    ref1, _ = submit(client, app, riders=2)
    ref2, _ = submit(app.test_client(), app, riders=2)
    r, _ = approve_via_admin(client, app, ref1, sid)
    with app.app_context():
        assert Booking.query.filter_by(reference=ref1).one().status == APPROVED
    r, bid2 = approve_via_admin(client, app, ref2, sid)
    with app.app_context():
        assert db.session.get(Booking, bid2).status == PENDING  # rejected: only 1 place left


def test_concurrent_approvals_cannot_double_allocate(app, client):
    with app.app_context():
        slot = db.session.get(Slot, first_slot(app))
        slot.capacity = 2
        db.session.commit()
        sid = slot.id
    refs = [submit(app.test_client(), app, riders=2)[0] for _ in range(2)]
    results = []

    def worker(ref):
        with app.app_context():
            admin = User.query.filter_by(email="booker@test.io").one()
            b = Booking.query.filter_by(reference=ref).one()
            try:
                approve(b, admin, ApprovalInput(slot=db.session.get(Slot, sid), final_price_cents=1000, payment_mode="full",
                                                deposit_cents=None, deadline=utcnow() + timedelta(hours=5), note="",
                                                horses=[], guide=None, confirmed_checks={"date_time", "group_size", "price", "availability"}))
                db.session.commit()
                results.append("ok")
            except WorkflowError:
                db.session.rollback()
                results.append("full")
            finally:
                db.session.remove()

    threads = [threading.Thread(target=worker, args=(r,)) for r in refs]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["full", "ok"]


def test_payment_deadline_expiry_releases_capacity(client, app):
    ref, slot_id = submit(client, app)
    approve_via_admin(client, app, ref, slot_id)
    with app.app_context():
        b = Booking.query.filter_by(reference=ref).one()
        b.payment_deadline = utcnow() - timedelta(minutes=1)
        db.session.commit()
        expired = expire_overdue()
        db.session.commit()
        assert [x.reference for x in expired] == [ref]
        b = Booking.query.filter_by(reference=ref).one()
        assert b.status == EXPIRED and payable_reason(b) is not None
        from seahorse.workflow import remaining_capacity
        assert remaining_capacity(db.session.get(Slot, slot_id)) == 8


# --------------------------------------------------------------------------- access control

def test_booking_requires_an_account(client, app):
    r = client.get("/book")
    assert r.status_code == 302 and "/account/sign-in" in r.location
    assert client.get("/plan-your-event").status_code == 302


def test_only_owner_or_staff_can_open_booking(client, app):
    ref, _ = submit(client, app)
    anonymous = app.test_client()
    assert "/account/sign-in" in anonymous.get(f"/booking/{ref}").location
    other = as_customer(app.test_client(), "other@example.com")
    assert other.get(f"/booking/{ref}").status_code == 404
    assert other.get(f"/booking/{ref}/pay").status_code == 404
    assert client.get(f"/booking/{ref}").status_code == 200
    with app.app_context():
        b = Booking.query.filter_by(reference=ref).one()
        assert b.user.email == "thandi@example.com" and b.contact_email == "thandi@example.com"
    staff = app.test_client()
    login(staff, "booker@test.io")
    assert staff.get(f"/booking/{ref}").status_code == 200


def test_role_permissions(client, app):
    ref, slot_id = submit(client, app)
    assert app.test_client().get("/admin/").status_code == 404  # hidden from visitors
    customer = as_customer(app.test_client())
    assert customer.get("/admin/").status_code == 404  # and from customers
    assert customer.get("/admin/users").status_code == 404
    home = customer.get("/").get_data(as_text=True)
    assert "staff/sign-in" not in home and "Staff sign-in" not in home and "Staff area" not in home
    guide = app.test_client()
    login(guide, "guide@test.io")
    assert guide.get("/admin/").status_code == 200
    assert guide.get("/admin/requests").status_code == 403
    assert guide.get("/admin/packages").status_code == 403
    booker = app.test_client()
    login(booker, "booker@test.io")
    assert booker.get("/admin/requests").status_code == 200
    assert booker.get("/admin/reports").status_code == 403
    assert booker.get("/admin/users").status_code == 403
    manager = app.test_client()
    login(manager, "manager@test.io")
    assert manager.get("/admin/reports").status_code == 200
    assert manager.get("/admin/users").status_code == 403
    sup = app.test_client()
    login(sup, "super@test.io")
    assert sup.get("/admin/users").status_code == 200


def test_csrf_required(client, app):
    as_customer(client)
    r = client.post("/book", data=booking_form("bogus", first_slot(app)))
    assert r.status_code == 400


def test_staff_signin_rejects_customers(client, app):
    with app.app_context():
        u = User(email="cust@test.io", name="Cust", role="customer")
        u.set_password("Password123")
        db.session.add(u)
        db.session.commit()
    r = login(client, "cust@test.io")
    assert "doesn&#39;t have staff access" in r.get_data(as_text=True)


# --------------------------------------------------------------------------- after-booking flows

def test_need_info_reply_returns_to_pending(client, app):
    ref, _ = submit(client, app)
    with app.app_context():
        bid = Booking.query.filter_by(reference=ref).one().id
    staff = app.test_client()
    login(staff, "booker@test.io")
    staff.post(f"/admin/requests/{bid}/action", data={"csrf_token": csrf(staff, "/admin/"), "action": "need_info",
                                                      "note": "How tall is each rider?"})
    with app.app_context():
        assert db.session.get(Booking, bid).status == "Awaiting Customer Information"
    client.post(f"/booking/{ref}/message", data={"csrf_token": csrf(client, f"/booking/{ref}"), "body": "1.7m and 1.6m"})
    with app.app_context():
        b = db.session.get(Booking, bid)
        assert b.status == PENDING
        assert [m.body for m in b.messages][-1] == "1.7m and 1.6m"


def test_internal_notes_hidden_from_customer(client, app):
    ref, _ = submit(client, app)
    with app.app_context():
        bid = Booking.query.filter_by(reference=ref).one().id
    staff = app.test_client()
    login(staff, "booker@test.io")
    staff.post(f"/admin/requests/{bid}/message", data={"csrf_token": csrf(staff, "/admin/"), "body": "SECRET horse note", "internal": "1"})
    assert "SECRET horse note" not in client.get(f"/booking/{ref}").get_data(as_text=True)
    assert "SECRET horse note" in staff.get(f"/admin/requests/{bid}").get_data(as_text=True)


def test_cancel_refund_and_review_rules(client, app):
    ref, slot_id = submit(client, app)
    approve_via_admin(client, app, ref, slot_id, price="1000")
    client.post(f"/booking/{ref}/pay", data={"csrf_token": csrf(client, f"/booking/{ref}")})
    with app.test_request_context():
        from seahorse.routes.payments import process_itn
        process_itn(itn(app, Payment.query.one()), "x", None)
        bid = Booking.query.filter_by(reference=ref).one().id
    # review not allowed before completion
    client.post(f"/booking/{ref}/review", data={"csrf_token": csrf(client, f"/booking/{ref}"), "rating": "5",
                                                "comment": "Wonderful ride!", "display_name": "T"})
    with app.app_context():
        assert db.session.get(Booking, bid).review is None
    mgr = app.test_client()
    login(mgr, "manager@test.io")
    t = csrf(mgr, "/admin/")
    mgr.post(f"/admin/requests/{bid}/refund", data={"csrf_token": t, "action": "create", "amount": "2000", "reason": "too much"})
    with app.app_context():
        assert db.session.get(Booking, bid).refunds == []  # can't refund more than paid
    mgr.post(f"/admin/requests/{bid}/refund", data={"csrf_token": t, "action": "create", "amount": "400", "reason": "Weather"})
    with app.app_context():
        b = db.session.get(Booking, bid)
        assert b.status == "Refund Pending"
        rid = b.refunds[0].id
    mgr.post(f"/admin/requests/{bid}/refund", data={"csrf_token": t, "action": "complete", "refund_id": str(rid)})
    with app.app_context():
        assert db.session.get(Booking, bid).status == "Partially Refunded"


def test_complete_then_review_and_waiver(client, app):
    ref, slot_id = submit(client, app, riders=1)
    approve_via_admin(client, app, ref, slot_id, price="650")
    client.post(f"/booking/{ref}/pay", data={"csrf_token": csrf(client, f"/booking/{ref}")})
    with app.test_request_context():
        from seahorse.routes.payments import process_itn
        process_itn(itn(app, Payment.query.one()), "x", None)
        bid = Booking.query.filter_by(reference=ref).one().id
    t = csrf(client, f"/booking/{ref}")
    r = client.post(f"/booking/{ref}/waiver", data={"csrf_token": t, "rider_name": "Lebo Child", "rider_is_minor": "on",
                                                   "signer_name": "Lebo Child", "emergency_name": "Mom", "emergency_phone": "0821112222", "agree": "on"})
    with app.app_context():
        assert db.session.get(Booking, bid).signatures == []  # minor can't self-sign
    client.post(f"/booking/{ref}/waiver", data={"csrf_token": t, "rider_name": "Thandi", "signer_name": "Thandi",
                                               "emergency_name": "Sipho", "emergency_phone": "0821112222", "agree": "on"})
    staff = app.test_client()
    login(staff, "booker@test.io")
    staff.post(f"/admin/requests/{bid}/action", data={"csrf_token": csrf(staff, "/admin/"), "action": "complete"})
    client.post(f"/booking/{ref}/review", data={"csrf_token": t, "rating": "5", "comment": "Wonderful ride!", "display_name": "Thandi"})
    with app.app_context():
        b = db.session.get(Booking, bid)
        assert b.status == "Completed" and len(b.signatures) == 1 and b.review.status == "pending"


def test_migrations_apply_cleanly(tmp_path):
    import os
    from flask_migrate import upgrade
    from seahorse import create_app as make
    app = make({"SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'm.db'}", "TESTING": True})
    with app.app_context():
        upgrade()
        from sqlalchemy import inspect
        assert "booking" in inspect(db.engine).get_table_names()
        db.engine.dispose()


def test_payfast_checkout_targets_official_gateway(app, client):
    app.config.update(PAYMENT_PROVIDER="payfast", PAYFAST_SANDBOX=True, TUNNEL_URL="https://abc.trycloudflare.com")
    ref, slot_id = submit(client, app)
    approve_via_admin(client, app, ref, slot_id, price="1200")
    html = client.post(f"/booking/{ref}/pay", data={"csrf_token": csrf(client, f"/booking/{ref}")}).get_data(as_text=True)
    assert 'action="https://sandbox.payfast.co.za/eng/process"' in html
    assert "https://abc.trycloudflare.com/payments/payfast/notify" in html
    assert "http://localhost/booking/" in html  # customer returns to the address they are using
    app.config["PAYFAST_SANDBOX"] = False
    with app.app_context():
        from seahorse import payfast
        assert payfast.config_from(app.config).process_url == "https://www.payfast.co.za/eng/process"
