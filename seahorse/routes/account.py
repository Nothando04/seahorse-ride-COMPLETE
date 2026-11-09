"""Customer accounts: sign-up with email verification, sign-in, password reset, profile, POPIA deletion."""
from __future__ import annotations

from urllib.parse import urlparse

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from ..extensions import db
from ..forms import Form
from ..models import Booking, User, utcnow
from .. import notify
from ..security import (audit, login_required, login_user, logout_user, make_token,
                        rate_limited, read_token)

bp = Blueprint("account", __name__, url_prefix="/account")


def _safe_next(default: str) -> str:
    target = request.args.get("next") or request.form.get("next") or ""
    parsed = urlparse(target)
    if target.startswith("/") and not target.startswith("//") and not parsed.netloc:
        return target
    return default


def _password_ok(form: Form, name="password") -> str:
    pw = request.form.get(name) or ""
    if len(pw) < 10:
        form.errors[name] = "Use at least 10 characters."
    elif pw.lower() == pw or not any(c.isdigit() for c in pw):
        form.errors[name] = "Use a mix of upper and lower case letters and at least one number."
    return pw


def _send_verification(user: User) -> None:
    token = make_token("verify-email", user.id)
    notify.queue(user.email, "Confirm your email address",
                 f"Hi {user.name},\n\nPlease confirm your email address for your Seahorse Ride account:\n"
                 f"{url_for('account.verify', token=token, _external=True)}\n\nThis link expires in 3 days.")


@bp.route("/sign-up", methods=["GET", "POST"])
def sign_up():
    form = Form(request.form)
    if request.method == "POST":
        if rate_limited("signup", 5, 3600):
            abort(429)
        name = form.text("name", "Full name", min_len=2, max_len=120)
        email = form.email()
        phone = form.phone(required=False)
        pw = _password_ok(form)
        if not form.checked("accept_privacy"):
            form.errors["accept_privacy"] = "Please accept the privacy policy."
        if form.ok and User.query.filter_by(email=email).first():
            form.errors["email"] = "An account with this email already exists. Sign in or reset your password."
        if form.ok:
            user = User(name=name, email=email, phone=phone or None, marketing_opt_in=form.checked("marketing"))
            user.set_password(pw)
            db.session.add(user)
            db.session.flush()
            # Attach earlier guest bookings made with this email once the email is verified.
            _send_verification(user)
            db.session.commit()
            login_user(user)
            flash(f"Welcome, {user.name.split(' ')[0]}! Your account is ready.", "success")
            return redirect(_safe_next(url_for("account.my_bookings")))
    return render_template("account/sign_up.html", form=form)


@bp.route("/verify/<token>")
def verify(token: str):
    uid = read_token("verify-email", token, max_age=3 * 86400)
    user = db.session.get(User, uid) if uid else None
    if user is None:
        flash("That confirmation link is invalid or has expired.", "error")
        return redirect(url_for("account.sign_in"))
    user.email_verified = True
    # Link guest bookings that used this (now verified) email address.
    Booking.query.filter(Booking.user_id.is_(None), db.func.lower(Booking.contact_email) == user.email).update(
        {"user_id": user.id}, synchronize_session=False)
    db.session.commit()
    flash("Email confirmed — thank you.", "success")
    return redirect(url_for("account.my_bookings") if g.user else url_for("account.sign_in"))


@bp.route("/verify/resend", methods=["POST"])
@login_required
def resend_verification():
    if rate_limited("verify", 3, 3600):
        abort(429)
    _send_verification(g.user)
    db.session.commit()
    flash("Confirmation email sent.", "success")
    return redirect(url_for("account.my_bookings"))


def _sign_in(staff: bool):
    form = Form(request.form)
    if request.method == "POST":
        if rate_limited("login", 10, 900):
            abort(429)
        email = form.email()
        pw = request.form.get("password") or ""
        user = User.query.filter_by(email=email).first() if form.ok else None
        if user is None or not user.is_active or not user.check_password(pw):
            form.errors["password"] = "Email or password is incorrect."
        elif staff and not user.is_staff:
            form.errors["password"] = "This account doesn't have staff access."
        if form.ok:
            login_user(user)
            user.last_login_at = utcnow()
            if user.is_staff:
                audit("staff_sign_in", "user", user.id)
            db.session.commit()
            return redirect(_safe_next(url_for("admin.dashboard") if user.is_staff else url_for("account.my_bookings")))
    return render_template("account/sign_in.html", form=form, staff=staff)


@bp.route("/sign-in", methods=["GET", "POST"])
def sign_in():
    return _sign_in(staff=False)


@bp.route("/staff/sign-in", methods=["GET", "POST"])  # unlisted — not linked anywhere on the public site
def staff_sign_in():
    return _sign_in(staff=True)


@bp.route("/sign-out", methods=["POST"])
def sign_out():
    logout_user()
    flash("You've been signed out.", "info")
    return redirect(url_for("public.home"))


@bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    form = Form(request.form)
    if request.method == "POST":
        if rate_limited("forgot", 5, 3600):
            abort(429)
        email = form.email()
        user = User.query.filter_by(email=email).first() if form.ok else None
        if user:
            token = make_token("reset-password", {"u": user.id, "p": user.password_hash[-12:]})
            notify.queue(user.email, "Reset your password",
                         f"Hi {user.name},\n\nReset your password here (valid for 1 hour):\n"
                         f"{url_for('account.reset_password', token=token, _external=True)}\n\n"
                         "If you didn't ask for this, ignore this email.")
            db.session.commit()
        if form.ok:
            flash("If an account exists for that email, we've sent a reset link.", "success")
            return redirect(url_for("account.sign_in"))
    return render_template("account/forgot.html", form=form)


@bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token: str):
    data = read_token("reset-password", token, max_age=3600)
    user = db.session.get(User, data.get("u")) if isinstance(data, dict) else None
    if user is None or user.password_hash[-12:] != data.get("p"):  # single use: changes once the password changes
        flash("That reset link is invalid or has already been used.", "error")
        return redirect(url_for("account.forgot_password"))
    form = Form(request.form)
    if request.method == "POST":
        pw = _password_ok(form)
        if form.ok:
            user.set_password(pw)
            user.email_verified = True
            db.session.commit()
            flash("Password updated. Please sign in.", "success")
            return redirect(url_for("account.sign_in"))
    return render_template("account/reset.html", form=form)


@bp.route("/bookings")
@login_required
def my_bookings():
    bookings = Booking.query.filter_by(user_id=g.user.id).order_by(Booking.created_at.desc()).all()
    return render_template("account/my_bookings.html", bookings=bookings)


@bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    form = Form(request.form)
    user = g.user
    if request.method == "POST":
        action = request.form.get("action")
        if action == "details":
            name = form.text("name", "Full name", min_len=2, max_len=120)
            phone = form.phone(required=False)
            if form.ok:
                user.name, user.phone = name, phone or None
                user.marketing_opt_in = form.checked("marketing")
                db.session.commit()
                flash("Details saved.", "success")
                return redirect(url_for("account.profile"))
        elif action == "password":
            if not user.check_password(request.form.get("current_password") or ""):
                form.errors["current_password"] = "Your current password is incorrect."
            pw = _password_ok(form, "new_password")
            if form.ok:
                user.set_password(pw)
                db.session.commit()
                login_user(user)
                flash("Password changed.", "success")
                return redirect(url_for("account.profile"))
        elif action == "delete":
            if not user.check_password(request.form.get("confirm_password") or ""):
                form.errors["confirm_password"] = "Enter your password to confirm."
            elif user.is_staff:
                form.errors["confirm_password"] = "Staff accounts are removed by a Super Administrator."
            else:
                # POPIA: remove personal details from the account; booking & payment records are kept for
                # accounting/legal retention but detached from the login.
                Booking.query.filter_by(user_id=user.id).update({"user_id": None})
                user.name, user.phone, user.is_active = "Deleted user", None, False
                user.email = f"deleted-{user.id}@invalid.local"
                user.set_password(make_token("x", user.id))
                user.marketing_opt_in = False
                audit("account_deleted", "user", user.id)
                db.session.commit()
                logout_user()
                flash("Your account has been deleted.", "success")
                return redirect(url_for("public.home"))
    return render_template("account/profile.html", form=form, user=user)
