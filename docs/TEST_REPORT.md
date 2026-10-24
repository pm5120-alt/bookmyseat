# BookMySeat Verification Report

Date: 2026-10-24. This report separates checks run in this session from historical evidence documented by an earlier audit. The project `.env`, local database, private tickets, and raw probe logs stay local and are not part of the submission ZIP.

## Checks run in this session

| Check | Result |
|---|---|
| `python manage.py check` | **Passed** — no issues |
| `python manage.py makemigrations --check --dry-run` | **Passed** — no model changes needed |
| `python -m pip check` | **Passed** — no broken requirements |
| `python -m compileall -q bookmyseat movies users scripts` | **Passed** |
| Render Blueprint YAML parse | **Passed** — valid YAML with 2 services and 1 database; it was not submitted to Render for account-side validation |
| `python manage.py test` | **Passed before the final deployment-only `MEDIA_ROOT` environment-variable adjustment** — 107 tests, 0 failures, 0 skips (97.5 seconds) |
| Targeted poster upload tests after the `MEDIA_ROOT` adjustment | **Passed** — 2 upload/admin tests; environment override resolved to the configured local path |
| Razorpay configuration read by Django | Key ID present with Test Mode prefix; secret present; webhook secret blank. Values were not displayed. No payment was initiated in this run. |
| GitHub repository / push | **Not performed** — no local Git repository or configured remote exists; no repository creation or push was done. |
| Render deployment | **Not performed** — a manifest is prepared, but paid services were not created and the site is not public. |

The test run emits warnings because `staticfiles/` does not exist yet, plus error-path logs from tests that deliberately mock provider failures. The run ended with `OK` and Django's test system check passed.

## Historical evidence (not rerun in this session)

Earlier project evidence records the following; the original raw logs remain local under `docs/evidence/` and are excluded from the release archive because they contain throwaway audit identifiers and operational test data:

- SQLite suite: 107 passed; PostgreSQL 16.15 suite: 107 run, 1 expected SQLite-only skip; PostgreSQL concurrency module: 8/8 passed.
- Earlier browser probes exercised home/discovery, search and filters, seat hold, admin permissions, booking history, review authorization, and pending-ticket access policy.
- A previous real Razorpay Test Mode order returned HTTP 200 and opened provider Checkout, which displayed “No appropriate payment method found.” No payment was submitted. A prior browser run also occurred before local Test Mode credentials were loaded and received HTTP 503 from order creation; that response does not describe the current configuration.
- A separate local Redis/Celery/SMTP-sink run recorded ticket generation and one delivery. It was not real SMTP provider delivery, and the enqueue was not through a real payment confirmation.
- Earlier PostgreSQL benchmark and HTTP probe results are documented in `PERFORMANCE.md` and the local-only raw evidence. They were not rerun here.

## Not verified

- Successful or failed Razorpay Test Mode transactions, server signature verification against the provider, webhook signature/duplicate handling, amount mismatch, refunds, and ticket generation after a real confirmed payment. Credentials being present does not verify the provider account. The previous Checkout error remains unresolved; the dashboard account's payment-method access was not checked in this session.
- Real SMTP delivery.
- Redis/Celery in the current Windows environment; earlier notes report Redis unavailable there.
- Docker build/start and public deployment.
- GitHub publication and the official internship rubric. The original ElevanceSkills brief was not present; task categories follow the local traceability matrix.
- Full exhaustive browser coverage on the current source.

No live payment, email, production deployment, or external account change was performed in this session.
