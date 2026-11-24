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

Registration creates an unverified account and accepts personal email addresses. Students must verify their email, submit a matriculation number and student ID, and receive staff approval before accessing marketplace, messaging, or lost-and-found features. A manual-review ID image is stored securely only while pending and is deleted from the configured media storage and database when approved or rejected; automatic approvals do not retain it. The ID route requires staff login. Students also provide a profile photo saved on their account for security; it remains hidden from other students unless they explicitly enable photo visibility in their profile privacy settings. Staff should use Django staff accounts for moderation.

## Marketplace flow

1. A verified student creates a listing. It starts as `pending_payment` and is visible only to its seller.
2. The seller uploads up to five listing images through `/api/products/images/`.
3. The seller calls `POST /api/payments/initialize-listing-fee/` with `{"product": <id>}`. The backend initializes the ₦1,500 fee (150000 kobo) with Paystack.
4. After Paystack confirms payment through the signed webhook at `/api/payments/webhook/`, the listing enters `pending_review`.
5. A school moderator approves listings from Django admin. Approved listings are active for 30 days. A superuser can set `listing_fee_enabled=False` or turn off Paystack in Platform settings; new listings are then free but still await school review. Configure categories and disable categories the school does not allow before opening registration.
6. A buyer requests an active listing. The seller can accept or cancel. Acceptance reserves the listing; completion marks it sold. The buyer can cancel a request before it is accepted. Buyers and sellers arrange the actual hand-off directly; the site does not provide escrow, shipping, or dispute resolution.

Editing an active listing returns it to school review. The seller can retry payment if the listing is still awaiting payment.

Configure `PAYSTACK_SECRET_KEY`, `PAYSTACK_WEBHOOK_SECRET`, and `PAYSTACK_CALLBACK_URL`. Register `https://tarmac-modular-backend.onrender.com/api/payments/webhook/` as the webhook URL in Paystack Dashboard → Developers → Webhooks (if you later add a custom API domain, use that backend domain instead). The webhook goes to the Django backend; `PAYSTACK_CALLBACK_URL` is the separate browser return URL `https://ourtarmac.online/payment-return`. Set `PAYSTACK_WEBHOOK_SECRET` to the webhook signing secret from Paystack and keep `PAYSTACK_SECRET_KEY` as the API secret key. Use test credentials/secrets in Paystack test mode, then configure live keys and the live webhook signing secret before accepting real payments. Do not activate listings based on the browser callback; only the verified webhook activates them. The webhook accepts Paystack's timestamped `Paystack-Signature` format and the legacy `x-paystack-signature` during transition.

## Business advertising, placements, and waitlist

Outside businesses can submit campaigns at `/advertise` without creating a student account. The form shows the placements allowed by each plan, checks capacity for the selected dates, and supports a waitlist when a selected position is full. When ad fees and Paystack are enabled, the advertiser pays through a Django-created Paystack checkout before staff review the campaign. The signed Paystack webhook records the successful transaction; the browser return alone never marks a campaign paid. Staff then approve or reject it, with an optional Paystack refund when rejecting a paid campaign. If fees or Paystack are disabled when a campaign is submitted, that new campaign is marked as free and goes to review.

Growth, Premium and other plan settings are offers for future submissions. Each campaign stores its selected plan name, listed plan price, duration, actual amount due and whether payment was required when it was submitted. Editing a plan never reprices existing campaigns. Campaign edits are for the advert creative; package, price, dates and booked placements are fixed, and changing them requires a new campaign. Admin-created internal adverts are complimentary and do not create payment or revenue records. Campaigns are archived rather than physically deleted, so payment and audit history stays connected.

Every Paystack attempt is stored in the `payments_payment` table with a unique reference, amount in kobo, currency, status, timestamp and campaign relationship; refund status and Paystack response are stored there too. Admin → Advertisements reports successful Paystack receipts less completed refunds, not the sum of package prices or pending amounts. The complete transaction list is available in Admin → Payments and `/api/admin-tools/payments/`. Keep `PAYSTACK_SECRET_KEY`, the Paystack webhook, `FRONTEND_ORIGIN`, and the Resend API settings configured for paid campaigns and branded emails.

Administrators manage slot capacity, limited-slot thresholds, exclusivity, placement switches, and waitlist offers at `/admin/ad-slots`. Plan placement access and the maximum positions per campaign are editable in `/admin/subscriptions` and Admin → Settings → Advertisements. Empty enabled positions show a “Put your brand here” link to the public booking page. Apply the included migrations before using the new pages:

```sh
python manage.py migrate
```

Schedule `python manage.py process_ad_campaigns` every 10 minutes on a Render Cron Job or another worker. It activates scheduled campaigns, ends expired campaigns, releases expired Paystack holds, and emails the next waitlisted advertiser when their requested dates are available. Capacity is also rechecked when an advertiser submits, staff approves, payment starts, and Paystack confirms. A waitlist offer lasts 24 hours and does not reserve a slot until the advertiser submits and staff approves the campaign.

## Lost and found and notifications

Only active, verified students can create or view reports and candidate matches. A scheduled worker compares title, description, colour, category, location, and incident dates. A score creates a *possible match*, not proof of ownership. Both reporters receive an in-app notification and, when enabled, a designed HTML email or opted-in WhatsApp notification. Order request confirmations and order status updates are also sent by email when email notifications are enabled. Students review the match through `/api/lost-found/matches/{id}/review/` with `{"decision":"possible_match"}` or `{"decision":"not_a_match"}`. Report owners can close a report at `/api/lost-found/reports/{id}/close/` or confirm return at `/api/lost-found/reports/{id}/returned/`.

Signup verification, password reset, and branded notifications are sent directly through the Resend API. In production, set `RESEND_API_KEY` to a Resend API key and `DEFAULT_FROM_EMAIL` to an address on a domain verified for sending in Resend; keep the API key only on the backend. During local development without an API key, Django writes emails to the console. Students can request another verification email at `POST /api/accounts/email/resend/`; signup and an expired verification-link page also provide this action. Set `FRONTEND_ORIGIN` and the verification/reset URLs to the deployed website so email buttons open the right pages. Other queued email notifications require `python manage.py retry_notifications` to run regularly. WhatsApp requires approved templates plus `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, and the relevant `WHATSAPP_*_TEMPLATE` setting. Students must provide an E.164 phone number and explicitly enable WhatsApp notifications in `PATCH /api/accounts/me/`; opting out stops future sends.

For `ourtarmac.online`, verify the domain in Resend and publish the DNS records Resend provides (SPF/DKIM, and DMARC when configured). Create working mailboxes or forwarding aliases such as `support@ourtarmac.online` and `hello@ourtarmac.online`; the domain alone does not create inboxes. Set Render's `RESEND_API_KEY`, `DEFAULT_FROM_EMAIL` to `Tarmac <noreply@ourtarmac.online>`, `SUPPORT_EMAIL` to the support inbox, `FRONTEND_ORIGIN` to `https://ourtarmac.online,https://www.ourtarmac.online` if both frontend hosts are in use, `EMAIL_VERIFICATION_URL` to `https://ourtarmac.online/verify-email`, and `PASSWORD_RESET_URL` to `https://ourtarmac.online/reset-password`. Set `PAYSTACK_CALLBACK_URL` to `https://ourtarmac.online/payment-return`. In Admin → Settings, save the working support and contact addresses; email footers and reply-to use the support address. Do not use these example addresses until the inboxes/aliases exist and the sending domain is verified.

For the production custom domain, keep the product name **Tarmac** and configure the frontend host to serve `https://ourtarmac.online`. To avoid cross-site cookie blocking on mobile browsers, add `api.ourtarmac.online` as a custom domain on the Render backend service and point its DNS CNAME at that service's `onrender.com` hostname. Then set the frontend's `NEXT_PUBLIC_API_URL=https://api.ourtarmac.online/api`; the website domain remains the frontend origin, not the API hostname. On Render set `ALLOWED_HOSTS` to include both `tarmac-modular-backend.onrender.com` and `api.ourtarmac.online`, set `FRONTEND_ORIGIN` to the comma-separated frontend origins you serve (for example `https://ourtarmac.online,https://www.ourtarmac.online`; this supplies the allowed CORS and CSRF origins), and set `AUTH_COOKIE_SECURE=True`, `AUTH_COOKIE_SAMESITE=Lax`, and `CSRF_COOKIE_SAMESITE=Lax`. Keep `PAYSTACK_CALLBACK_URL=https://ourtarmac.online/payment-return`. Local `.env` values should remain on localhost for local development.

Email and WhatsApp delivery is queued in the database so provider latency does not slow API requests. Schedule `python manage.py retry_notifications`, `python manage.py match_lost_found`, and `python manage.py dispatch_announcements` to run every minute. Schedule `python manage.py expire_products` daily. Notifications retry with backoff; database row locks protect workers processing the same item. Lost-and-found candidates create a separate private Tarmac Support alert chat for each report owner, plus in-app and configured email/WhatsApp alerts. These match and delivery commands must be scheduled in production; they do not run as part of the web request.

Admins control email and WhatsApp per notification category in Admin → Settings → Notifications. In-app alerts remain in Tarmac. WhatsApp sending requires the school access token and approved Meta templates in environment variables; the numeric WhatsApp Business phone number ID can be changed in platform settings. Student WhatsApp delivery also requires the student to enable it and consent. Admin direct messages can be sent as separate Official, Tarmac Support, or Promotion conversations.

Signed-in users send a throttled presence heartbeat to `POST /api/accounts/presence/` while the site tab is visible and recently active. Chat participant data and public student marketplace profiles include `last_seen_at`; the frontend presents recent activity and older activity as relative labels. Buyer reviews keep their 1–5 rating and derive Positive (4–5), Neutral (3), or Negative (1–2) sentiment. A review may include one optional image up to 5 MB; it is optimized through the configured image storage and becomes visible to others only when the review is approved.

## Useful API routes

- `POST /api/accounts/register/`, `GET|PATCH /api/accounts/me/`, `/api/accounts/students/{id}/`, `/api/accounts/verification/`
- `POST /api/auth/token/`, `POST /api/auth/token/refresh/`
- `/api/products/`, `/api/products/categories/`, `/api/products/images/`, `/api/products/favourites/`
- `/api/payments/`, `POST /api/payments/initialize-listing-fee/`, `POST /api/payments/webhook/`
- `/api/ads/`, `POST /api/payments/initialize-ad-fee/`
- `/api/orders/`
- `/api/lost-found/reports/`, `/api/lost-found/matches/`
- `/api/jobs/categories/`, `/api/jobs/listings/`, `/api/jobs/reports/`
- Public business submission: `/api/jobs/external/submit/`, `/api/jobs/external/confirm-email/`, `/api/jobs/external/manage/`; admin moderation: `/api/jobs/admin/listings/`, `/api/jobs/admin/reports/`
- `/api/messaging/conversations/`, `/api/messaging/messages/`
- `/api/notifications/` (including `/{id}/mark-read/`)
- `/api/accounts/email/confirm/`, `/api/accounts/email/resend/`, `/api/accounts/password-reset/`, `/api/accounts/password-reset/confirm/`
- `/api/admin-tools/dashboard/`, `/api/admin-tools/reports/`, `/api/admin-tools/announcements/` (POST creates; `/{id}/publish/` queues in-app popups), `/api/admin-tools/announcement-publishers/` (superuser-only grant/revoke), `/api/admin-tools/logs/`
- Students can report a marketplace listing or student, a lost-and-found post, or a student from a message thread through `POST /api/admin-tools/reports/`. Reports accept up to five `evidence_images`; each image is limited to 5 MB and stored as an authenticated Cloudinary asset. Only the reporter and staff can view report evidence through `/api/admin-tools/report-evidence/{id}/image/`. Staff can review and resolve reports in Tarmac's admin panel.
- `/api/system/status/` reports maintenance, enabled sections, and current fee policy.

## Admin controls

Create a Django superuser for platform controls. Under Platform settings, a superuser can disable the whole site, switch marketplace, lost-and-found, messaging, or ads on and off, and set Paystack plus listing/ad fees. Disabling a fee or Paystack makes new listings/campaigns free while retaining school moderation. Admin staff can review student accounts, listings, ads, lost-and-found reports and matches, payments, and user reports. Superusers can grant a non-staff, active account with a verified email announcement-publisher access at `/admin/administrators`. The representative uses `/announcement-publisher` to post for all verified students and can manage only their own announcements; they do not get admin access, department targeting or pinning privileges. Revoke access from the same superuser page. Publish school announcements to verified students or a department; users receive a popup-ready in-app notification record.

Marketplace categories are managed by administrators under Admin → Categories. Only active categories allowed for sale appear in the student Marketplace browse filters and listing form. Existing category records remain in the database until an administrator edits, deactivates, or deletes them.

## Jobs & Gigs

Verified students can browse and post campus jobs, internships, freelance work, and student services at `/jobs`. Student listings are held for admin approval by default; the superuser can change this and switch Jobs & Gigs or public business submissions off under Admin → Settings. Student enquiries use the existing Tarmac inbox. Student email and phone numbers are not included in public listing responses. Students can edit, pause, resume, mark filled, or remove their own listings; conversations remain available after a listing closes.

Businesses can use the public home page's **Post a job opportunity** link or visit `/jobs/submit` without a student account. Their email must be confirmed before the submission reaches the review queue, and every business listing requires admin approval. Business contact details remain admin-only; only the business name, opportunity details, and approved application instructions/link are public. Approval emails contain a private management link whose random token is stored only as a hash, can be rotated by an admin, and expires by the listing deadline or within 90 days. Management-link actions require an explicit request; a deletion request stays in the admin queue and preserves the record. Sponsorship purchases and job engagement reviews are not part of this first release.

Active listings disappear from public Jobs & Gigs results after their application deadline. Schedule `python manage.py expire_job_listings` daily to update their stored status; public reads do not run bulk expiry writes.

## Production configuration

Set `DEBUG=False`, a new private `SECRET_KEY`, real `ALLOWED_HOSTS`, `FRONTEND_ORIGIN`, PostgreSQL and Redis connection values, `RESEND_API_KEY`, `DEFAULT_FROM_EMAIL`, and the public callback URL. Browser login uses Secure, HttpOnly JWT cookies; the frontend must use credentialed requests and the configured `FRONTEND_ORIGIN` must exactly match the deployed frontend origin. Production defaults to `AUTH_COOKIE_SAMESITE=None` for separate frontend/backend origins. Some browsers restrict cross-site cookies, so for broad browser compatibility use a custom frontend and API domain under the same site (for example, `tarmac.example.com` and `api.tarmac.example.com`) and set `AUTH_COOKIE_SAMESITE=Lax`. `CSRF_TRUSTED_ORIGINS` may be set to a comma-separated list when additional frontend origins are needed. For local development, the default cookie policy is `Lax` and non-Secure over localhost. Set `RESEND_API_KEY` to a Resend API key and `DEFAULT_FROM_EMAIL` to an address on a domain verified for sending in Resend. Students may register with personal email addresses; they must verify their email, then submit a matriculation number and student ID for admin approval before they can access student-only features. For Supabase Postgres, set `DB_ENGINE=postgres` and put the Supabase connection URI in `DATABASE_URL`; the settings parser requires SSL by default. For Render, choose a Supabase pooler compatible with the app's connection mode and network. Supabase stores the database; keep Django itself running on Render or another Python host. If charging fees, configure Paystack transaction and webhook secrets; with no Paystack credentials, the platform defaults payments off and newly created listings/ads are free pending moderation. Production settings refuse to start when required database, Redis, Cloudinary, or email settings are missing. Allow request bodies up to 27 MB at the reverse proxy so five 5 MB report screenshots can be uploaded together. Leave `SECURE_SSL_REDIRECT` enabled behind HTTPS. If TLS terminates at a trusted reverse proxy, set `TRUST_PROXY_SSL_HEADER=True` only when that proxy overwrites `X-Forwarded-Proto`. Run `python manage.py check --deploy` with production settings, `python manage.py migrate`, and `python manage.py collectstatic` before starting `gunicorn config.wsgi:application`.

### Operations and capacity

Set `SENTRY_DSN` in the backend host to enable backend error reporting and sampled traces; set the sample rate to a low value first and review it against traffic and budget. `/health/live/` checks that the process responds; `/health/ready/` checks both PostgreSQL and the shared Redis cache and returns 503 when either is unavailable. Use these endpoints for host health checks, not as a substitute for monitoring.

The `retry_notifications` management command now processes email, WhatsApp, and phone push deliveries from the database queue. Schedule `python manage.py retry_notifications` at least once per minute while traffic is low. For growing traffic, create a separate Render Background Worker with start command `python manage.py run_notification_worker` and scale worker instances independently from the web service. Schedule the expiry, campaign, lost-and-found matching, and announcement commands described above as well. Monitor pending notification age and failed attempts. Database-backed delivery is durable but adds database writes; a dedicated broker and task queue should be introduced before sustained high-volume traffic.

The codebase has not been load-tested and has no demonstrated concurrent-user capacity. Do not infer capacity from account totals or replica counts. Load-test staging with realistic request rates, image sizes, read/write mix, and burst patterns before selecting production instance counts or promising support for large concurrency.

Keep `.env` and uploaded media out of source control. Do not configure the web server or object store to expose `MEDIA_ROOT` or `PRIVATE_MEDIA_ROOT` directly: product and lost-and-found images are served through verified-student API routes, student ID images through the staff-only route, and report screenshots through reporter/staff-only routes. Set backups, monitoring, retention/deletion rules, and moderation procedures before inviting students. Review school policy and applicable privacy requirements before storing student IDs and contact details. Configure WhatsApp opt-in and approved templates according to [Meta's WhatsApp Business Messaging Policy](https://whatsappbusiness.com/policy/), and stop notifications when a student opts out.

### Cloudinary image storage

Create a Cloudinary account and copy its `CLOUDINARY_URL` from the Cloudinary dashboard. Set it in your local `.env` and as a secret environment variable on Render. Production startup requires this value. Image uploads are orientation-corrected, resized to at most 1920 × 1920 pixels, and re-encoded as WebP at quality 80 before they leave Django. Product, Lost & Found and advertisement images use Cloudinary delivery. Profile photos are returned only through the authenticated profile image endpoint, and only when the student enables photo visibility. Student ID images, profile change images, and report evidence use Cloudinary's authenticated delivery and are fetched only through the existing access-controlled API routes. PDFs and other private file attachments remain on the existing private file storage.

New uploads go to Cloudinary; existing media is not copied automatically. If the database already contains media files, migrate those files to Cloudinary while the original files are still available before removing the old media storage.
