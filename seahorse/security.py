"""Authentication, authorisation, CSRF, rate limiting, signed links, uploads and audit logging."""
from __future__ import annotations

import hmac
import os
import secrets
import time
from collections import defaultdict, deque
from functools import wraps
from threading import Lock

from flask import abort, current_app, flash, g, redirect, request, session, url_for
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.utils import secure_filename

from .extensions import db
from .models import AuditLog, Booking, User

# --------------------------------------------------------------------------- sessions / users

def load_current_user() -> None:
    g.user = None
    uid = session.get("uid")
    if uid:
        user = db.session.get(User, uid)
        if user and user.is_active and session.get("pw") == user.password_hash[-16:]:
            g.user = user
        else:
            session.pop("uid", None)


def login_user(user: User) -> None:
    session.clear()  # new session id on login (prevents session fixation)
    session["uid"] = user.id
    session["pw"] = user.password_hash[-16:]  # changing the password logs out other sessions
    session.permanent = True


def logout_user() -> None:
    session.clear()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            flash("Please sign in to continue.", "info")
            return redirect(url_for("account.sign_in", next=request.full_path))
        return view(*args, **kwargs)
    return wrapped


def staff_required(perm: str | None = None):
    """Server-side authorisation for every admin route. Hiding buttons is not a security control."""
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if g.user is None or not g.user.is_staff:
                abort(404)  # customers and visitors can't even tell the staff area exists
            if perm and not g.user.can(perm):
                audit("access_denied", "route", request.path)
                db.session.commit()
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


# --------------------------------------------------------------------------- CSRF

def csrf_token() -> str:
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


CSRF_EXEMPT = {"payments.payfast_notify"}


def check_csrf() -> None:
    if request.method in {"GET", "HEAD", "OPTIONS"} or request.endpoint in CSRF_EXEMPT:
        return
    if current_app.config.get("TESTING") and current_app.config.get("WTF_CSRF_DISABLED"):
        return
    sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
    if not sent or not hmac.compare_digest(sent, session.get("csrf", "")):
        abort(400, "Your form expired. Please go back, refresh the page and try again.")


# --------------------------------------------------------------------------- rate limiting
_hits: dict[str, deque] = defaultdict(deque)
_lock = Lock()


def rate_limited(bucket: str, limit: int, window_seconds: int) -> bool:
    """Simple in-process sliding window. Returns True if the caller is over the limit.
    (Behind several workers use a shared store such as Redis — see README.)"""
    if current_app.config.get("TESTING") and not current_app.config.get("RATE_LIMIT_IN_TESTS"):
        return False
    key = f"{bucket}:{request.headers.get('X-Forwarded-For', request.remote_addr or '').split(',')[0].strip()}"
    now = time.monotonic()
    with _lock:
        q = _hits[key]
        while q and q[0] < now - window_seconds:
            q.popleft()
        if len(q) >= limit:
            return True
        q.append(now)
    return False


def reset_rate_limits() -> None:
    with _lock:
        _hits.clear()


# --------------------------------------------------------------------------- signed tokens

def _serializer(salt: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=salt)


def make_token(salt: str, data) -> str:
    return _serializer(salt).dumps(data)


def read_token(salt: str, token: str, max_age: int):
    try:
        return _serializer(salt).loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None


def booking_access_link(booking: Booking, days: int = 60) -> str:
    """Secure link a guest can use to open their booking. Contains a signed, expiring token —
    the reference alone is never enough."""
    token = make_token("booking-access", {"b": booking.id, "k": booking.access_key[:12]})
    return url_for("booking.access", token=token, _external=True)


def grant_booking(booking: Booking) -> None:
    allowed = set(session.get("bookings", []))
    allowed.add(booking.id)
    session["bookings"] = sorted(allowed)[-30:]


def can_view_booking(booking: Booking) -> bool:
    """Only the customer account that owns the booking, or staff, may see it."""
    user = g.get("user")
    return user is not None and (user.is_staff or booking.user_id == user.id)


# --------------------------------------------------------------------------- uploads
IMAGE_EXT = {"jpg", "jpeg", "png", "webp"}
DOC_EXT = IMAGE_EXT | {"pdf"}
MAGIC = {
    "jpg": [b"\xff\xd8\xff"], "jpeg": [b"\xff\xd8\xff"], "png": [b"\x89PNG"],
    "webp": [b"RIFF"], "pdf": [b"%PDF"],
}


def save_upload(file_storage, folder: str, allowed: set[str]) -> str | None:
    """Validates extension, size (MAX_CONTENT_LENGTH) and file signature, stores under a random name.
    Returns the stored relative path or raises ValueError with a user-safe message."""
    if not file_storage or not file_storage.filename:
        return None
    name = secure_filename(file_storage.filename)
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in allowed:
        raise ValueError(f"File type not allowed. Use: {', '.join(sorted(allowed))}.")
    head = file_storage.stream.read(8)
    file_storage.stream.seek(0)
    if not any(head.startswith(m) for m in MAGIC.get(ext, [])):
        raise ValueError("That file doesn't look like a valid " + ext.upper() + ".")
    rel = f"{folder}/{secrets.token_hex(12)}.{ext}"
    dest = os.path.join(current_app.config["UPLOAD_FOLDER"], rel)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    file_storage.save(dest)
    if ext in {"jpg", "jpeg", "png", "webp"}:
        _optimise_image(dest)
    return rel


def _optimise_image(path: str) -> None:
    """Resize large photos and strip metadata (incl. GPS) — privacy and page speed."""
    try:
        from PIL import Image, ImageOps
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im)
            im.thumbnail((1800, 1800))
            fmt = "JPEG" if path.lower().endswith(("jpg", "jpeg")) else None
            if fmt == "JPEG" and im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            im.save(path, format=fmt, quality=82, optimize=True) if fmt else im.save(path, optimize=True)
    except Exception:  # noqa: BLE001 — optimisation is best effort, validation already passed
        pass


# --------------------------------------------------------------------------- audit

def audit(action: str, entity: str | None = None, entity_id=None, detail: str | None = None) -> None:
    user = g.get("user")
    db.session.add(AuditLog(user_id=user.id if user else None, action=action, entity=entity,
                            entity_id=str(entity_id) if entity_id is not None else None, detail=detail,
                            ip_address=(request.remote_addr if request else None)))
