# BookMySeat

Django movie-ticket application with screen-specific seat inventory, payment/refund handling, reviews, discovery, staff analytics, and asynchronous private ticket delivery.

## Clean local setup

Requirements: Python 3.12 or newer. The bundled Docker image uses Python 3.13. The checked environment for this submission used Python 3.12.14.

1. Create an environment and install app dependencies:

   ```powershell
   python -m venv .venv
   .venv\Scripts\python -m pip install -r requirements.txt
   ```

   On Linux or macOS use `.venv/bin/python -m pip install -r requirements.txt`.

2. Copy `.env.example` to `.env`. Generate a key with `python -c "import secrets; print(secrets.token_urlsafe(64))"` and replace the `DJANGO_SECRET_KEY` placeholder. Settings load this file for local commands. Do not use the example placeholder as a key.
3. Apply the SQLite migrations: `.venv\Scripts\python manage.py migrate` (or `.venv/bin/python manage.py migrate`). A fresh checkout has no database or user records and starts with an empty catalog.
4. Create an administrator securely. For a full local owner account, use `.venv\Scripts\python manage.py createsuperuser`. For the assignment's separate demo dashboard account, use the `create_demo_admin` command below; do not use a production account as the demo account.
5. Start locally: `.venv\Scripts\python manage.py runserver`; visit `/`, `/movies/`, and `/admin/`.

For an optional demo catalogue on a fresh or disposable database, run `.venv\Scripts\python manage.py seed_demo_catalogue --yes` after migrations. It adds four fictional movies, one demo theatre and screen, 48 seats, and 12 upcoming shows. It never runs automatically or edits/deletes existing records; omit it if you want to keep the catalogue empty.

The settings fail closed if a strong explicit `DJANGO_SECRET_KEY` is absent. `DJANGO_DEBUG` defaults to false; the example turns it on for local development. Production mode additionally requires PostgreSQL, a non-local allowed host, an HTTPS `SITE_URL`, a Celery broker, a non-console email backend, and Razorpay credentials. `DATABASE_URL` is omitted from the local example and defaults to the project SQLite path; use PostgreSQL in production.

### Assignment demo administrator

After applying migrations, create the separate, non-superuser demo account with:

```powershell
# Option A: securely choose a password for this demo environment (prompted; not stored in source).
$demoCredential = Get-Credential -UserName demo_admin -Message "Set the BookMySeat demo-only password"
$env:BOOKMYSEAT_DEMO_ADMIN_PASSWORD = $demoCredential.GetNetworkCredential().Password
.venv\Scripts\python manage.py create_demo_admin
Remove-Item Env:BOOKMYSEAT_DEMO_ADMIN_PASSWORD

# Option B: let Django securely generate a password and display it once.
.venv\Scripts\python manage.py create_demo_admin
```

Demo username: `demo_admin`. Its password is the environment value you chose for this demo database or the one-time generated value printed by the command. The command never has a default password, refuses to modify any existing account with that username, creates a clearly labelled staff account that is not a superuser, and grants only the `movies.view_booking_analytics` permission through the `BookMySeat Demo Administrators` group. Keep any chosen password in the demo environment only; do not copy it into this repository, ZIP, or production configuration. The generated password cannot be shown again after the command exits.

## Environment settings

`.env.example` lists the supported values. Key settings are `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`, `DATABASE_URL`, `SITE_URL`, `TIME_ZONE`, `SEAT_HOLD_SECONDS` (120 by default), `PRIVATE_MEDIA_ROOT`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`, `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET`, and email settings (`EMAIL_BACKEND`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS`, `DEFAULT_FROM_EMAIL`). Docker Compose also requires `POSTGRES_PASSWORD`; generate a URI-safe value with `python -c "import secrets; print(secrets.token_hex(32))"` and put it in `.env`. The Compose file passes PostgreSQL/Redis addresses to web and worker services. Never put real secrets in source control.

## Docker Compose

1. Copy `.env.example` to `.env` and set a new Django key and nonempty `POSTGRES_PASSWORD`.
2. Run `docker compose up --build`.
3. For a full local owner account, run `docker compose exec web python manage.py createsuperuser`. For the assignment dashboard account, run `docker compose exec web python manage.py create_demo_admin`; without a password variable it generates and displays a demo-only password once.
4. Visit `http://localhost:8000/`. Set real test credentials and use controlled destinations before trying Razorpay or SMTP.
5. `docker compose down` retains named volumes. `docker compose down -v` removes them and deletes local database/ticket data.

Compose config includes Postgres and Redis readiness checks plus web, worker and beat health checks. Its YAML syntax was parsed locally, but Docker Engine/Compose are not installed here, so the services were not built or launched. This local Compose example sets development mode; do not deploy it as production without setting production security and service values.

## Six task areas

- **1 — Movie management, trailers, reviews and ratings:** manage movies and related catalog data in Django Admin; validate image bytes; use validated YouTube IDs; restrict reviews to eligible paid viewers and moderate hidden content.
- **2 — Smart seat reservation:** assign seats to screens, use timed holds and atomic reservations, add new seat requests to the user's active hold, and release inventory on expiry or verified refund. Use the reservation-modification flow for intentional seat removals.
- **3 — Payments and booking management:** create provider orders server-side, verify callbacks/webhooks, record retryable events, and keep paid cancellation pending until refund confirmation.
- **4 — Admin analytics:** use the explicit analytics permission, custom date ranges, defined metrics, and streaming formula-safe CSV exports.
- **5 — Discovery and recommendations:** combine search, catalog/show filters, sorting, pagination, recent-view and booking-history recommendations with a trending fallback.
- **6 — Tickets and email:** generate a signed QR PDF asynchronously; store downloads privately and check booking ownership; retry delivery through Celery.

## Verification

**Current submission status (25 October 2026): deployment available; payment checkout incomplete; external acceptance remains partial.** See the [latest submission verification report](docs/SUBMISSION_VERIFICATION_2026-10-25.md) for current evidence and required follow-up. The previous [24 October report](docs/SUBMISSION_VERIFICATION_2026-10-24.md) is retained as historical context and is superseded for current payment status.

**Live demo:** https://bookmyseat-demo-drmf.onrender.com/  
**Public repository:** https://github.com/pm5120-alt/bookmyseat

Render reported the latest deployment as live. The previous successful poster-fix build collected 133 static files, including four title-specific SVG posters and one generic fallback, and Render logs previously recorded HTTP 200 for the homepage. The latest recorded Razorpay Checkout attempt displayed **“No appropriate payment method found”** after the owner entered payment environment variables in Render. A successful hosted transaction has **not** been demonstrated, so do not claim live payment/capture/webhook/refund as verified. The payment issue may involve test-account payment-method availability, credential matching or provider/integration configuration and still needs diagnosis.

The embedded `docs/TEST_REPORT.md` records 109 SQLite tests passed for the packaged snapshot; a separate acceptance note records 110/110 for a subsequent audit. The count discrepancy remains unreconciled, and this report does not assert a single final numeric test total. Historical local evidence records Django system checks, migration-drift checks, dependency checks, Python compilation and isolated PostgreSQL concurrency tests; these do not demonstrate concurrency acceptance on the live Render database.

**Other incomplete external acceptance checks:** successful hosted Razorpay test transaction/capture/webhook/refund, production database concurrency, real SMTP delivery from the current hosted demo, and a full Docker build/launch. A Test Mode order, mocked payment, or local SMTP sink is not proof of a successfully completed hosted transaction or real email delivery. See the latest report before submission and be transparent about any unresolved provider limitation.

Install test tools with `python -m pip install -r requirements-test.txt`; run the suite with `python manage.py test`. Other useful checks are `python manage.py check`, `python manage.py makemigrations --check --dry-run`, `python -m pip check`, and `python -m compileall -q bookmyseat movies users scripts`.

## PostgreSQL acceptance gates (Windows PowerShell)

These checks need a local PostgreSQL server, a login allowed to create databases, and at least 55 available PostgreSQL connections for the 50-client contention run. They are not part of the SQLite suite. Create two separate empty databases, and never point these scripts at the supplied data or an application/production database:

```powershell
createdb bookmyseat_concurrency
createdb bookmyseat_bench
$env:DATABASE_URL = $env:BOOKMYSEAT_CONCURRENCY_URL
$env:BOOKMYSEAT_POSTGRES_DISPOSABLE = "YES"
.\scripts\run_postgres_concurrency.ps1

$env:DATABASE_URL = $env:BOOKMYSEAT_BENCH_URL
.\scripts\run_postgres_benchmark.ps1
```

Before these commands, set `BOOKMYSEAT_CONCURRENCY_URL` and `BOOKMYSEAT_BENCH_URL` in your local PowerShell process to credential-bearing URIs for the two new local databases; keep them out of source control and reports. The concurrency script requires a newly created empty base database. Django creates `test_bookmyseat_concurrency`, applies the complete migration chain there, runs two `TransactionTestCase` races with 50 independent authenticated clients/connections, and removes that derived test database. The benchmark script migrates and seeds only `bookmyseat_bench`; it saves the command's real JSON output under `docs/evidence/`. Both scripts refuse a database with the wrong exact name or missing explicit disposable-database confirmation. If a benchmark fails after writes start, discard and recreate only `bookmyseat_bench` before retrying. Do not use `--keepdb` for the concurrency script.

After reviewing and confirming the exact database names, remove only the two dedicated databases with `dropdb bookmyseat_concurrency` and `dropdb bookmyseat_bench`. The test runner normally removes its derived test database; if a failed run leaves `test_bookmyseat_concurrency`, inspect that exact database before removing it.

### Equivalent bash commands (as used for the recorded evidence)

Use throwaway databases only; set the URLs in your shell, never in files. Do not add `--noinput` or `--keepdb` to the concurrency run if a stale `test_bookmyseat_concurrency` exists; inspect it first.

```bash
export DJANGO_DEBUG=true DJANGO_SECRET_KEY="<random 50+ characters>" BOOKMYSEAT_POSTGRES_DISPOSABLE=YES
export DATABASE_URL="postgres://USER:PASSWORD@127.0.0.1:5432/bookmyseat_concurrency"
python scripts/preflight_postgres_harness.py --mode concurrency
python manage.py test movies.postgres_concurrency_tests --verbosity 2

export DATABASE_URL="postgres://USER:PASSWORD@127.0.0.1:5432/bookmyseat_bench"
python scripts/preflight_postgres_harness.py --mode benchmark
python manage.py migrate --noinput
python manage.py benchmark_bookings --count 100000 --confirm-disposable > seed.json
python manage.py benchmark_index_comparison --confirm-disposable --window-days 30 365 > comparison.json
```

Documentation/evidence consistency can be re-checked with `python scripts/verify_docs_consistency.py`.

## Data, files and release status

The submission archive intentionally excludes operational SQLite databases, backups, poster uploads, private ticket files, sessions, and generated staticfiles. Migrations create a clean database; create a superuser and catalog records after setup. The supplied database and both preserved checkpoints remain outside the submission ZIP in the working folder.

Read `docs/TRACEABILITY.md` for the requirement-by-requirement evidence and `docs/IMPLEMENTATION_STATUS.md` plus `docs/TEST_REPORT.md` for limitations. Provider test doubles are not proof of live Razorpay behavior, and a local SMTP sink is not proof of real email delivery. PostgreSQL concurrency and the local Redis/Celery checks were executed separately (see Verification above). No production deployment result is claimed.


