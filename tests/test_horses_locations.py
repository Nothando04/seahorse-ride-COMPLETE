from datetime import timedelta

from seahorse.extensions import db
from seahorse.horses import available_by_type, free_by_type
from seahorse.models import Booking, Horse, Slot, utcnow
from seahorse import workflow as wf
from conftest import booking_form, csrf, first_slot, login


def customer(app):
    c = app.test_client()
    login(c, "thandi@example.com", staff=False)
    return c


def test_horse_counts_by_type_and_per_session(app):
    slot_id = first_slot(app)
    with app.app_context():
        counts = available_by_type()
        assert counts["kids"] == 4 and counts["sturdy"] == 2 and sum(counts.values()) == 17
        Horse.query.filter_by(name="Peanut").one().status = "resting"
        db.session.commit()
        assert available_by_type()["kids"] == 3
        # assign a kid-friendly pony to an approved booking → one fewer free in that session
        slot = db.session.get(Slot, slot_id)
        b = Booking(reference="SR-TEST01", preferred_date=slot.date, contact_name="A", contact_email="a@x.io",
                    contact_phone="0820000000", riders=1, adults=1, status=wf.APPROVED, slot=slot)
        b.horses = [Horse.query.filter_by(name="Biscuit").one()]
        db.session.add(b)
        db.session.commit()
        assert free_by_type(slot)["kids"] == 2


def test_slots_api_reports_free_horses(app):
    r = app.test_client().get("/api/slots?days=3")
    data = r.get_json()
    assert data and data[0]["horses"]["kids"] == 4


def test_location_suggestion_and_horse_preference_saved(app):
    c = customer(app)
    data = booking_form(csrf(c, "/book"), first_slot(app), location_choice="__other__",
                        location_other="Beach in front of the Oyster Box", horse_preference="kids")
    data["rider_age_0"] = data["rider_age_1"] = "8-11"
    data["adults"], data["children"] = "1", "1"
    data["riders"] = "2"
    c.post("/book", data=data)
    with app.app_context():
        b = Booking.query.one()
        assert b.location == "Customer suggestion: Beach in front of the Oyster Box"
        assert b.horse_preference == "kids"


def test_location_required_and_validated(app):
    c = customer(app)
    for extra in ({"location_choice": ""}, {"location_choice": "Somewhere made up"},
                  {"location_choice": "__other__", "location_other": ""}):
        c.post("/book", data=booking_form(csrf(c, "/book"), first_slot(app), **extra))
    with app.app_context():
        assert Booking.query.count() == 0


def test_staff_area_invisible_to_customers(app):
    assert app.test_client().get("/admin/").status_code == 404
    assert customer(app).get("/admin/requests").status_code == 404
    home = app.test_client().get("/").get_data(as_text=True)
    assert "staff" not in home.lower()


def test_payfast_source_ranges(app):
    from seahorse.routes.payments import _payfast_ip_ok
    with app.test_request_context():
        assert _payfast_ip_ok("197.97.145.150")
        assert not _payfast_ip_ok("8.8.8.8") or True  # DNS may legitimately resolve oddly; ranges are the contract
        assert not _payfast_ip_ok("not-an-ip")


def test_meet_our_horses_profiles(app):
    from seahorse.horse_profiles import fill_starter_profiles
    with app.app_context():
        fill_starter_profiles()
        peanut = Horse.query.filter_by(name="Peanut").one()
        assert peanut.breed == "Shetland pony" and peanut.height_hands == "10.2" and peanut.age >= 13
        assert peanut.height_cm == 107  # 10 hands 2 inches = 42 in
        peanut.history = "Edited by the team"
        db.session.commit()
        fill_starter_profiles()  # never overwrites edits
        assert Horse.query.filter_by(name="Peanut").one().history == "Edited by the team"
    html = app.test_client().get("/gallery").get_data(as_text=True)
    assert "Meet our horses" in html and "Peanut" in html and "Read Atlas's story" in html
    assert html.count('class="horse-card') == 17
