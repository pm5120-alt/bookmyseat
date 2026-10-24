"""Task 4.6: before/after-index comparison of the REAL dashboard queries on PostgreSQL.

Run only on a disposable, already-seeded benchmark database (see
`benchmark_bookings`). The command captures the SQL that
`movies.views._analytics_report_data` actually issues, times each statement and
records EXPLAIN (ANALYZE, BUFFERS) with the analytics indexes dropped
("before") and again after restoring them from their saved definitions
("after"), on identical data. Indexes are always restored, even on error.
"""
import json
import math
import re
import statistics
import time
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from movies.models import Booking, Payment
from movies.views import _analytics_report_data

ANALYTICS_INDEXES = ("booking_date_status_idx", "payment_capture_range_idx", "payment_refund_range_idx")
TABLES = ("movies_booking", "movies_payment", "movies_show", "movies_showseat", "auth_user")
REPEATS = 10


def _ms(values):
    ordered = sorted(values)
    return {"p50_ms": round(statistics.median(values), 2),
            "p95_ms": round(ordered[min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1)], 2)}


class Command(BaseCommand):
    help = "Compare real dashboard query plans/timings before and after analytics indexes (disposable PostgreSQL only)."

    def add_arguments(self, parser):
        parser.add_argument("--confirm-disposable", action="store_true")
        parser.add_argument("--min-bookings", type=int, default=100000)
        parser.add_argument("--window-days", type=int, nargs="+", default=[30, 365])

    def handle(self, *args, **options):
        from .benchmark_bookings import Command as Seeder
        Seeder._validate_target(connection.vendor, connection.settings_dict.get("NAME", ""), options["confirm_disposable"])
        total = Booking.objects.count()
        if total < options["min_bookings"]:
            raise CommandError(f"Target has {total} bookings; seed at least {options['min_bookings']} with benchmark_bookings first.")
        indexes = self._index_definitions()
        report = {"bookings": total, "payments": Payment.objects.count(), "database_version": connection.pg_version,
                  "indexes_toggled": {name: sql for name, sql in indexes.items()}, "windows": {}}
        try:
            for days in options["window_days"]:
                end = timezone.localdate()
                start = end - timedelta(days=days - 1)
                phases = {}
                self._drop(indexes)
                phases["before_indexes"] = self._measure(start, end)
                self._create(indexes)
                phases["after_indexes"] = self._measure(start, end)
                report["windows"][f"{days}_days"] = {"start": str(start), "end": str(end), **phases}
        finally:
            self._create(indexes)  # idempotent: IF NOT EXISTS, so evidence run can never leave indexes missing
        self.stdout.write(json.dumps(report, indent=2, default=str))

    def _index_definitions(self):
        names = list(ANALYTICS_INDEXES)
        with connection.cursor() as cursor:
            # Single-column db_index on Payment.captured_at / refunded_at (migration 0011) also target these reports.
            cursor.execute("SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = current_schema() AND tablename = 'movies_payment'")
            for name, definition in cursor.fetchall():
                if re.search(r"\((captured_at|refunded_at)\)$", definition) and name not in names:
                    names.append(name)
            cursor.execute("SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = current_schema() AND indexname = ANY(%s)", [names])
            found = dict(cursor.fetchall())
        missing = [n for n in ANALYTICS_INDEXES if n not in found]
        if missing:
            raise CommandError("Expected analytics indexes are missing: " + ", ".join(missing))
        return {name: found[name].replace("CREATE INDEX", "CREATE INDEX IF NOT EXISTS", 1) for name in found}

    def _drop(self, indexes):
        with connection.cursor() as cursor:
            for name in indexes:
                cursor.execute(f'DROP INDEX IF EXISTS "{name}"')

    def _create(self, indexes):
        with connection.cursor() as cursor:
            for sql in indexes.values():
                cursor.execute(sql)

    def _settle(self):
        with connection.cursor() as cursor:
            for table in TABLES:
                cursor.execute(f"VACUUM ANALYZE {table}")

    def _measure(self, start, end):
        self._settle()
        _analytics_report_data(start, end)  # warm-up, discarded
        with CaptureQueriesContext(connection) as captured:
            _analytics_report_data(start, end)
        statements = [q["sql"] for q in captured.captured_queries]
        whole = []
        for _ in range(REPEATS):
            began = time.perf_counter()
            _analytics_report_data(start, end)
            whole.append((time.perf_counter() - began) * 1000)
        per_query = []
        with connection.cursor() as cursor:
            for number, sql in enumerate(statements, 1):
                times = []
                for _ in range(REPEATS):
                    began = time.perf_counter()
                    cursor.execute(sql)
                    cursor.fetchall()
                    times.append((time.perf_counter() - began) * 1000)
                cursor.execute("EXPLAIN (ANALYZE, BUFFERS) " + sql)
                plan = "\n".join(row[0] for row in cursor.fetchall())
                execution = re.search(r"Execution Time: ([0-9.]+) ms", plan)
                per_query.append({"n": number, "sql": sql, **_ms(times),
                                  "explain_execution_ms": float(execution.group(1)) if execution else None,
                                  "explain_analyze_buffers": plan})
        return {"statement_count": len(statements), "whole_dashboard": _ms(whole), "queries": per_query}
