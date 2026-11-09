# Seahorse Ride — Database

The demo database contains **all 20 tables, every one filled with example data**:
- 19 users across all roles
- 14 bookings covering 12 statuses
- 17 horses in 5 types
- payments, a refund, waivers, a review, messages, emails, the gallery, settings and the audit log

It comes in three versions. Pick the one for the database program you use.

| File | Use it with |
|---|---|
| `seahorse_demo.db` | **SQLite**: open it in [DB Browser for SQLite](https://sqlitebrowser.org) (free) → *Browse Data* tab → choose a table. Nothing to set up. |
| `seahorse_demo_postgresql.sql` | **PostgreSQL**: pgAdmin, Neon, Supabase, `psql` |
| `seahorse_demo_mysql.sql` | **MySQL / MariaDB**: MySQL Workbench, phpMyAdmin, XAMPP |
| `schema_postgresql.sql`, `schema_sqlite.sql` | Empty tables only (structure, no data) |
| `erd.png` | Diagram of how the tables connect |

The `.sql` files create every table (with keys, constraints and indexes) and insert every row.
Both were tested by loading them into real PostgreSQL 16 and MariaDB servers.

## Opening each version
**SQLite:** in DB Browser for SQLite, choose *Open Database* → `seahorse_demo.db`.

**PostgreSQL (pgAdmin):**
1. Create a database called `seahorse`.
2. Right-click it → *Query Tool* → open `seahorse_demo_postgresql.sql` → Run (F5).
3. Find the tables under *Schemas → public → Tables* (right-click a table → *View/Edit Data → All Rows*).

From the command line instead: `psql -d seahorse -f seahorse_demo_postgresql.sql`

**MySQL Workbench / phpMyAdmin:**
1. Create a database called `seahorse` (utf8mb4).
2. Load the file:
   - Workbench: *File → Open SQL Script* → `seahorse_demo_mysql.sql`, then Execute.
   - phpMyAdmin: *Import* tab → choose the file → Go.

From the command line instead: `mysql seahorse < seahorse_demo_mysql.sql`

## Running the website on the demo data
- **SQLite:** copy `seahorse_demo.db` to `instance/seahorse.db` (create the `instance` folder if needed), then `python app.py`.
- **PostgreSQL:** load the `.sql` file as above, set `DATABASE_URL=postgresql://user:password@host:5432/seahorse` in `.env`, then `python app.py`.

The website itself uses SQLite or PostgreSQL. The MySQL file is for viewing the data in MySQL tools.

## Demo logins (password `Password123` for all)
| Email | Role | Sign in at |
|---|---|---|
| admin@seahorse.test | Super Administrator | `/account/staff/sign-in` |
| manager@seahorse.test | Manager | `/account/staff/sign-in` |
| bookings@seahorse.test | Booking Administrator | `/account/staff/sign-in` |
| guide@seahorse.test | Staff / Guide | `/account/staff/sign-in` |
| customer@seahorse.test, sipho@example.com, emma@example.com … | Customers | `/account/sign-in` |

Passwords are stored as one-way hashes (in the `password_hash` column), so nobody can read them, not even the admin.

## What's in each table
| Table | Holds |
|---|---|
| `user` | Customer and staff accounts, roles |
| `experience`, `add_on` | Rides, packages and extras with prices |
| `slot` | Ride sessions per day and their rider capacity |
| `horse`, `booking_horses` | Horses (type, weight limit, status) and which horses are assigned to which booking |
| `booking` | Every request and booking: dates, group, riders, location, horse preference, prices, status |
| `status_history` | Audit trail of every status change: who made it, when and why |
| `message` | Customer–team conversation per booking, including private staff notes |
| `payment`, `payment_event`, `refund` | Payments (PayFast or manual), every PayFast notification, refunds. Amounts are in cents (ZAR). |
| `waiver`, `waiver_signature` | Waiver versions and each rider's signature and emergency contact |
| `review`, `gallery_image` | Reviews after completed rides, gallery photos |
| `notification`, `enquiry` | Email outbox, contact-form enquiries |
| `setting`, `audit_log` | Business settings, log of staff actions |
| `alembic_version` | Migration marker (which version of the schema this is) |

## Rebuilding
```
python database/build_demo_db.py     # recreates seahorse_demo.db
python database/export_sql.py        # recreates the PostgreSQL and MySQL .sql files from it
```
