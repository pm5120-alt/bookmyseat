# Data migration plan

1. Keep `db.sqlite3.baseline` unchanged as the pre-migration rollback copy.
2. The archive database is authoritative for legacy records. Do not connect to or migrate the embedded remote database URL.
3. Use additive migrations where possible: add city/genre/language/cast/poster/screen/show/show-seat/payment/review metadata; alter booking seat history to a rebookable foreign key; retain legacy model rows.
4. A data migration maps every existing theater record to a default screen and scheduled show, then maps its existing seats to show-seat records. Legacy bookings map to booking-seat rows; no legacy rows are deleted.
5. The archive includes duplicate seat labels within one theater. Keep each seat primary key distinct and rely on unique `(show, seat)` reservations. Do not deduplicate existing seat records automatically.
6. After applying migrations to a copy of the baseline, compare row counts and verify every old booking still references its user, movie, theater, and seat. Keep the original baseline copy until those checks pass.
7. For a production PostgreSQL database, take a provider snapshot and restore it into a staging database first. This is not possible from the supplied archive alone.

## Local migration evidence

The SQLite working copy completed `movies.0002` through `movies.0006`. The baseline and migrated counts match: 5 movies, 3 theaters, 6 seats, and 5 bookings. The data migration created 3 show mappings, 6 show-seat rows, and 5 booking-seat rows; every legacy booking now has a reference and show association. The baseline backup hash is recorded in `BASELINE.md`. A blank in-memory test database also applied all migrations during `manage.py test`.

Commands are documented in `DEPLOYMENT.md`; migration results will be recorded after execution.
