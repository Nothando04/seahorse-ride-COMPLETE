"""Builds database/seahorse_demo.db — a ready-made SQLite database with demo staff accounts,
horses and bookings in different statuses. Run from the project folder:

    python database/build_demo_db.py

To use it as the live database:  copy it to instance/seahorse.db  (or set DATABASE_URL=sqlite:///full/path/seahorse_demo.db)
All demo passwords: Password123
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from flask_migrate import upgrade  # noqa: E402

from seahorse import create_app  # noqa: E402
from seahorse.demo_data import populate_demo  # noqa: E402
from seahorse.extensions import db  # noqa: E402

path = os.path.abspath(os.path.join(os.path.dirname(__file__), "seahorse_demo.db"))
if os.path.exists(path):
    os.remove(path)
app = create_app({"SQLALCHEMY_DATABASE_URI": f"sqlite:///{path}", "TESTING": True, "SERVER_NAME": "localhost:5000"})

with app.app_context():
    upgrade()
    counts = populate_demo("Password123")
    print("Created", path)
    print(counts)
    db.engine.dispose()
