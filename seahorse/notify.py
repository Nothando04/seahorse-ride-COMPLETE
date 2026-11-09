"""Email notifications with a database outbox.

Every message is written to the ``notification`` table in the same transaction as the change that caused it,
then delivered after the request. If SMTP isn't configured the message is kept with status ``logged`` so the
team can read it under Admin → Email outbox (handy in development). Failed sends are retried.
"""
from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage

from flask import current_app, url_for

from .extensions import db
from .models import Booking, Notification, User, utcnow
from .security import booking_access_link
from .settings import get_setting


def money(cents: int | None, currency: str = "ZAR") -> str:
    if cents is None:
        return "Price on request"
    symbol = "R" if currency == "ZAR" else currency + " "
    return f"{symbol}{cents / 100:,.2f}".replace(",", " ")


def queue(to: str, subject: str, body: str, booking: Booking | None = None) -> Notification | None:
    if not to:
        return None
    signature = f"\n\n— {get_setting('business_name')}"
    contact = " · ".join(x for x in [get_setting("contact_phone"), get_setting("contact_email")] if x)
    if contact:
        signature += f"\n{contact}"
    n = Notification(to_email=to, subject=subject, body_text=body.strip() + signature,
                     booking_id=booking.id if booking and booking.id else None)
    db.session.add(n)
    return n


def staff_recipients() -> list[str]:
    emails = {e.strip() for e in get_setting("staff_alert_email").split(",") if e.strip()}
    if not emails:
        emails = {u.email for u in User.query.filter(User.role.in_(["booking_admin", "manager", "super_admin"]),
                                                     User.is_active.is_(True)).all()}
    return sorted(emails)


def queue_staff(subject: str, body: str, booking: Booking | None = None) -> None:
    for addr in staff_recipients():
        queue(addr, subject, body, booking)


# --------------------------------------------------------------------------- delivery

def deliver_pending(limit: int = 25) -> int:
    cfg = current_app.config
    pending = (Notification.query.filter(Notification.status.in_(["queued", "failed"]), Notification.attempts < 5)
               .order_by(Notification.id).limit(limit).all())
    if not pending:
        return 0
    sent = 0
    if cfg.get("BREVO_API_KEY"):
        return _deliver_via_brevo(pending, cfg)
    if not cfg.get("SMTP_HOST"):
        for n in pending:
            n.status, n.attempts = "logged", n.attempts + 1
            current_app.logger.info("EMAIL (not sent — SMTP not configured) to=%s subject=%s\n%s",
                                    n.to_email, n.subject, n.body_text)
        db.session.commit()
        return 0
    try:
        smtp = _connect(cfg)
    except Exception as exc:  # noqa: BLE001
        for n in pending:
            n.status, n.attempts, n.last_error = "failed", n.attempts + 1, str(exc)[:300]
        db.session.commit()
        return 0
    with smtp:
        for n in pending:
            msg = EmailMessage()
            msg["From"] = cfg.get("MAIL_FROM") or cfg.get("SMTP_USERNAME")
            msg["To"] = n.to_email
            msg["Subject"] = n.subject
            msg.set_content(n.body_text)
            n.attempts += 1
            try:
                smtp.send_message(msg)
                n.status, n.sent_at, n.last_error = "sent", utcnow(), None
                sent += 1
            except Exception as exc:  # noqa: BLE001
                n.status, n.last_error = "failed", str(exc)[:300]
    db.session.commit()
    return sent


def _deliver_via_brevo(pending, cfg) -> int:
    """Send through Brevo's HTTPS email API — works on hosts that block SMTP ports (e.g. Render's free plan)."""
    import requests
    from email.utils import parseaddr
    name, addr = parseaddr(cfg.get("MAIL_FROM") or "")
    sent = 0
    for n in pending:
        n.attempts += 1
        try:
            r = requests.post("https://api.brevo.com/v3/smtp/email", timeout=20,
                              headers={"api-key": cfg["BREVO_API_KEY"], "accept": "application/json"},
                              json={"sender": {"email": addr, "name": name or "Seahorse Ride"},
                                    "to": [{"email": n.to_email}], "subject": n.subject, "textContent": n.body_text})
            if r.status_code < 300:
                n.status, n.sent_at, n.last_error = "sent", utcnow(), None
                sent += 1
            else:
                n.status, n.last_error = "failed", f"Brevo {r.status_code}: {r.text[:250]}"
        except Exception as exc:  # noqa: BLE001
            n.status, n.last_error = "failed", str(exc)[:300]
    db.session.commit()
    return sent


def _connect(cfg) -> smtplib.SMTP:
    host, port = cfg["SMTP_HOST"], int(cfg.get("SMTP_PORT") or 587)
    if port == 465:
        smtp = smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=20)
    else:
        smtp = smtplib.SMTP(host, port, timeout=20)
        smtp.starttls(context=ssl.create_default_context())
    if cfg.get("SMTP_USERNAME"):
        password = cfg.get("SMTP_PASSWORD", "")
        if host.strip().lower() == "smtp.gmail.com":
            password = password.replace(" ", "")   # Gmail shows app passwords as "abcd efgh ijkl mnop"
        smtp.login(cfg["SMTP_USERNAME"].strip(), password)
    return smtp


def provider(cfg) -> str:
    """Which email service is set up, in words for the startup message and the admin outbox."""
    if cfg.get("BREVO_API_KEY"):
        return "Brevo email API"
    if cfg.get("SMTP_HOST"):
        return f"SMTP {cfg['SMTP_HOST']}:{cfg.get('SMTP_PORT') or 587}"
    return ""


def send_test(to: str) -> tuple[bool, str]:
    """Queue and immediately try to send one test email. Returns (sent?, message for staff)."""
    cfg = current_app.config
    if not provider(cfg):
        return False, "No email service is set up yet (SMTP_* or BREVO_API_KEY), so nothing can be sent."
    n = queue(to, "Seahorse Ride test email",
              "This is a test email from Seahorse Ride. If you can read this, emails are working.")
    db.session.commit()
    deliver_pending()
    db.session.refresh(n)
    if n.status == "sent":
        return True, f"Test email sent to {to} through {provider(cfg)}."
    return False, f"Sending failed: {n.last_error or n.status}"


# --------------------------------------------------------------------------- message builders

def _when(b: Booking) -> str:
    if b.slot:
        return b.slot.label
    t = f" at {b.preferred_time}" if b.preferred_time else ""
    return f"{b.preferred_date:%a %d %b %Y}{t} (requested)"


def request_received(b: Booking) -> None:
    link = booking_access_link(b)
    queue(b.contact_email, f"We've received your request {b.reference}", f"""
Hi {b.contact_name},

Thank you for your {'event enquiry' if b.kind == 'event' else 'booking request'} with Seahorse Ride.

Reference: {b.reference}
Experience: {b.display_name}
Date: {_when(b)}
Group: {b.adults} adult(s), {b.children} child(ren), {b.riders} rider(s)
Where: {b.location or 'to be agreed'}

Status: Pending Admin Approval.
Nothing has been charged and the booking is not yet confirmed. Our team will check availability, horses,
guides and safety conditions, then send you a final price and a secure payment link.

Track your request and message us here:
{link}
""", b)
    queue_staff(f"New {'event enquiry' if b.kind == 'event' else 'booking request'} {b.reference}", f"""
A new request is waiting for review.

{b.reference} — {b.display_name}
{b.contact_name} · {b.contact_email} · {b.contact_phone}
Date: {_when(b)} · Riders: {b.riders} · Group: {b.group_size}

Review it: {url_for('admin.request_detail', booking_id=b.id, _external=True)}
""", b)


def approved(b: Booking) -> None:
    link = booking_access_link(b)
    due = money(b.amount_due_now_cents, b.currency)
    pay_type = (f"Deposit of {money(b.deposit_cents, b.currency)} now; balance of "
                f"{money(b.final_price_cents - (b.deposit_cents or 0), b.currency)} before your ride"
                if b.payment_mode == "deposit" else "Full payment")
    queue(b.contact_email, f"Approved — please pay to secure booking {b.reference}", f"""
Hi {b.contact_name},

Good news — your booking has been approved.

Reference: {b.reference}
Package: {b.display_name}
Date and time: {_when(b)}
Group size: {b.group_size} ({b.riders} rider(s))
Final price: {money(b.final_price_cents, b.currency)} ({b.currency})
Payment: {pay_type}
Amount due now: {due}
Pay before: {b.payment_deadline:%a %d %b %Y, %H:%M} (UTC)
{('Note from our team: ' + b.approval_note) if b.approval_note else ''}

Pay securely here (card or Instant EFT via PayFast):
{link}

Your place is held until the deadline. If payment isn't received in time the provisional booking is released.

Cancellation and refund terms:
{get_setting('cancellation_policy')}
""", b)


def status_update(b: Booking, headline: str, detail: str = "") -> None:
    queue(b.contact_email, f"{headline} — {b.reference}", f"""
Hi {b.contact_name},

{headline}.
{detail}

Reference: {b.reference}
Status: {b.status}

View your booking: {booking_access_link(b)}
""", b)


def payment_received(b: Booking, amount_cents: int) -> None:
    queue(b.contact_email, f"Payment received — {b.reference} is confirmed", f"""
Hi {b.contact_name},

We've received your payment of {money(amount_cents, b.currency)}. Thank you!

Reference: {b.reference}
Experience: {b.display_name}
When: {_when(b)}
Total paid: {money(b.amount_paid_cents, b.currency)}
Balance outstanding: {money(b.balance_cents, b.currency)}

Your receipt and booking details: {booking_access_link(b)}
Please complete the rider waiver before arrival.
""", b)
    queue_staff(f"Payment verified for {b.reference}", f"{money(amount_cents)} received for {b.reference} "
                f"({b.contact_name}). Status: {b.status}.", b)


def new_message(b: Booking, from_customer: bool, body: str) -> None:
    if from_customer:
        queue_staff(f"New message on {b.reference}", f"{b.contact_name} wrote:\n\n{body}\n\n"
                    f"Reply: {url_for('admin.request_detail', booking_id=b.id, _external=True)}", b)
    else:
        queue(b.contact_email, f"New message about your booking {b.reference}",
              f"Hi {b.contact_name},\n\nOur team wrote:\n\n{body}\n\nReply here: {booking_access_link(b)}", b)


def expired(b: Booking) -> None:
    status_update(b, "Your payment deadline has passed",
                  "The provisional booking has been released. Reply on your booking page if you'd still like to ride "
                  "and we'll check availability again.")
    queue_staff(f"Payment deadline expired — {b.reference}", f"{b.reference} ({b.contact_name}) was released "
                "because payment wasn't received in time. You can extend the deadline from the booking page.", b)


def reminder(b: Booking) -> None:
    queue(b.contact_email, f"Reminder: your ride {b.reference} is coming up", f"""
Hi {b.contact_name},

A quick reminder that your Seahorse Ride booking is coming up: {_when(b)}.
Meeting point: {get_setting('meeting_point') or 'see your booking page'}

Please make sure every rider has signed the waiver: {booking_access_link(b)}
Wear closed shoes and comfortable long trousers, and arrive 15 minutes early.
Conditions are checked on the day by our guides; we will contact you if weather affects your ride.
""", b)


def feedback_request(b: Booking) -> None:
    status_update(b, "Thank you for riding with Seahorse Ride",
                  "We'd love to hear how it went. You can leave a short review on your booking page.")
