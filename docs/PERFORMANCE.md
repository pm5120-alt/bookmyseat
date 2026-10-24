# Analytics and performance assessment

## Analytics definitions and time boundaries

Custom date filters use Django's configured local timezone. The start date is inclusive at local midnight; the selected end date is inclusive by converting it to an exclusive boundary at midnight of the following local date. Empty or malformed ranges are rejected; the maximum span is 367 inclusive calendar days.

- **Daily, weekly, monthly, yearly gross revenue:** sum captured `Payment.amount`, grouped by `Payment.captured_at` in local time. Only captured payments count.
- **Refund total/count:** include only refunds whose provider result is confirmed as processed, grouped by `Payment.refunded_at`; pending, failed, and manual-reconciliation requests are excluded.
- **Net revenue:** captured gross amount minus confirmed refund amounts in the selected period. A refund may fall in a different period from its original capture.
- **Booking trends, cancellations, top movies/theaters, peak booking hours:** use the booking cohort (`Booking.booked_at`) in the selected local-time range. Cancellation count/rate counts bookings in `cancelled` or `refunded` status; cancellation-pending requests are not yet cancellations. The denominator is all bookings created in the selected cohort. Top movies/theaters rank captured payment value attached to paid bookings in that booking cohort; these rankings are gross booking-cohort values, while period gross/net revenue uses payment timestamps.
- **Theater occupancy:** booked `ShowSeat` rows divided by the sum of physical `Screen.seats` capacity for active shows whose `Show.starts_at` is in the selected period. Each show contributes its screen capacity once; holds are not booked seats. This is scheduled inventory utilization, not ticketed attendance.
- **Top theaters by revenue and bookings:** rank by captured payment amount and distinct paid bookings in the selected booking cohort. This ranking does not subtract refunds; refund totals are reported separately by refund date.
- **Peak booking hour:** histogram of `Booking.booked_at` hour in the Django local timezone.
- **User growth:** user `date_joined` cohort in local time.

All dashboard metrics and streaming CSV exports use the same validated date range. CSV fields are escaped against spreadsheet formula injection.

## Current query and memory safeguards

- Discovery, home, and booking-history pages are bounded/paginated; important discovery/detail paths have query-count tests.
- CSV export streams rows with queryset iteration rather than building the complete response in memory.
- Analytics aggregates in the database and materializes grouped results, not all source bookings.
- Search, show/movie, seat ownership, payment timestamps, and analytics date/status fields have supporting indexes; confirm query plans on the target PostgreSQL version and realistic data before tuning further.

## 100,000 booking assessment

**Historical user-supplied benchmark (Windows host, PostgreSQL 18.6, Python 3.13.14); not rerun. A fresh run on a different environment is documented in the next section.** The benchmark was reported as executed against the dedicated `bookmyseat_bench` database on PostgreSQL 18.6 with Python 3.13.14. The raw output is [`docs/evidence/benchmark-100000-20261023-222051.json`](evidence/benchmark-100000-20261023-222051.json). The populated database was not accessed.

The evidence file is valid UTF-8 without a BOM. The final audit loaded it with ordinary UTF-8 `Path.read_text` and `json.loads`, then asserted the recorded seeded counts, integrity mismatch counters, seed time, database size, and query timing values without modifying the JSON. The PowerShell runner explicitly writes UTF-8 output while echoing the captured benchmark output and propagating its exit code.

## Fresh 100,000-booking PostgreSQL benchmark with before/after-index comparison (2026-10-24)

Target: a newly created empty database `bookmyseat_bench` on PostgreSQL 16.15 in a small sandbox (not production hardware; the older user-supplied Windows/PostgreSQL 18 JSON is retained and untouched as historical evidence). Seed: `benchmark_bookings --count 100000` (100,000 bookings, payments, show seats, booking seats; 123 s; all integrity counters 0). Comparison: `benchmark_index_comparison --window-days 30 365`, which captures the 17 SQL statements that `_analytics_report_data` really issues, runs each 10 times and `EXPLAIN (ANALYZE, BUFFERS)` with the analytics indexes dropped, then again after restoring them from their saved definitions, on identical data (`VACUUM ANALYZE` before each phase). Indexes toggled: `booking_date_status_idx`, `payment_capture_range_idx`, `payment_refund_range_idx`, and the single-column `captured_at`/`refunded_at` indexes on `movies_payment`. Raw output: `evidence/final-audit-20261024-rerun/index-before-after-comparison.json`.

| Window | Whole dashboard p50 before -> after |
|---|---|
| Default 30 days | 265 ms -> 102 ms |
| 365 days | 751 ms -> 731 ms (no meaningful change) |

Per statement over 30 days, the booking and payment range statements fell from roughly 5-14 ms to 0.3-3 ms (plans changed from sequential scans to index-only, index or bitmap scans over the new indexes). The two heaviest statements (top movies / top theaters, about 36 ms to 26-31 ms) improved little because they join and de-duplicate payments. Over a 365-day window the range covers most of the table, so PostgreSQL correctly keeps sequential scans and the indexes do not help; this is expected behavior, not a measurement error. Timings come from one sandbox run with 10 repeats per statement and carry noise of a few milliseconds.

**Defect found by this run:** the occupancy statement originally took 13.9-14.2 s for a 365-day window because it joined screen seats to show seats per show. It was rewritten with scalar subqueries (identical per-theater results, whole dashboard about 0.6 s). The 17-statement comparison above already uses the rewritten query.

Bounds: the seed places every show in the past within one year and uses one theater/screen, so theater-level ranking and occupancy are not realistically distributed; the numbers show scaling with row count, not production capacity.

### Historical user-supplied results (Windows / PostgreSQL 18.6; not produced in the current sandbox)

| Measurement | Reported result |
|---|---:|
| Bookings / payments / show-seat rows / booking-seat rows | 100,000 each |
| Confirmed / pending / cancelled / refunded bookings | 60,000 / 20,000 / 10,000 / 10,000 |
| Seed time | 99.65 seconds |
| Database size after seed | 174,266,047 bytes |
| PostgreSQL sizing reported | 8 CPUs; max_connections 100; shared_buffers 128 MB; work_mem 4 MB; effective_cache_size 4 GB |
| Integrity mismatches | 0 across booking-seat/show matching, payment amounts/counts/states, confirmed seats, and pending holds |
| Streaming CSV sample | 4,625 rows in 96.1 ms; peak Python allocation 358,477 bytes; chunk size 1,000 |

The JSON records query timing and plan measurements: daily bookings p50/p95 4.55/7.12 ms; daily revenue 3.80/4.64 ms; top movies 3.39/4.66 ms. These are measurements from the reported local benchmark environment, not deployment capacity guarantees. Do not rerun against the already populated benchmark database. For a future run, use a newly created disposable database; the command refuses populated application tables. If a run fails after insertion begins, discard and recreate only that dedicated benchmark database before retrying.

The benchmark does not measure concurrent reservation load. The PostgreSQL concurrency suite (8/8 passed on PostgreSQL 16.15) is documented separately in `TEST_REPORT.md`, and local Redis/Celery worker and Beat checks passed. Razorpay provider integration, real SMTP delivery, Docker launch, staging, and production deployment remain unverified. Do not infer production readiness or a deployment load threshold from this benchmark.
