# Tarmac school marketplace API

Django REST backend for a school-only second-hand marketplace, student verification, direct buyer-to-seller requests, lost-and-found reports, and notifications.

## Local setup

Use Python 3.10 or newer. This project targets the supported Django 5.2 LTS series, which receives security fixes through April 2028. Python 3.9 cannot install the requirements.

```bash
python3.12 -m venv --clear .venv
source .venv/bin/activate       # Windows: .venv\\Scripts\\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp -n .env.example .env        # Windows: copy only if .env does not already exist
```

In `.env`, keep `DEBUG=True` for local development and replace `SECRET_KEY` with a private value. For example, generate one with `python -c "import secrets; print(secrets.token_urlsafe(50))"`. Then run:

```bash
python manage.py migrate
python manage.py createsuperuser
python manage.py check
python manage.py runserver
```

API: `http://127.0.0.1:8000/api/` · Admin: `http://127.0.0.1:8000/admin/`.

Initial migrations are included. Do not run `makemigrations` for normal setup; create and commit a new migration whenever a model changes.

## Student verification and access

Registration creates an unverified account and accepts personal email addresses. Students must verify their email, submit a matriculation number and student ID, and receive staff approval before accessing marketplace, messaging, or lost-and-found features. The ID file is stored outside public media and its image route requires staff login. Staff should use Django staff accounts for moderation.

## Marketplace flow

1. A verified student creates a listing. It starts as `pending_payment` and is visible only to its seller.
2. The seller uploads up to five listing images through `/api/products/images/`.
3. The seller calls `POST /api/payments/initialize-listing-fee/` with `{"product": <id>}`. The backend initializes the ₦1,500 fee (150000 kobo) with Paystack.
4. After Paystack confirms payment through the signed webhook at `/api/payments/webhook/`, the listing enters `pending_review`.
5. A school moderator approves listings from Django admin. Approved listings are active for 30 days. A superuser can set `listing_fee_enabled=False` or turn off Paystack in Platform settings; new listings are then free but still await school review. Configure categories and disable categories the school does not allow before opening registration.
6. A buyer requests an active listing. The seller can accept or cancel. Acceptance reserves the listing; completion marks it sold. The buyer can cancel a request before it is accepted. Buyers and sellers arrange the actual hand-off directly; the site does not provide escrow, shipping, or dispute resolution.

Editing an active listing returns it to school review. The seller can retry payment if the listing is still awaiting payment.

Configure `PAYSTACK_SECRET_KEY`, `PAYSTACK_WEBHOOK_SECRET`, and `PAYSTACK_CALLBACK_URL`. Set the webhook URL in the Paystack dashboard and use HTTPS in production. Do not activate listings based on the browser callback; only the verified webhook activates them. Test with Paystack test credentials before live mode. The webhook accepts Paystack's timestamped `Paystack-Signature` format and the legacy `x-paystack-signature` during transition.

## Lost and found and notifications

Only active, verified students can create or view reports and candidate matches. A scheduled worker compares title, description, colour, category, location, and incident dates. A score creates a *possible match*, not proof of ownership. Both reporters receive an in-app notification and, when enabled, an email or opted-in WhatsApp notification. Students review the match through `/api/lost-found/matches/{id}/review/` with `{"decision":"possible_match"}` or `{"decision":"not_a_match"}`. Report owners can close a report at `/api/lost-found/reports/{id}/close/` or confirm return at `/api/lost-found/reports/{id}/returned/`.

Email is written to the console locally. Configure SMTP in production. WhatsApp requires approved templates plus `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, and the relevant `WHATSAPP_*_TEMPLATE` setting. Students must provide an E.164 phone number and explicitly enable WhatsApp notifications in `PATCH /api/accounts/me/`; opting out stops future sends.

Email and WhatsApp delivery is queued in the database so provider latency does not slow API requests. Schedule `python manage.py retry_notifications`, `python manage.py match_lost_found`, and `python manage.py dispatch_announcements` to run every minute. Schedule `python manage.py expire_products` daily. Notifications retry with backoff; database row locks protect workers processing the same item.

## Useful API routes

- `POST /api/accounts/register/`, `GET|PATCH /api/accounts/me/`, `/api/accounts/students/{id}/`, `/api/accounts/verification/`
- `POST /api/auth/token/`, `POST /api/auth/token/refresh/`
- `/api/products/`, `/api/products/categories/`, `/api/products/images/`, `/api/products/favourites/`
- `/api/payments/`, `POST /api/payments/initialize-listing-fee/`, `POST /api/payments/webhook/`
- `/api/ads/`, `POST /api/payments/initialize-ad-fee/`
- `/api/orders/`
- `/api/lost-found/reports/`, `/api/lost-found/matches/`
- `/api/messaging/conversations/`, `/api/messaging/messages/`
- `/api/notifications/` (including `/{id}/mark-read/`)
- `/api/accounts/email/confirm/`, `/api/accounts/email/resend/`, `/api/accounts/password-reset/`, `/api/accounts/password-reset/confirm/`
- `/api/admin-tools/dashboard/`, `/api/admin-tools/reports/`, `/api/admin-tools/announcements/` (POST creates; `/{id}/publish/` queues in-app popups), `/api/admin-tools/logs/`
- Students can report a marketplace listing or student, a lost-and-found post, or a student from a message thread through `POST /api/admin-tools/reports/`. Reports accept up to five `evidence_images`; each image is limited to 5 MB and stored under `PRIVATE_MEDIA_ROOT`. Only the reporter and staff can view report evidence through `/api/admin-tools/report-evidence/{id}/image/`. Staff can review and resolve reports in Tarmac's admin panel.
- `/api/system/status/` reports maintenance, enabled sections, and current fee policy.

## Admin controls

Create a Django superuser for platform controls. Under Platform settings, a superuser can disable the whole site, switch marketplace, lost-and-found, messaging, or ads on and off, and set Paystack plus listing/ad fees. Disabling a fee or Paystack makes new listings/campaigns free while retaining school moderation. Admin staff can review student accounts, listings, ads, lost-and-found reports and matches, payments, and user reports. Publish school announcements to verified students or a department; users receive a popup-ready in-app notification record.

Run `python manage.py seed_categories` once to add the default marketplace categories.

## Production configuration

Set `DEBUG=False`, a new private `SECRET_KEY`, real `ALLOWED_HOSTS`, `FRONTEND_ORIGIN`, PostgreSQL and Redis connection values, SMTP values, and the public callback URL. Students may register with personal email addresses; they must verify their email, then submit a matriculation number and student ID for admin approval before they can access student-only features. For Supabase Postgres, set `DB_ENGINE=postgres` and put the Supabase connection URI in `DATABASE_URL`; the settings parser requires SSL by default. For Render, Supabase recommends the session pooler when the app host uses IPv4. Supabase stores the database; keep Django itself running on Render or another Python host. If charging fees, configure Paystack transaction and webhook secrets; with no Paystack credentials, the platform defaults payments off and newly created listings/ads are free pending moderation. Production settings refuse to start when required database, Redis, or email settings are missing. Allow request bodies up to 27 MB at the reverse proxy so five 5 MB report screenshots can be uploaded together. Leave `SECURE_SSL_REDIRECT` enabled behind HTTPS. If TLS terminates at a trusted reverse proxy, set `TRUST_PROXY_SSL_HEADER=True` only when that proxy overwrites `X-Forwarded-Proto`. Run `python manage.py check --deploy` with production settings, `python manage.py migrate`, and `python manage.py collectstatic` before starting `gunicorn config.wsgi:application`.

Keep `.env` and uploaded media out of source control. Do not configure the web server or object store to expose `MEDIA_ROOT` or `PRIVATE_MEDIA_ROOT` directly: product and lost-and-found images are served through verified-student API routes, student ID images through the staff-only route, and report screenshots through reporter/staff-only routes. Set backups, monitoring, retention/deletion rules, and moderation procedures before inviting students. Review school policy and applicable privacy requirements before storing student IDs and contact details. Configure WhatsApp opt-in and approved templates according to [Meta's WhatsApp Business Messaging Policy](https://whatsappbusiness.com/policy/), and stop notifications when a student opts out.
