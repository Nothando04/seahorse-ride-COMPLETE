"""Business settings stored in the database and editable by a Super Administrator."""
from .extensions import db
from .models import Setting

DEFAULTS: dict[str, str] = {
    "business_name": "Seahorse Ride",
    "tagline": "Beach horse-riding experiences, group packages and private events",
    "contact_email": "",
    "contact_phone": "",
    "whatsapp_number": "",  # international format without +, e.g. 27821234567
    "address": "",  # leave blank until the business confirms it — never invented
    "meeting_point": "",
    "operating_hours": "",
    "facebook_url": "",
    "instagram_url": "",
    "staff_alert_email": "",
    "ops_status": "normal",  # normal | caution | paused
    "ops_message": "",
    "deposit_percent": "50",
    "payment_deadline_hours": "48",
    "reminder_hours": "24",
    "cancellation_policy": (
        "[Business to complete] Describe how much notice customers must give to cancel or reschedule, "
        "what is refunded in each case and how weather cancellations are handled."
    ),
    "weather_latitude": "",
    "weather_longitude": "",
    "limit_wind_kmh": "",  # blank = not configured; the guide's on-site assessment always decides
    "limit_gust_kmh": "",
    "limit_rain_mm": "",
    "limit_temp_min_c": "",
    "limit_temp_max_c": "",
    "weather_template": (
        "Hi {name}, due to weather conditions forecast for {date} we may need to move your Seahorse Ride "
        "booking {reference}. Please reply in your booking messages so we can agree on a new date. "
        "Your payment is safe and will be carried over."
    ),
    "hero_image": "",
    # One suggestion per line. Customers pick one, or suggest their own place.
    "ride_locations": (
        "Main beach — our usual meeting point\n"
        "Quiet beach stretch — fewer people, great for first-timers\n"
        "Sunset shoreline — best for evening and couples rides\n"
        "Dunes and coastal trail\n"
        "Your own venue or event location — we'll check if we can come to you"
    ),
}

OPS_STATUS_LABELS = {"normal": "Normal Operations", "caution": "Caution", "paused": "Rides Paused"}


def get_setting(key: str) -> str:
    row = db.session.get(Setting, key)
    return row.value if row is not None else DEFAULTS.get(key, "")


def all_settings() -> dict[str, str]:
    values = dict(DEFAULTS)
    for row in Setting.query.all():
        values[row.key] = row.value
    return values


def set_setting(key: str, value: str) -> None:
    row = db.session.get(Setting, key)
    if row is None:
        db.session.add(Setting(key=key, value=value))
    else:
        row.value = value


def int_setting(key: str, fallback: int) -> int:
    try:
        return int(get_setting(key))
    except (TypeError, ValueError):
        return fallback
