# Seahorse Ride — Booking & Event Management Website (Python edition)

A full-stack Python (Flask) website for a beach horse-riding business. Customers browse experiences and packages, send booking requests or event enquiries, and **pay only after an administrator has reviewed and approved the request**. Payments go through **PayFast** (South Africa), and a verified server-to-server notification confirms them.

---

## 1. Quick start (Windows PowerShell — macOS/Linux commands in brackets)

You need **Python 3.11+**. Open the folder that contains `app.py`, then:

```powershell
python -m venv venv
venv\Scripts\activate                      # (macOS/Linux: source venv/bin/activate)
python -m pip install -r requirements.txt
copy .env.example .env                      # (macOS/Linux: cp .env.example .env)
python app.py
```

Open **http://127.0.0.1:5000**.

> If `pip` gives "Fatal error in launcher", always use `python -m pip …` (or `py -m pip …`).
> If activation is blocked, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.

On start-up `app.py`:
1. creates the database from the migrations (`instance/seahorse.db`) and loads the packages and 45 days of ride sessions;
2. **creates the administrator account** from `ADMIN_EMAIL` / `ADMIN_PASSWORD` in `.env` and prints the login in the terminal
   (if `ADMIN_PASSWORD` is blank in development the password is `Seahorse@2026` — change it under Profile after signing in);
3. **downloads the horse photos** in the background (see section 1b) and starts the server.

### Who logs in where
| Person | How |
|---|---|
| **Customer** | Must **create an account** (`/account/sign-up`) and **sign in** before booking a ride, planning an event, paying or messaging. Bookings are private to the account that made them. |
| **Administrator / staff** | Sign in at **`http://127.0.0.1:5000/account/staff/sign-in`** — this address is deliberately **not linked anywhere** on the public site; bookmark it. Customer accounts are refused there, and every `/admin` page shows "Page not found" to visitors and customers, so clients never see staff information. Add more staff under Admin → Users & roles. |

### 1b. Horse photos
The first time the site starts it downloads its photos into `static/img/photos/` with Python (`seahorse/photos.py`):
your four horse photos from the original Seahorse Lovable project (if that project's preview is reachable) and free
beach-riding photos from Unsplash (Unsplash License). Pillow checks, resizes and optimises each one. Re-run any time:

```powershell
flask --app app fetch-photos
```

Prefer your own photos? Any of these works, and they take priority:
- drop JPG/PNG files into `static/img/photos/` (any names — they're used across the site), or
- Admin → **Settings** (home-page hero photo), Admin → **Packages** (a photo per package), Admin → **Gallery**, Admin → **Horses**.

Without internet access the site falls back to its illustrated artwork, so it always works.

**Horse photos (Gallery → Meet our horses).** On first start the site also downloads one real, freely licensed photo
per horse from Wikimedia Commons, matched to the horse's breed and colour (`seahorse/horse_photos.py`). The
photographer and licence are shown on each card, as the licences require. These are photos of the breed, not of your own horses.
Upload real photos of your horses in **Admin → Horses** and they replace the breed photos straight away.
Re-run with `flask --app app fetch-horse-photos` (add `--refresh` to download again).

> Demo prices are filled in so the estimator works — replace them in Admin → Packages & pricing before launch
> (or set `SEED_DEMO_PRICES=false` before the first run).

## Sharing the site & the digital business card

**Public link while running on your laptop.** When you run `python app.py`, the site also gets a free, temporary
public address (`https://….trycloudflare.com`). It's printed in the terminal in a box, with a QR code. Anyone can open it on
their phone, anywhere, for as long as your laptop is on and the site is running. The address changes every time you restart.

For safety, developer/debug mode switches off automatically while the public link is on. Set `PUBLIC_LINK=false` in `.env` to keep the site private to your computer.

**Permanent public address.** Deploy the site (section 6, *Going live*). You get `https://<name>.onrender.com`, or
your own domain, which works even when your laptop is off.

**Digital business card: `/card`.** A phone-friendly card with:
- the logo, plus Book / Plan an event / WhatsApp / Call / Email / Directions / Meet the horses buttons
- a **Save contact** button (adds the business to the phone's contacts)
- a **QR code**, plus Download QR (PNG, with the logo) and Print business cards (8 standard 85 × 55 mm cards on A4, front and back)

The QR code always points at the address the site is live on right now: your domain, else today's public link, else
this computer. If it's on this computer only, the card warns you that other people can't scan it.

The contact buttons use the details in **Admin → Settings** (phone, email, WhatsApp, meeting point, hours, socials).
Staff open the card from the admin menu (*Business card & QR*).

**Print the QR on flyers or posters only after deploying.** The temporary link changes on every restart, so a printed QR pointing at it would stop working.

## 2. The core rule: approval before payment

```
Customer submits request ──► Pending Admin Approval   (no charge, no capacity reserved)
        │
Admin checks date, group, horses, guide, safety, price
        ├─► Awaiting Customer Information ─(customer replies)─► Pending
        ├─► Changes Proposed ─(customer accepts)─► Pending
        ├─► Declined
        └─► Approved — Awaiting Payment   (capacity held until the deadline; payment link emailed)
                 │
       PayFast ITN verified on the server ──► Confirmed — Paid ──► Completed / No-Show
                 │                                   ├─► Rescheduling Requested ─► Rescheduled
       deadline passes ──► Payment Expired           └─► Cancelled ─► Refund Pending ─► (Partially) Refunded
```

These rules are enforced on the server, so they don't depend on the screens:

- Payment pages refuse to open until an admin has approved the booking and set a final price.
- Before approving, the admin must tick four confirmations: date/time, group size, price and operational availability.
- A booking is marked paid **only** through one of two routes:
  - A PayFast ITN that passes every check: signature → merchant ID → PayFast "VALID" confirmation → payment reference → exact amount.
  - A manager recording an EFT or cash payment they have verified in the bank account.
- The browser's "return" redirect is never trusted, and neither is a customer's screenshot or button click.
- If PayFast sends the same notification twice, the second one is ignored.
- Capacity is checked inside a database lock when a booking is approved, extended or rescheduled. Two admins can't sell the same last places. This is covered by a concurrency test.
- Every status change goes into the timestamped history, with who made it and why.

All 17 statuses from the brief are implemented (`seahorse/workflow.py`).

---

## 3. Payments — Paystack card checkout (card number, expiry date, CVV)

The site uses **Paystack**'s official hosted checkout. After an admin approves a booking, the customer clicks
**Pay R… securely with Paystack** and types their **card number, expiry date and CVV on Paystack's own page**
(checkout.paystack.com). Card details never reach this site.

**Set it up (test mode, free, about 5 minutes):**

1. Create an account at https://paystack.com and choose **South Africa** as the business country (needed for ZAR).
2. Dashboard → **Settings → API Keys & Webhooks**. Make sure the toggle says **Test mode**, then copy the
   **Test Secret Key** (`sk_test_…`) and **Test Public Key** (`pk_test_…`).
3. Put them in `.env` (on your computer) or Render → Environment (live site):
   `PAYSTACK_SECRET_KEY=sk_test_…` and `PAYSTACK_PUBLIC_KEY=pk_test_…`. With `PAYMENT_PROVIDER=auto` (the default),
   the site switches to Paystack as soon as the secret key is there.
4. Optional, live site only: on the same Paystack page set **Test Webhook URL** to
   `https://<your site>/payments/paystack/webhook`.

**How a payment is confirmed:** when the customer comes back, the server asks Paystack's API (with the secret key)
what happened and checks the reference, status, currency and exact amount before the booking becomes
*Confirmed — Paid*. The webhook is a second, independent confirmation, checked with Paystack's HMAC-SHA512
signature. Both are idempotent, so a payment is never counted twice. This works on `localhost` too.

**Test cards (test mode only — no real money):**

| Card number | Expiry | CVV | Result |
|---|---|---|---|
| 4084 0840 8408 4081 | any future date, e.g. 12/30 | 408 | Success (if asked for a PIN: 0000) |
| 4084 0800 0000 0409 | any future date | 408 | Declined |
| 4084 0800 0000 0005 | any future date | 408 | Insufficient funds |

Paystack's dashboard can also show its own current list of test cards. **Going live** = Paystack business
verification, then swap in the `sk_live_…` / `pk_live_…` keys. No code changes.

### 3a. PayFast (still supported)


Customers pay on **PayFast's own secure page** (payfast.co.za): card (Visa/Mastercard), Instant EFT and the other
methods PayFast offers. Card details are typed on PayFast's site and never touch this website.

| `.env` setting | Result |
|---|---|
| `PAYMENT_PROVIDER=payfast`, `PAYFAST_SANDBOX=true` | Real PayFast page in **sandbox** mode — use PayFast's test card details, no real money moves. |
| `PAYMENT_PROVIDER=payfast`, `PAYFAST_SANDBOX=false` + live merchant ID/key/passphrase | **Live** card payments into the business's PayFast account. |
| `PAYMENT_PROVIDER=simulator` | Offline look-alike for coding without internet only (refused in production). |

**How a payment is confirmed:** after the customer pays, PayFast's servers call the site's ITN webhook
(`/payments/payfast/notify`). The site checks the signature, the merchant ID, asks PayFast to confirm the
notification ("VALID"), checks the reference and exact amount, and only then marks the booking *Confirmed — Paid*.

**Running on your own computer:** PayFast can't call `localhost`, so `python app.py` automatically opens a free,
temporary public HTTPS address (Cloudflare quick tunnel, via the `pycloudflared` package, no account needed)
and gives it to PayFast as the notification address. The terminal shows
`PayFast notifications → https://….trycloudflare.com/payments/payfast/notify`, and Admin → Payments & refunds shows
whether PayFast can reach the site. Customers keep using `http://127.0.0.1:5000`.
If the tunnel can't start (firewall/no internet), payments still open on PayFast, and staff can record a payment
they can see in the PayFast dashboard (Admin → booking → *Record EFT / cash payment*).

**Testing in the sandbox:**
1. Optional but recommended: create a free account at https://sandbox.payfast.co.za and put your sandbox
   merchant ID, key and passphrase in `.env` (blank = PayFast's public test merchant `10000100`).
2. Book a ride as a customer → approve it as admin → as the customer click **Pay securely with PayFast**.
3. On the PayFast sandbox page, complete the payment with PayFast's test details.
4. You're returned to the site; within a few seconds the booking shows **Confirmed — Paid**, and the ITN is
   listed under Admin → Payments & refunds.

**Going live:** register the business with PayFast (merchant account), set `PAYFAST_SANDBOX=false` and the live
`PAYFAST_MERCHANT_ID`, `PAYFAST_MERCHANT_KEY` and `PAYFAST_PASSPHRASE` (PayFast dashboard → Settings → Integration),
deploy on an HTTPS domain and set `PUBLIC_SITE_URL` — the tunnel is then not used.

Refunds are recorded in admin (Refund Pending → Refunded / Partially Refunded); the money itself is returned from the PayFast dashboard.

## 3b. Horse types and riding locations

**Horses by type.** Every horse has a type, and the site shows how many of each are working right now:

| Type | Starter herd |
|---|---|
| Kid-friendly ponies | 4 |
| Extra-calm horses (nervous / first-time riders) | 3 |
| Beginner-friendly horses | 5 |
| Horses for confident riders | 3 |
| Sturdy horses for taller riders | 2 |

- The counts appear on the home and Experiences pages ("A horse for every rider").
- In the booking and event forms the customer picks the type that suits their group. The numbers then update to
  show how many of each type are **free in the session they chose** (horses already assigned to approved or paid
  bookings in that session aren't counted).
- Horses marked resting, maintenance or retired are left out automatically.
- Staff see the customer's preference on each request, with horses grouped by type for assignment.
- Edit names, types, weight limits, photos and status in **Admin → Horses**. The starter herd is example data: replace it with the real horses.

**Where to ride.** The booking and event forms ask *"Where would you like to ride?"*. The customer picks a
suggested spot or chooses **Suggest another place** and types their own idea. Staff see it on the request.
Edit the suggestions in **Admin → Settings → Suggested riding locations**, one per line; text after " — " shows as a short description.

## 4. Features (mapped to the brief)

- **Public site.** Includes:
  - Home: hero, featured experiences, how booking works, packages, gallery, genuine reviews only, safety, FAQs, contact.
  - Experiences, Packages (the 8 packages from the brief, all editable) and package detail pages.
  - Gallery, Contact, and Safety / Terms / Cancellation / Privacy (POPIA) pages.
  - Floating WhatsApp button, click-to-call, click-to-email and an essential-cookies notice.
  - SEO: titles, descriptions, Open Graph tags, `sitemap.xml`, `robots.txt` and structured data.
- **6-step booking form.** Steps are: experience → date (live remaining places per session) → group and each rider's age group and experience → extras with a live estimate → contact → review.
  - It also works without JavaScript.
  - Fields are validated on the server.
  - Duplicate submissions are blocked.
  - The age and group-size rules for each package are enforced.
- **Plan Your Event.** All the fields from the brief, plus an optional brief or image upload (type and size checked, stored privately). School and youth groups must describe their safeguarding arrangements.
- **Customer area.** Covers:
  - Customer accounts (sign-up, sign-in, email confirmation, password reset).
  - Progress tracker and a message thread per booking.
  - Accept proposed changes, pay a deposit or the balance, printable receipt, add-to-calendar.
  - Request a new date, cancel (free before payment), digital waivers (a parent or guardian signs for under-18s, and the waiver version is recorded), and a review after a completed ride.
  - Profile, marketing preferences and POPIA account deletion.
- **Accounts required.** Customers must sign up and sign in to book; a booking (and its payment page) opens only for the account that owns it, or for staff. Links in emails ask the customer to sign in first.
- **Admin dashboard.** All the widgets from the brief, plus these areas:
  - **Requests:** filter by status, date, type, event type, package or customer, with full approve, decline, ask-for-info, propose-changes, cancel, reschedule, complete and no-show actions. Horse and guide assignment includes conflict checks.
  - **Messages:** customer messages, read/unread, and private admin notes the customer never sees.
  - **Availability calendar:** create seasonal sessions in bulk, set capacity, block a session or a whole day (weather, private event, horse rest).
  - **Horses:** status, suitability and weight limits.
  - **Packages and add-ons**, **Payments** with the ITN log, **Refunds**, **Enquiries**, **Email outbox** with retry.
  - **Reviews** moderation and **Gallery** (consent-aware publishing; photos are resized and GPS data removed).
  - **Waivers:** versioned.
  - **Weather & safety:** operations status (Normal / Caution / Rides Paused, shown as a site banner), a 7-day Open-Meteo forecast checked against limits *you* configure, and weather notices to every booking on a date.
  - **Reports** with CSV export (opens in Excel), **Users & roles**, **Settings**, **Audit log**.
- **Roles.** Customer, Staff/Guide, Booking Administrator, Manager and Super Administrator, each with least-privilege permissions checked on every admin route on the server.
- **Notifications.** Emails go out for: request received (to the customer and to staff), more info needed, changes proposed, approved with payment link, payment received, payment failed, deadline expired (to both sides), new messages, reschedules, cancellations, refunds, ride reminders and feedback requests.
  - Every email is stored in the outbox first and failed sends are retried.
  - Without SMTP settings, emails are kept in **Admin → Email outbox** instead of being sent.

### Security

- Passwords hashed (Werkzeug PBKDF2), session renewed on login, secure/HttpOnly/SameSite cookies.
- CSRF tokens on every form; security headers including CSP and HSTS in production.
- Rate limiting on login, booking, lookup and contact forms.
- SQLAlchemy parameterised queries; Jinja auto-escaping.
- Uploads checked by extension *and* file signature, given random names, and private files served to staff only.
- CSV exports protected against formula injection.
- Audit log of staff actions.
- Secrets only in environment variables.

---

## 5. Project structure

```
seahorse-ride/
├── app.py                  # entry point: migrate + seed + create admin + fetch photos + start server
├── seahorse/
│   ├── __init__.py         # app factory, config, security headers, hooks
│   ├── models.py           # database schema (SQLAlchemy) + roles/permissions
│   ├── workflow.py         # statuses, transitions, capacity locking, approval & payment rules
│   ├── payfast.py          # PayFast signing, checkout and ITN verification
│   ├── notify.py           # email outbox + SMTP + all notification texts
│   ├── security.py         # auth, RBAC, CSRF, rate limits, signed links, uploads, audit
│   ├── photos.py           # downloads/optimises horse photos (Pillow) and picks them for each page
│   ├── jobs.py             # deadline expiry, reminders, email retry
│   ├── cli.py              # flask seed / create-admin / run-jobs
│   └── routes/             # public, booking, account, payments, admin
├── templates/              # Jinja HTML (public site, customer area, admin)
├── static/                 # CSS, JS (progressive enhancement), SVG artwork
├── migrations/             # Alembic database migrations
├── database/               # demo SQLite DB, SQL schema (PostgreSQL + SQLite), ER diagram — see database/README.md
└── tests/                  # 27 automated tests (pytest)
```

Run the tests with:

```bash
python -m pytest -q
```

They cover the full lifecycle, the simulator and ITN flow, forged or underpaid or wrong-merchant ITNs, duplicate ITNs, deposit + balance, overbooking (including two concurrent approvals), deadline expiry, guest access, role permissions, CSRF, internal notes, refunds, waivers, reviews and the migrations.

---

## 6. Going live — a permanent link (Render)

### 6a. Fastest: a live link for a submission or demo (about 10 minutes, free)

Only two accounts are needed: GitHub and Render. The site starts with example data (bookings in every status, horses, payments, reviews), uses PayFast **sandbox** test payments, and downloads the horse photos while it builds.

1. **GitHub.** Sign in at https://github.com → **New repository** → name it `seahorse-ride`, choose **Private**, create it.
   On the new page click **uploading an existing file** and drag in everything inside the project folder
   **except** `.env`, `instance`, `venv` and `__pycache__` (never upload `.env` — it holds your secrets). Click **Commit changes**.
   GitHub's web upload takes up to 100 files at a time, so do it in two goes: first the `templates` and `static` folders,
   then **Add file → Upload files** again for everything else. (GitHub Desktop or `git push` also work.)
2. **Render.** Sign in at https://render.com with your GitHub account → **New → Blueprint** → pick `seahorse-ride`
   (if it isn't listed, click *Configure account* and give Render access to the repository).
3. Render reads `render.yaml` and asks for a few values. Fill in:
   - `ADMIN_EMAIL` — your email, `ADMIN_PASSWORD` — 10+ characters (this is your admin sign-in)
   - leave everything else **blank** (PayFast sandbox test details are built in; `DATABASE_URL` is optional — see 6b)
4. Click **Apply**. The first build takes about 5 minutes. When the service shows **Live**, your link is at the top:
   `https://seahorse-ride.onrender.com` (or similar). That is the URL to submit.
5. Open `https://<your-link>/card` for the digital business card — its QR code now points to the permanent link.

Sign in as admin at `/account/staff/sign-in` with `ADMIN_EMAIL` / `ADMIN_PASSWORD`. The demo accounts
(`admin@seahorse.test`, `manager@seahorse.test`, `bookings@seahorse.test`, `guide@seahorse.test`, `customer@seahorse.test`)
use `DEMO_PASSWORD`, or your `ADMIN_PASSWORD` when that is blank. Test card payments use PayFast's sandbox page —
no real money moves. Set `DEMO_DATA=false` in Render → Environment to start with an empty system instead.

**Without a separate database** the site uses a built-in SQLite file. It works, but on the free plan it is reset to the
demo data whenever the server restarts or wakes up from sleep — fine for a demo, not for a real business.

### 6b. Keep data permanently (free) and send real emails

1. **Database.** Create a project at https://neon.tech (free, doesn't expire), copy the connection string (`postgresql://…`)
   and paste it into Render → Environment → `DATABASE_URL`. Render's own free database is deleted after 30 days, so it isn't used.
2. **Email.** Create a free account at https://www.brevo.com, verify your sender email, and create an API key.
   Set `BREVO_API_KEY` and `MAIL_FROM` (e.g. `Seahorse Ride <you@yourdomain.co.za>`). Render's free plan blocks normal SMTP ports.
3. Leave `PUBLIC_SITE_URL` blank until you have a custom domain; Render's address is used automatically.
4. Every start runs `flask --app app prepare`: it applies the migrations, loads the data on the very first run, and makes sure the admin exists.
5. **Go live with real money:** register and verify the business at PayFast, then set `PAYFAST_SANDBOX=false` and the live
   merchant ID, key and passphrase in Render → Environment, and `DEMO_DATA=false` on a fresh database.
6. **Custom domain (optional):** Render → Settings → Custom Domains, add the DNS records it shows, then set `PUBLIC_SITE_URL=https://yourdomain.co.za`.

**Free plan limits (Render):**
- The site sleeps after 15 minutes without visitors, and the first visit then takes about a minute to load.
- Photos uploaded through the admin area are lost on restart. The photos in `static/img/photos/` are kept, because they're part of the code: commit new photos there.

For a real business, switch the web service to a paid instance so it's always on, and add a persistent disk for uploads.

Other hosts work too. The included `Dockerfile` and `Procfile` cover Railway, Fly.io, a VPS and similar. On any of them:
- run `flask --app app prepare` on deploy
- start with `gunicorn app:app`
- set `APP_ENV=production`

### Honest notes

- The legal pages and the waiver are **templates**. The business (and, where needed, a lawyer) must review them. Nothing in the app invents prices, reviews, addresses or safety thresholds.
- The weather forecast is advisory. The guide's on-site assessment always decides. Tide and surf data are not included.
- WhatsApp is a click-to-chat link. Automated WhatsApp messages would need an authorised WhatsApp Business API provider and customer consent.
- SMS is not built in. Email is the primary channel, as the brief allows.
