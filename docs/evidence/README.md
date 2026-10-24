# Evidence provenance

The raw audit logs and benchmark data are retained in the working folder for local review. They are intentionally excluded from the public repository and submission ZIP because they contain generated user-like identifiers and operational test data. The verified results and their limitations are summarized in `../TEST_REPORT.md`, `../TRACEABILITY.md`, and `../PERFORMANCE.md`.

The 2026-10-24 test report records 107 SQLite tests passing, 107 PostgreSQL tests with one expected SQLite-only skip, eight PostgreSQL concurrency tests passing, and a separate Redis/Celery/local-SMTP-sink ticket run. These are recorded evidence from the prior audit; they were not all rerun during the current deployment-preparation pass. Real provider payments, real SMTP delivery and public deployment remain unverified.
