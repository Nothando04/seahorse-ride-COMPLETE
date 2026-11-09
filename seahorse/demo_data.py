"""Demo dataset: staff accounts, customers and bookings in (almost) every status, so every table has rows.

Used by database/build_demo_db.py (the downloadable demo database) and, when DEMO_DATA=true, by the
live site on its very first start so visitors and markers see a working system straight away.
Call inside an app context, on a database whose schema is up to date and that has no packages yet.
"""
import json
from datetime import timedelta

from . import workflow as wf
from .cli import seed
from .extensions import db
from .models import (AddOn, AuditLog, Booking, Enquiry, Experience, GalleryImage, Horse, Message, Notification,
                     Payment, PaymentEvent, Refund, Review, Slot, StatusHistory, User, Waiver, WaiverSignature, utcnow)
from .settings import set_setting

DEMO_STAFF = [("admin@seahorse.test", "Sam Admin", "super_admin"),
              ("manager@seahorse.test", "Mia Manager", "manager"),
              ("bookings@seahorse.test", "Ben Bookings", "booking_admin"),
              ("guide@seahorse.test", "Gugu Guide", "guide"),
              ("customer@seahorse.test", "Thandi Mokoena", "customer")]


def populate_demo(password: str) -> dict:
    """Fill the current database with the demo dataset. Returns {status: count}."""
    seed(demo_prices=True)
    users = {}
    for email, name, role in DEMO_STAFF:
        u = User(email=email, name=name, role=role, email_verified=True)
        u.set_password(password)
        db.session.add(u)
        users[role] = u
    db.session.commit()
    admin, guide = users["super_admin"], users["guide"]
    exps = Experience.query.order_by(Experience.id).all()

    def customer(name, email):
        u = User.query.filter_by(email=email).first()
        if u is None:
            u = User(email=email, name=name, role="customer", email_verified=True)
            u.set_password(password)
            db.session.add(u)
        return u

    def make(i, name, email, exp, riders, days):
        b = Booking(reference=wf.new_reference(), experience=exp, preferred_date=utcnow().date() + timedelta(days=days),
                    preferred_time="10:30", adults=riders, riders=riders, contact_name=name, contact_email=email,
                    contact_phone="082 555 01%02d" % i, terms_accepted_at=utcnow(),
                    rider_details=json.dumps([{"age_group": "18+", "experience": "never"}] * riders),
                    estimate_cents=(exp.price_cents * riders) if exp.price_cents else None,
                    user=customer(name, email))
        db.session.add(b)
        db.session.flush()
        db.session.add(StatusHistory(booking=b, to_status=wf.PENDING, actor_label=f"{name} (customer)",
                                     note="Request submitted online."))
        return b

    def approve(b, price):
        slot = Slot.query.filter_by(date=b.preferred_date, start_time="10:30").first()
        wf.approve(b, admin, wf.ApprovalInput(slot=slot, final_price_cents=price, payment_mode="full", deposit_cents=None,
                                              deadline=utcnow() + timedelta(hours=48), note="Approved", horses=[],
                                              guide=guide, confirmed_checks=wf.REQUIRED_CHECKS))

    def pay(b):
        p = Payment(booking=b, m_payment_id=f"{b.reference}-P1", provider="simulator", amount_cents=b.final_price_cents)
        db.session.add(p)
        db.session.flush()
        wf.record_payment(p, f"SIM{p.id}")

    b1 = make(1, "Thandi Mokoena", "customer@seahorse.test", exps[0], 2, 3)              # Pending Admin Approval
    b2 = make(2, "Pieter van Wyk", "pieter@example.com", exps[2], 2, 4); approve(b2, 180000)  # Approved — Awaiting Payment
    b3 = make(3, "Aisha Patel", "aisha@example.com", exps[4], 4, 5); approve(b3, 240000); pay(b3)  # Confirmed — Paid
    b4 = make(4, "Lerato Dube", "lerato@example.com", exps[1], 1, 6)
    wf.transition(b4, wf.NEED_INFO, admin, note="How tall is the rider?")
    b5 = make(5, "John Smith", "john@example.com", exps[3], 3, 2)
    wf.transition(b5, wf.DECLINED, admin, note="Fully booked that weekend.")
    ev = make(6, "Nomsa Khumalo", "nomsa@example.com", exps[7], 6, 14)
    ev.kind, ev.event_type, ev.budget = "event", "Birthday", "R6 000"
    db.session.add(Message(booking=b1, sender="system", body="Request received. Nothing has been charged.", read_by_admin=True))
    db.session.add(Message(booking=b4, sender="admin", user_id=admin.id, body="How tall is the rider?", read_by_admin=True))
    db.session.add(Message(booking=b3, sender="customer", body="Can we bring a picnic?"))
    db.session.add(Enquiry(name="Kyle Naidoo", email="kyle@example.com", subject="Gift vouchers?",
                           message="Do you sell gift vouchers for a couples ride?"))
    db.session.commit()

    # ---------------------------------------------------------------- more examples so every table has rows
    manager = User(email="manager2@seahorse.test", name="Mia Manager", role="manager", email_verified=True)
    manager.set_password(password)
    db.session.add(manager)
    horses = {h.name: h for h in Horse.query.all()}
    addons = AddOn.query.all()

    def slot_for(b):
        return Slot.query.filter_by(date=b.preferred_date, start_time="10:30").first()

    def history(b, frm, to, note, who="Sam Admin (Super Administrator)"):
        b.status = to
        db.session.add(StatusHistory(booking=b, from_status=frm, to_status=to, changed_by_id=admin.id, actor_label=who, note=note))

    # Deposit paid, balance outstanding, horses assigned, waivers signed
    b7 = make(7, "Sipho Ndlovu", "sipho@example.com", exps[4], 3, 7)
    b7.children, b7.adults, b7.horse_preference = 2, 1, "kids"
    b7.location = "Quiet beach stretch — fewer people, great for first-timers"
    b7.addon_ids = f"[{addons[0].id}]"
    wf.approve(b7, admin, wf.ApprovalInput(slot=slot_for(b7), final_price_cents=210000, payment_mode="deposit",
                                           deposit_cents=105000, deadline=utcnow() + timedelta(hours=48),
                                           note="Includes photography.", horses=[horses["Peanut"], horses["Biscuit"], horses["Willow"]],
                                           guide=guide, confirmed_checks=wf.REQUIRED_CHECKS))
    p7 = Payment(booking=b7, m_payment_id=f"{b7.reference}-P1", provider="payfast", kind="deposit", amount_cents=105000)
    db.session.add(p7); db.session.flush(); wf.record_payment(p7, "PF1290001")
    waiver = Waiver.query.filter_by(is_active=True).first()
    for rider, minor, signer in [("Sipho Ndlovu", False, "Sipho Ndlovu"), ("Ayanda Ndlovu", True, "Sipho Ndlovu"),
                                 ("Lwazi Ndlovu", True, "Sipho Ndlovu")]:
        db.session.add(WaiverSignature(booking=b7, waiver=waiver, rider_name=rider, rider_is_minor=minor, signer_name=signer,
                                       emergency_name="Zanele Ndlovu", emergency_phone="082 555 0199", ip_address="127.0.0.1"))

    # Completed ride with an approved review
    b8 = make(8, "Emma Botha", "emma@example.com", exps[2], 2, 1)
    b8.horse_preference = "beginner"; b8.location = "Sunset shoreline — best for evening and couples rides"
    approve(b8, 180000); b8.horses = [horses["Storm"], horses["Coral"]]; pay(b8)
    history(b8, wf.CONFIRMED, wf.COMPLETED, "Ride completed.")
    b8.checked_in_at = utcnow() - timedelta(hours=3)
    db.session.add(Review(booking=b8, display_name="Emma", rating=5, status="approved",
                          comment="Magical sunset ride — the horses were so calm and our guide was brilliant."))

    # Paid, then cancelled with a partial refund
    b9 = make(9, "Michael Pillay", "michael@example.com", exps[0], 2, 9)
    approve(b9, 130000); pay(b9)
    history(b9, wf.CONFIRMED, wf.CANCELLED, "Customer cancelled 5 days before the ride.")
    history(b9, wf.CANCELLED, wf.REFUND_PENDING, "Refund of R650.00 started (50% per policy).")
    db.session.add(Refund(booking=b9, amount_cents=65000, reason="Cancelled 5 days before — 50% refund per policy",
                          status="completed", created_by_id=admin.id, completed_at=utcnow()))
    history(b9, wf.REFUND_PENDING, wf.PART_REFUNDED, "Refund of R650.00 completed.")

    # Rescheduled, rescheduling requested, changes proposed, payment expired, no-show
    b10 = make(10, "Fatima Adams", "fatima@example.com", exps[1], 2, 8); approve(b10, 170000); pay(b10)
    new_slot = Slot.query.filter_by(date=b10.preferred_date + timedelta(days=2), start_time="16:30").first()
    wf.reschedule(b10, admin, new_slot, "Moved because of strong wind forecast.")
    b11 = make(11, "David Smith", "david@example.com", exps[3], 4, 10); approve(b11, 280000); pay(b11)
    history(b11, wf.CONFIRMED, wf.RESCHED_REQ, "Customer asked for the following weekend.")
    b12 = make(12, "Naledi Mahlangu", "naledi@example.com", exps[5], 6, 12)
    history(b12, wf.PENDING, wf.CHANGES, "Saturday is full — could you do Sunday 10:30 at R4 500 for the group?")
    b12.approval_note = "Saturday is full — could you do Sunday 10:30 at R4 500 for the group?"
    b13 = make(13, "Chris Venter", "chris@example.com", exps[0], 1, 3); approve(b13, 65000)
    b13.payment_deadline = utcnow() - timedelta(hours=2)
    history(b13, wf.APPROVED, wf.EXPIRED, "Payment deadline passed — provisional booking released.", "System")
    b14 = make(14, "Grace Mokoena", "grace@example.com", exps[0], 1, 1); approve(b14, 65000); pay(b14)
    history(b14, wf.CONFIRMED, wf.NO_SHOW, "Customer did not arrive.")

    # A failed card attempt and the gateway notification log
    db.session.add(Payment(booking=b2, m_payment_id=f"{b2.reference}-P1", provider="payfast", kind="full",
                           amount_cents=180000, status="failed"))
    db.session.flush()
    for pay_row in Payment.query.all():
        outcome = {"paid": "complete_confirmed", "failed": "payment_failed"}.get(pay_row.status, "ignored_status")
        db.session.add(PaymentEvent(payment_id=pay_row.id, outcome=outcome, remote_ip="197.97.145.150",
                                    detail="Signature, merchant and amount verified" if pay_row.status == "paid" else "Gateway reported FAILED",
                                    payload=f'{{"m_payment_id": "{pay_row.m_payment_id}", "amount_gross": "{pay_row.amount_cents / 100:.2f}"}}'))
    db.session.add(PaymentEvent(outcome="rejected_signature", detail="Signature mismatch", remote_ip="45.12.9.3",
                                payload='{"m_payment_id": "SR-FAKE00-P1", "amount_gross": "1.00"}'))

    # Conversations, enquiries, emails, gallery, settings, audit trail
    db.session.add(Message(booking=b7, sender="customer", body="Can my two kids ride ponies next to me?", read_by_admin=True))
    db.session.add(Message(booking=b7, sender="admin", user_id=admin.id, body="Yes — Peanut and Biscuit are booked for them.", read_by_customer=True))
    db.session.add(Message(booking=b7, sender="admin", user_id=admin.id, internal=True, read_by_admin=True,
                           body="Private note: younger child is nervous — lead rein all the way."))
    db.session.add(Enquiry(name="Ruth Daniels", email="ruth@example.com", subject="Wheelchair access?", status="replied",
                           message="Is there a viewing area for a grandparent in a wheelchair?",
                           reply="Yes — the boardwalk next to the meeting point is step-free."))
    for b in Booking.query.all():
        db.session.add(Notification(to_email=b.contact_email, subject=f"We've received your request {b.reference}",
                                    body_text=f"Hi {b.contact_name}, thanks for your request {b.reference}.",
                                    booking_id=b.id, status="sent", attempts=1, sent_at=b.created_at))
    db.session.add(Notification(to_email="pieter@example.com", subject="Approved — please pay", body_text="(example)",
                                booking_id=b2.id, status="failed", attempts=5, last_error="Mailbox temporarily unavailable"))
    for i, (name, cat, cap) in enumerate([("beach-group.jpg", "groups", "Splashing through the surf"),
                                          ("beach-sunset.jpg", "couples", "Golden hour on the shoreline"),
                                          ("white-horse.jpg", "rides", "All smiles on a white horse"),
                                          ("mountain-ride.jpg", "tourists", "Above the clouds")]):
        db.session.add(GalleryImage(path=f"gallery/{name}", caption=cap, category=cat, consent_confirmed=True,
                                    is_published=i < 3))
    for k, v in {"business_name": "Seahorse Ride", "contact_email": "bookings@seahorse.test",
                 "contact_phone": "082 555 0100", "whatsapp_number": "27825550100",
                 "operating_hours": "Daily 07:30–18:30", "meeting_point": "Main beach lifeguard tower"}.items():
        set_setting(k, v)
    for action, entity, eid, detail in [("staff_sign_in", "user", admin.id, None),
                                        ("booking_approved", "booking", b3.reference, "price=240000"),
                                        ("refund_create", "booking", b9.reference, "R650.00"),
                                        ("slot_blocked", "slot", "12", "Weather closure"),
                                        ("role_changed", "user", "manager2@seahorse.test", "manager"),
                                        ("export", "report", "bookings", "last 90 days")]:
        db.session.add(AuditLog(user_id=admin.id, action=action, entity=entity, entity_id=str(eid), detail=detail,
                                ip_address="127.0.0.1"))
    db.session.commit()
    # Demo e-mails are examples only — never send them to the made-up addresses.
    for n in Notification.query.filter(Notification.status.in_(["queued", "failed"])).all():
        if n.status == "queued":
            n.status, n.sent_at = "sent", utcnow()
        n.attempts = max(n.attempts or 0, 5) if n.status == "failed" else (n.attempts or 1)
    db.session.commit()
    return {s: n for s in wf.ALL_STATUSES if (n := Booking.query.filter_by(status=s).count())}
