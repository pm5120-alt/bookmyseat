# Deployment and clean setup

## Configuration

Use Python 3.12+ for local setup (the Dockerfile uses Python 3.13). Copy `.env.example` to `.env` for local use; settings load it without overriding variables supplied by the process manager. Replace the Django key placeholder. The SQLite URL defaults to the local project database when `DATABASE_URL` is unset. Django debug defaults to false, and settings fail during startup if the key is missing or too short.

Production mode (`DJANGO_DEBUG=false`) requires a PostgreSQL `DATABASE_URL`, at least one non-local `DJANGO_ALLOWED_HOSTS` entry, HTTPS `SITE_URL`, `CELERY_BROKER_URL`, a non-console email backend, and all Razorpay test/live key and webhook secret variables. Configure trusted CSRF origins and service credentials for the real deployment. Production HTTPS redirect, secure session/CSRF cookies, HSTS, nosniff, frame denial, and same-origin referrer policy are enabled. `SECURE_HSTS_PRELOAD` stays false unless the domain owner confirms every affected subdomain is HTTPS-ready; Django's deploy check emits its preload warning when false. Never place actual credentials in this guide or source control.

## Windows local run without Docker

From PowerShell, go to the project folder. If the bundled local `.venv` is missing (for example, after extracting the submission ZIP), create it and install the project requirements:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

For this existing checkout, the `.venv` is already present, so use it directly. Start the local SQLite-backed site with:

```powershell
$env:DJANGO_DEBUG = "true"
$env:DJANGO_SECRET_KEY = (& .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))").Trim()
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py runserver
```

Keep that PowerShell window open while using the website. Open [http://127.0.0.1:8000/](http://127.0.0.1:8000/) in Chrome. Press `Ctrl+C` in PowerShell to stop the server. If you restart it, run the commands again; the new temporary local key signs you out of any previous session. This local path does not require Docker, PostgreSQL, Razorpay, or SMTP. The default SQLite database is local to the project folder.

## Docker Compose local stack

The compose file runs PostgreSQL, Redis, Django web, a Celery worker and Celery beat. The PostgreSQL password has required interpolation and no baked-in fallback. Generate a URI-safe value with `python -c "import secrets; print(secrets.token_hex(32))"`; set it in `.env` along with a generated `DJANGO_SECRET_KEY`. Then run `docker compose up --build`. Create a full owner account with `docker compose exec web python manage.py createsuperuser`, or create the assignment's separate demo dashboard account with `docker compose exec web python manage.py create_demo_admin`; that command generates and displays a demo-only password once when `BOOKMYSEAT_DEMO_ADMIN_PASSWORD` is not set. Visit `http://localhost:8000/`. Docker itself has not been launched in the current verification.

Postgres and Redis use health checks. Web checks its HTTP endpoint; worker checks Celery ping; beat checks its running process. The named database and Redis volumes persist service data, while one private-ticket volume is shared by web and worker. Static files are collected during image build and served through WhiteNoise. The build uses a throwaway generated key for the static-collection step; it is not stored in the image environment. User poster uploads are excluded from the image and need durable object or shared storage for a multi-instance deployment.

`docker compose config` validated `docker-compose.yml` (Docker 29.1.3, Compose 2.40.3, placeholder values; `docker-checks.txt`). Image build, container start and stack health are NOT VERIFIED: the sandbox could not pull the `python:3.13-slim` base image (registry unreachable). Staging/production deployment was not run.

## Manual process layout

Run all processes with the same environment and required file mounts:

- Web: `gunicorn bookmyseat.wsgi:application --bind 0.0.0.0:8000`
- Worker: `celery -A bookmyseat worker --loglevel=INFO`
- Scheduler: `celery -A bookmyseat beat --loglevel=INFO`

Run `python manage.py migrate --noinput` and `python manage.py collectstatic --noinput` during release. Keep exactly one beat scheduler per environment. Worker and beat must use the same PostgreSQL, Redis, email/provider config, and private media storage as web.

## Temporary Render demo deployment

The `render-demo.yaml` Blueprint is deployed in the `priyanshu` workspace at [https://bookmyseat-demo-drmf.onrender.com/](https://bookmyseat-demo-drmf.onrender.com/). Render reports the Free web service as Live. Render build logs show every migration applied on the initial build; after the startup fix, the successful build reported “No migrations to apply” and collected 128 static files. Django runs with `DEBUG=false`, HTTPS cookies and redirects, PostgreSQL, and host checks enabled. `BOOKMYSEAT_DEMO_MODE=true` lets the demo start without a queue service, payment credentials, or SMTP credentials. In demo mode only, if Render's generated 256-bit Base64 secret is shorter than the project's 50-character minimum, Django derives a stable 64-character signing key from that generated value.

The current demo database contains four fictional demo movies. The app now serves bundled SVG poster fallbacks locally when a movie has no uploaded poster, so poster rendering does not depend on third-party image URLs. Razorpay order creation is unavailable; email uses an in-memory backend; background ticket delivery is unavailable because no worker is deployed. Those integrations are not verified by this demo. Render Free web instances sleep after inactivity and have a temporary filesystem; Free Postgres expires after 30 days, has no backups, and is limited to 1 GB. Do not store real customer or payment data in this demo. See [Render Free instance limitations](https://render.com/docs/free).

`render.yaml` describes a separate paid deployment with PostgreSQL, Key Value, Celery worker/Beat, and a persistent disk; it was not deployed or used for this demo. It keeps one web instance because all processes share the same private ticket directory. Scaling beyond one web instance requires shared object storage for private tickets and separate worker services.

The paid deployment design above was previously estimated at about **$41.25/month** on 2026-10-24, before bandwidth and workspace charges; it is not active. The demo uses only Free web and Postgres resources and has no persistent disk, paid worker, Razorpay credentials, or SMTP credentials. See [Render pricing](https://render.com/pricing), [Render persistent disks](https://render.com/docs/disks), and [Render background workers](https://render.com/docs/background-workers).

## Database and uploaded files

Take a provider snapshot and test migrations on a restored staging copy before production. Migration `0007` assigns each legacy theater seat to the first screen without renumbering it. Operational databases and backups are deliberately excluded from `BookMySeat-Submission.zip`; the supplied baseline and working checkpoint remain local outside that archive. Private ticket files use `PRIVATE_MEDIA_ROOT` and must never be exposed by static or public media routes. `MEDIA_ROOT` poster uploads are excluded from the submission and container image; use durable object storage or a shared persistent volume appropriate to the deployment.

## Release checks and known limits

Use `python manage.py check --deploy` with actual production configuration; migrate a staging database; and verify database, broker, worker, beat, email, payment webhook/refunds, static/media delivery, and health endpoints independently. `python manage.py benchmark_bookings --count 100000` writes synthetic rows and must only run against a disposable PostgreSQL database. It does not replace the separate 50-client overlapping-seat contention test.

See `TEST_REPORT.md` for final-source SQLite, PostgreSQL, and concurrency totals. Current-run `manage.py check`, demo-mode settings check, Render build, migrations, homepage, movie page, and anonymous login/admin redirects were verified. Razorpay, webhooks, real SMTP, ticket-worker delivery, populated movie data, and production deployment remain NOT VERIFIED.

## Disposable PostgreSQL acceptance scripts

For Windows PowerShell instructions to run the PostgreSQL `TransactionTestCase` suite and 100,000-booking command, see the PostgreSQL acceptance section in `README.md`. The concurrency suite creates and migrates only Django's derived `test_bookmyseat_concurrency` database. Its base `bookmyseat_concurrency` database must be newly created and empty; the script refuses an existing derived test database. The benchmark uses a separate exact-name `bookmyseat_bench` database and writes synthetic rows. Do not share either database between checks. The final-source PostgreSQL suite and concurrency results in `TEST_REPORT.md` were produced against a disposable PostgreSQL 16.15 database in a separate verification environment. These Windows acceptance scripts were not rerun during the current local-browser audit, and no database URL was configured for that run.

