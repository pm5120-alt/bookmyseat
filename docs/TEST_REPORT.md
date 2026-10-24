# BookMySeat Verification Report

Date: 2026-10-24. This report separates checks run in this session from historical evidence documented by an earlier audit. The project `.env`, local database, private tickets, and raw probe logs stay local and are not part of the submission ZIP.

## Earlier local acceptance audit (2026-10-24)

Earlier acceptance audit after the homepage placeholder cleanup, before the uploaded demo-catalogue addition used the project virtual environment. `\.venv\Scripts\python.exe manage.py test` passed 108 tests with 0 failures and 0 skips in 102.2 seconds. `manage.py check`, `makemigrations --check --dry-run`, `pip check`, and `compileall` passed on that source. The restarted local browser showed the dynamic sample movie, movie discovery, the no-match search state, selectable available seats, and an anonymous analytics denial (403); no reservation or payment was submitted. The database is SQLite with 1 movie, 1 show, 1 pending and 2 failed bookings, 1 created and 3 failed payments, and no reviews. Redis, Docker, and a configured disposable PostgreSQL database were unavailable. The focused homepage cleanup removed static Event/Premiere/Music cards and added a regression test. See local-only `audit-outputs/acceptance-audit-2026-10-24.txt` for the exact command/output summary.

## Uploaded submission snapshot verification (2026-10-24)

The uploaded ZIP contained one new management command and one appended test. Using the existing local Python environment against an isolated extraction of that ZIP, `manage.py test` passed **109 tests, 0 failures, 0 skips** in 95.1 seconds. The focused command `manage.py test movies.tests.SeedDemoCatalogueTests --verbosity 2` passed. `manage.py check`, `makemigrations --check --dry-run`, `python -m pip check`, and `python -m compileall -q bookmyseat movies users scripts` also passed. The test database was in-memory. Separately, after running migrations against a fresh SQLite database in the isolated extraction, `seed_demo_catalogue --yes` created 4 fictional movies, 1 theatre, 1 screen, 48 seats, and 12 shows; the second run created 0 additional records. No local or hosted customer database was modified. No browser flow was run against this new seeded snapshot.

## Publication and temporary demo verification (2026-10-24)

The reviewed source and deployment documentation were pushed to the public repository [pm5120-alt/bookmyseat](https://github.com/pm5120-alt/bookmyseat). The first publication commit was `535b31d23226eababf02c69358fbaa295e6c9243`; subsequent verified documentation updates were pushed. A high-confidence secret-pattern scan of the publication history found no configured high-confidence credential patterns. `.env`, the local database, and key CSVs are not tracked or included in the submission archive.

The separate `render-demo.yaml` deployment is Live on Free Render web and PostgreSQL resources at [https://bookmyseat-demo-drmf.onrender.com/](https://bookmyseat-demo-drmf.onrender.com/). The latest deploy for the documentation-only deployment note completed successfully; logs show the application started, the service became Live, migrations were already current, and 128 static files were collected. Live HTTP checks returned 200 for `/`, `/movies/`, and `/login/`; anonymous `/profile/` and `/admin/` requests redirected to their login pages. The browser rendered the homepage and movie discovery page. The fresh hosted database has no movie catalogue, so movie discovery showed its correct empty state. These checks establish basic deployment and anonymous route behavior, not the full six-task acceptance criteria.

## Earlier checks recorded from the preceding 2026-10-24 audit

| Check | Result |
|---|---|
| `python manage.py check` | **Passed** — no issues |
| `python manage.py makemigrations --check --dry-run` | **Passed** — no model changes needed |
| `python -m pip check` | **Passed** — no broken requirements |
| `python -m compileall -q bookmyseat movies users scripts` | **Passed** |
| Render Blueprint YAML parse | **Passed** — valid YAML with 2 services and 1 database; it was not submitted to Render for account-side validation |
| Earlier `python manage.py test` | **Historical baseline, passed before the final deployment-only `MEDIA_ROOT` environment-variable adjustment** — 107 tests, 0 failures, 0 skips (97.5 seconds) |
| Targeted poster upload tests after the `MEDIA_ROOT` adjustment | **Passed** — 2 upload/admin tests; environment override resolved to the configured local path |
| Razorpay configuration read by Django | Key ID present with Test Mode prefix; secret present; webhook secret blank. Values were not displayed. No payment was initiated in this run. |
| GitHub repository / push | **Not performed at that earlier audit point** — no repository or remote had yet been created. See the later publication verification above. |
| Render deployment | **Not performed at that earlier audit point** — the manifest had not yet been submitted. See the later temporary demo verification above. |

The test run emits warnings because `staticfiles/` does not exist yet, plus error-path logs from tests that deliberately mock provider failures. The run ended with `OK` and Django's test system check passed.

## Historical evidence (not rerun in this session)

Earlier project evidence records the following; the original raw logs remain local under `docs/evidence/` and are excluded from the release archive because they contain throwaway audit identifiers and operational test data:

- Historical SQLite suite: 107 passed; PostgreSQL 16.15 suite: 107 run, 1 expected SQLite-only skip; PostgreSQL concurrency module: 8/8 passed.
- Earlier browser probes exercised home/discovery, search and filters, seat hold, admin permissions, booking history, review authorization, and pending-ticket access policy.
- A previous real Razorpay Test Mode order returned HTTP 200 and opened provider Checkout, which displayed “No appropriate payment method found.” No payment was submitted. A prior browser run also occurred before local Test Mode credentials were loaded and received HTTP 503 from order creation; that response does not describe the current configuration.
- A separate local Redis/Celery/SMTP-sink run recorded ticket generation and one delivery. It was not real SMTP provider delivery, and the enqueue was not through a real payment confirmation.
- Earlier PostgreSQL benchmark and HTTP probe results are documented in `PERFORMANCE.md` and the local-only raw evidence. They were not rerun here.

## Not verified

- Successful or failed Razorpay Test Mode transactions, server signature verification against the provider, webhook signature/duplicate handling, amount mismatch, refunds, and ticket generation after a real confirmed payment. Credentials being present does not verify the provider account. The previous Checkout error remains unresolved; the dashboard account's payment-method access was not checked in this session.
- Real SMTP delivery.
- Redis/Celery in the current Windows environment; earlier notes report Redis unavailable there.
- Docker build/start and production deployment; only the temporary Free demo was deployed.
- The official internship rubric. The original ElevanceSkills brief was not present; task categories follow the local traceability matrix.
- Full exhaustive browser coverage on the current source.

During the acceptance-audit session described above, no live payment, email, public deployment, or external account change was performed. The later publication step created the requested public source repository and temporary Free Render demo; no production deployment, payment, webhook, or email provider configuration was performed.
