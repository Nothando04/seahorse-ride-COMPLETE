"""Command-line tools: ``flask seed``, ``flask create-admin``, ``flask run-jobs``."""
from __future__ import annotations

from datetime import timedelta

import click
from flask import Flask, current_app

from .extensions import db
from .models import ROLES, AddOn, Experience, Horse, Slot, User, Waiver, utcnow


def register_cli(app: Flask) -> None:
    @app.cli.command("bst")
    @click.argument("values", nargs=-1, required=True)
    @click.option("--search", "find", default=None, help="A value to search for.")
    def bst_cmd(values, find):
        """Build a binary search tree from VALUES (in order) and print traversals, e.g.  flask --app app bst 50 30 70 --search 60"""
        from .structures import BinarySearchTree, parse_keys
        keys = parse_keys(" ".join(values))
        tree = BinarySearchTree()
        for k in keys:
            path = tree.insert(k)
            where = ("root" if not path else "duplicate (not added)" if k == path[-1]
                     else f"{'left' if k < path[-1] else 'right'} of {path[-1]}")
            click.echo(f"insert {k!s:>6}: " + " -> ".join(map(str, path)) + (" => " if path else "") + where)
        click.echo(f"\nroot={tree.root.key}  nodes={len(tree)}  height={tree.height()}  leaves={tree.leaves()}"
                   + ("  (SKEWED)" if tree.is_skewed() else ""))
        for depth, level in enumerate(tree.level_order()):
            click.echo(f"  level {depth}: {level}")
        click.echo(f"in-order:   {tree.inorder()}")
        click.echo(f"pre-order:  {tree.preorder()}")
        click.echo(f"post-order: {tree.postorder()}")
        if find is not None:
            target = parse_keys(find, limit=1)[0]
            node, path = tree.search(target)
            click.echo(f"search {target}: {' -> '.join(map(str, path))} -> {'FOUND' if node else 'NOT FOUND'}"
                       f" ({len(path)} comparisons)")

    @app.cli.command("seed")
    @click.option("--demo-prices", is_flag=True, help="Fill example prices so the estimator can be demonstrated. "
                  "Replace them with the business's real prices before launch.")
    @click.option("--days", default=45, help="How many days of ride sessions to create.")
    def seed_cmd(demo_prices: bool, days: int):
        """Create the package catalogue, add-ons, sample ride sessions and a draft waiver."""
        seed(demo_prices=demo_prices, days=days)
        click.echo("Seed complete.")

    @app.cli.command("create-admin")
    @click.argument("email")
    @click.argument("name")
    @click.option("--role", type=click.Choice(ROLES), default="super_admin")
    @click.password_option()
    def create_admin(email: str, name: str, role: str, password: str):
        """Create (or promote) a staff account."""
        user = User.query.filter_by(email=email.lower()).first()
        if user is None:
            user = User(email=email.lower(), name=name, email_verified=True)
            db.session.add(user)
        user.role = role
        user.set_password(password)
        db.session.commit()
        click.echo(f"{email} is now {role}.")

    @app.cli.command("prepare")
    def prepare_cmd():
        """Production start-up: apply migrations, load the catalogue/horses on first run, create the admin
        from ADMIN_EMAIL / ADMIN_PASSWORD. Safe to run on every deploy."""
        import os
        from app import prepare_database
        prepare_database(demo_prices=os.environ.get("SEED_DEMO_PRICES", "true").lower() != "false")
        click.echo("Database ready.")

    @app.cli.command("fetch-horse-photos")
    @click.option("--refresh", is_flag=True, help="Download again even if a horse already has a photo.")
    @click.option("--starter", is_flag=True, help="Use the 17 starter horses (no database needed — for build steps).")
    def fetch_horse_photos_cmd(refresh: bool, starter: bool):
        """Download a real, freely licensed breed photo for each horse (Wikimedia Commons)."""
        import os
        from .horse_photos import fetch_horse_photos
        names = [h[1] for h in STARTER_HORSES] if starter else [h.name for h in Horse.query.all()]
        r = fetch_horse_photos(os.path.join(current_app.static_folder, "img", "horses"), names, click.echo, refresh)
        click.echo(f"{len(r['downloaded'])} downloaded, {len(r['failed'])} unavailable.")

    @app.cli.command("fetch-photos")
    @click.option("--all", "refresh", is_flag=True, help="Download again even if the photo already exists.")
    def fetch_photos_cmd(refresh: bool):
        """Download the horse photos into static/img/photos/."""
        import os
        from .photos import fetch_photos
        result = fetch_photos(os.path.join(current_app.static_folder, "img", "photos"), click.echo, only_missing=not refresh)
        click.echo(f"{len(result['downloaded'])} downloaded, {len(result['failed'])} unavailable.")

    @app.cli.command("run-jobs")
    def run_jobs_cmd():
        """Expire overdue payments, send reminders, retry emails."""
        from .jobs import run_jobs
        base = current_app.config.get("PUBLIC_SITE_URL") or "http://localhost:5000"
        with current_app.test_request_context(base_url=base):
            click.echo(run_jobs())


PACKAGES = [
    # slug, kind, name, summary, duration, basis, min, max, min_age, difficulty, demo price (cents)
    ("beach-ride", "ride", "Guided Beach Ride", "An unhurried guided ride along the shoreline — ideal for first-timers.",
     60, "per_person", 1, 8, 8, "Beginner friendly", 65000),
    ("sunset-ride", "ride", "Sunset Beach Ride", "Ride the golden hour at the water's edge as the sun goes down.",
     90, "per_person", 1, 8, 12, "Beginner friendly", 85000),
    ("couples", "package", "Couples Experience", "A private ride for two — perfect for anniversaries and proposals.",
     90, "per_group", 2, 2, 16, "All levels", 180000),
    ("small-group", "package", "Small Group Adventure", "A shared adventure for a small group of friends or travellers.",
     90, "per_person", 3, 6, 12, "All levels", 70000),
    ("family-fun", "package", "Family Fun", "A gentle family ride with extra guide support for younger riders.",
     60, "per_person", 2, 8, 6, "Beginner friendly", 60000),
    ("friends-celebration", "package", "Friends & Celebration", "Celebrate together on horseback — great for "
     "bachelorettes, reunions and milestone moments.", 120, "per_person", 4, 12, 16, "All levels", 75000),
    ("tourist", "package", "Tourist Experience", "Discover the coastline with a guide who knows its stories.",
     90, "per_person", 1, 8, 10, "Beginner friendly", 80000),
    ("birthday-party", "package", "Birthday & Private Party", "A private ride session built around your celebration.",
     120, "quote", 4, 20, 6, "All levels", None),
    ("corporate", "package", "Corporate & Special Events", "Team outings and client experiences with tailored logistics.",
     180, "quote", 6, 30, 16, "All levels", None),
    ("custom", "package", "Custom Experience", "Tell us what you have in mind and we'll design it with you.",
     None, "quote", 1, None, None, "Varies", None),
]

ADDONS = [
    ("Photography", "A guide takes photos during the ride (delivery time confirmed on approval).", "per_booking", 35000),
    ("Picnic basket", "Snacks and drinks after your ride.", "per_person", 18000),
    ("Flowers / décor", "Simple décor for proposals and celebrations — details agreed with our team.", "per_booking", None),
    ("Extra guide", "An additional guide for nervous or younger riders.", "per_booking", 30000),
]


# Starter herd so the site shows real numbers per horse type. Edit names, limits and photos in Admin → Horses.
STARTER_HORSES = [
    ("kids", "Peanut", "Tiny, patient pony who loves children", 45), ("kids", "Biscuit", "Gentle and easy to lead", 50),
    ("kids", "Pebbles", "Calm Shetland cross, great with first-timers", 45), ("kids", "Coco", "Sweet-natured pony", 55),
    ("nervous", "Willow", "Our steadiest mare — unflappable on the beach", 85), ("nervous", "Sage", "Slow, careful and kind", 85),
    ("nervous", "Honey", "Relaxed and reassuring", 80),
    ("beginner", "Storm", "Calm, steady beach horse", 95), ("beginner", "Coral", "Gentle and good with groups", 90),
    ("beginner", "Sandy", "Follows the leader happily", 90), ("beginner", "Misty", "Quiet and willing", 90),
    ("beginner", "Shadow", "Easy-going gelding", 95),
    ("confident", "Breeze", "Forward-going, loves a canter", 90), ("confident", "Blaze", "Energetic and responsive", 85),
    ("confident", "Tide", "Athletic beach galloper", 90),
    ("sturdy", "Atlas", "Big, strong and gentle", 115), ("sturdy", "Duke", "Sturdy draught cross, very calm", 120),
]


def seed_horses() -> None:
    """Adds the starter herd when there are no horses yet (or only the old placeholder)."""
    existing = Horse.query.all()
    if len(existing) == 1 and existing[0].name.startswith("Example horse"):
        db.session.delete(existing[0])
        existing = []
    from .horse_profiles import fill_starter_profiles
    if existing:
        fill_starter_profiles()
        return
    from .models import HORSE_TYPES
    for cat, name, temperament, weight in STARTER_HORSES:
        db.session.add(Horse(name=name, category=cat, temperament=temperament, max_rider_weight_kg=weight,
                             suitable_for=HORSE_TYPES[cat][0], status="available", show_publicly=True))
    db.session.commit()
    fill_starter_profiles()


GALLERY_STARTERS = [("beach-group.jpg", "groups", "Splashing through the surf"),
                    ("beach-sunset.jpg", "couples", "Golden hour on the shoreline"),
                    ("white-horse.jpg", "rides", "All smiles on a white horse"),
                    ("mountain-ride.jpg", "tourists", "Above the clouds")]


def seed_gallery() -> None:
    """Start the ride-photo gallery with the business's own photos (only when the gallery is empty)."""
    from .models import GalleryImage
    if GalleryImage.query.count():
        return
    for name, cat, caption in GALLERY_STARTERS:
        db.session.add(GalleryImage(path=f"gallery/{name}", caption=caption, category=cat,
                                    consent_confirmed=True, is_published=True))
    db.session.commit()


def seed(demo_prices: bool = False, days: int = 45) -> None:
    if Experience.query.count() == 0:
        for i, (slug, kind, name, summary, dur, basis, mn, mx, age, diff, price) in enumerate(PACKAGES):
            db.session.add(Experience(
                slug=slug, kind=kind, name=name, summary=summary, description=summary, duration_minutes=dur,
                price_basis=basis, price_cents=(price if demo_prices else None), min_group=mn, max_group=mx,
                min_age=age, difficulty=diff, sort_order=i * 10, is_featured=slug in {"beach-ride", "couples", "family-fun"},
                inclusions="Experienced guide\nSaddled, beach-trained horse\nHelmet and safety briefing",
                exclusions="Transport to the meeting point\nPhotography (available as an add-on)",
                requirements="Closed shoes and long trousers. Riders must follow the guide's instructions at all times.",
                terms="Bookings are confirmed only after our team approves the request and payment is received.",
                safety_notes="Rider weight, age and experience limits are confirmed by our team for each horse. "
                             "The guide's on-site assessment of conditions is final.",
                availability_note="Subject to tide, weather and horse availability."))
    if AddOn.query.count() == 0:
        for name, desc, basis, price in ADDONS:
            db.session.add(AddOn(name=name, description=desc, price_basis=basis,
                                 price_cents=(price if demo_prices else None)))
    seed_horses()
    if Waiver.query.count() == 0:
        db.session.add(Waiver(version=1, title="Rider acknowledgement (draft — must be reviewed)", is_active=True, body=(
            "[DRAFT — replace with your legally reviewed waiver before launch]\n\n"
            "I understand that horse riding involves inherent risks. I confirm the rider information I have "
            "provided is accurate, that riders will wear the safety equipment provided and follow the guide's "
            "instructions, and that the guide may stop or change the ride for safety reasons.")))
    today = utcnow().date()
    for d in range(1, days + 1):
        day = today + timedelta(days=d)
        for start, end in [("08:00", "09:30"), ("10:30", "12:00"), ("16:30", "18:00")]:
            if not Slot.query.filter_by(date=day, start_time=start).first():
                db.session.add(Slot(date=day, start_time=start, end_time=end, capacity=8))
    db.session.commit()
