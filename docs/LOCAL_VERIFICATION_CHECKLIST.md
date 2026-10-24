# Local verification results and remaining checks

## Historical baseline executed on earlier source — 2026-10-24 (one sandbox; raw outputs in `docs/evidence/final-audit-20261024-rerun/`)

| Check | Result |
|---|---|
| Historical `python manage.py test --verbosity 1` on SQLite | **107 passed, 0 failed, 0 skipped** (`sqlite-full-suite-final.txt`) |
| Historical same suite on PostgreSQL 16.15 (disposable cluster) | **107 run, 0 failed, 1 skipped** — the skip is the SQLite-only benchmark-refusal test (`postgres-full-suite-final.txt`) |
| `python manage.py test movies.postgres_concurrency_tests` (explicit run; not found by default discovery) | **8 passed, 0 failed, 0 skipped**, through `scripts/preflight_postgres_harness.py` (`postgres-concurrency-final.txt`) |
| `python manage.py check`, `makemigrations --check --dry-run`, `pip check`, `compileall` | Passed (`static-checks.txt`) |
| Inline template JavaScript, `node --check` (Node v22.22.2; Django tags replaced by `0` before parsing) | 5 blocks parsed |
| `python manage.py check --deploy` with production-mode placeholder settings | 1 warning: `security.W021` (HSTS preload opt-in) |
| `docker compose config` with placeholder values | Passed (exit 0); image pull blocked, see below (`docker-checks.txt`) |
| Fresh migration chain | Applied on new PostgreSQL databases (`bookmyseat_bench`, `bms_e2e`) and, implicitly, by both full-suite runs |
| Local Redis broker + separate Celery worker + separate Beat + local SMTP sink | Ticket task executed, one email with a valid PDF attachment, second run skipped (no duplicate), Beat dispatched `release_expired_holds` (`celery-*-redis-e2e.log`) |
| Fresh 100,000-booking PostgreSQL seed and before/after-index comparison | Completed; see `PERFORMANCE.md` (`benchmark-seed-100000.json`, `index-before-after-comparison.json`) |
| Live HTTP probes against `runserver` on PostgreSQL | 30/30 access-control/security checks and 13/13 Task 1/Task 5 checks (`live-probe-access-control.txt`, `live-probe-task1-task5.txt`; the probe scripts, which contain throwaway local test passwords, are delivered separately and are not in the ZIP) |

The default SQLite suite does not discover the separately named PostgreSQL module; it must be run explicitly. An earlier pass's folder `docs/evidence/final-audit-20261024/` is superseded history (see `docs/evidence/README.md`).

## NOT VERIFIED

- **Razorpay provider integration:** no provider requests were made and no live-money operation was performed. Order lookup semantics, Checkout, real webhooks and refunds are covered only by test doubles. Run `docs/RAZORPAY_TEST_MODE_RUNBOOK.md` with test-mode keys.
- **Real SMTP provider delivery:** only a local SMTP sink was used.
- **Redis/Celery beyond the local check:** broker-outage recovery and retry-with-backoff against live Redis, and any production broker.
- **Docker and deployment:** `docker compose config` validation passed (`docker-checks.txt`). Image build, container start, stack health, staging and production deployment were not run (the base image could not be pulled in the sandbox).
- **Real-browser behavior** of the AJAX discovery and seat-selection pages: only server-side tests, live HTTP probes and JavaScript syntax checks were run.
- **Production-scale performance:** the benchmark is single-theater synthetic data on small sandbox hardware.
- **Credential rotation:** the secret key and database credentials that were hardcoded in the original settings must be rotated by the user; this cannot be verified from the archive.

For your own PostgreSQL verification, follow the safe runner instructions in `README.md`. Never use a production database or any populated benchmark database.
