import json
import math
import os
import platform
import statistics
import time
import tracemalloc
import uuid
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.db.models import Count, F, Sum
from django.db.models.functions import TruncDate
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from movies.models import Booking, BookingSeat, Movie, Payment, Screen, Seat, Show, ShowSeat, Theater


class Command(BaseCommand):
    help = "Seed and measure a representative 100,000+ booking workload on PostgreSQL. Use only on a disposable database."

    def add_arguments(self, parser):
        parser.add_argument("--count", type=int, default=100000)
        parser.add_argument("--confirm-disposable", action="store_true",
                            help="Confirm this is an empty, disposable PostgreSQL database dedicated to benchmarking.")

    def handle(self, *args, **options):
        count = options["count"]
        if count < 100000:
            raise CommandError("The assessment requires at least 100,000 bookings.")
        self._validate_target(connection.vendor, connection.settings_dict.get("NAME", ""), options["confirm_disposable"])
        User = get_user_model()
        protected_models = [User, Movie, Theater, Screen, Seat, Show, ShowSeat, Booking, Payment, BookingSeat]
        populated = [model._meta.label for model in protected_models if model.objects.exists()]
        if populated:
            raise CommandError("Benchmark target must have empty application tables; found existing rows in: " + ", ".join(populated))

        seed_started = time.perf_counter()
        users = self._users()
        movies = list(Movie.objects.order_by("pk")[:10])
        while len(movies) < 10:
            movies.append(Movie.objects.create(name=f"Benchmark Film {len(movies) + 1}"))
        theater = Theater.objects.first() or Theater.objects.create(name="Benchmark Theater")
        screen = Screen.objects.filter(theater=theater).first() or Screen.objects.create(theater=theater, name="Benchmark Screen")
        seats = list(Seat.objects.filter(screen=screen).order_by("pk")[:100])
        if len(seats) < 100:
            add = [Seat(screen=screen, seat_number=f"Z{uuid.uuid4().hex[:8]}") for _ in range(100 - len(seats))]
            seats.extend(Seat.objects.bulk_create(add, batch_size=100))
        now = timezone.now()
        shows = Show.objects.bulk_create([
            Show(theater=theater, screen=screen, movie=movies[n % len(movies)],
                 starts_at=now - timedelta(days=(n % 365), hours=2), price=Decimal("250.00"))
            for n in range(math.ceil(count / 100))
        ], batch_size=1000)

        seeded = 0
        batch_size = 1000
        for start in range(0, count, batch_size):
            batch = []
            timestamps = []
            target_statuses = []
            for offset in range(start, min(start + batch_size, count)):
                hour = (offset // 17) % 24
                show = shows[offset // 100]
                stamp = show.starts_at - timedelta(days=(offset % 30), hours=hour)
                timestamps.append(stamp)
                status = (Booking.CONFIRMED if offset % 10 < 6 else Booking.PENDING if offset % 10 < 8
                          else Booking.CANCELLED if offset % 10 == 8 else Booking.REFUNDED)
                target_statuses.append(status)
                batch.append(Booking(user=users[offset % len(users)], movie=show.movie, theater=theater, show=show,
                                     status=Booking.PENDING, total_amount=Decimal("250.00"), booked_at=stamp))
            with transaction.atomic():
                rows = Booking.objects.bulk_create(batch, batch_size=batch_size)
                for booking, stamp in zip(rows, timestamps): booking.booked_at = stamp
                Booking.objects.bulk_update(rows, ["booked_at"], batch_size=batch_size)
                payment_rows = []
                show_seat_rows = []
                booking_seat_rows = []
                for local, booking in enumerate(rows):
                    index = start + local
                    target_status = target_statuses[local]
                    payment_status = {
                        Booking.CONFIRMED: Payment.CAPTURED,
                        Booking.PENDING: Payment.CREATED,
                        Booking.CANCELLED: Payment.CANCELLED,
                        Booking.REFUNDED: Payment.REFUNDED,
                    }[target_status]
                    captured_at = booking.booked_at if payment_status in {Payment.CAPTURED, Payment.REFUNDED} else None
                    refunded_at = booking.booked_at if payment_status == Payment.REFUNDED else None
                    payment_rows.append(Payment(
                        booking=booking, amount=Decimal("250.00"), currency="INR", status=payment_status,
                        idempotency_key=f"bench-{uuid.uuid4().hex}", refund_status="processed" if payment_status == Payment.REFUNDED else "none",
                        refund_reason="benchmark" if payment_status == Payment.REFUNDED else "",
                        refund_id=f"bench-refund-{index}" if payment_status == Payment.REFUNDED else "",
                        transaction_id=f"bench-payment-{index}" if payment_status in {Payment.CAPTURED, Payment.REFUNDED} else "",
                        captured_at=captured_at, refunded_at=refunded_at))
                    show_index, seat_index = index // 100, index % 100
                    if target_status == Booking.CONFIRMED:
                        show_seat = ShowSeat(show=shows[show_index], seat=seats[seat_index],
                                             status=ShowSeat.BOOKED, active_booking=booking)
                    elif target_status == Booking.PENDING:
                        show_seat = ShowSeat(show=shows[show_index], seat=seats[seat_index],
                                             status=ShowSeat.HELD, held_by=booking.user,
                                             hold_expires_at=now + timedelta(seconds=120), active_booking=booking)
                    else:
                        show_seat = ShowSeat(show=shows[show_index], seat=seats[seat_index],
                                             status=ShowSeat.AVAILABLE)
                    show_seat_rows.append(show_seat)
                    booking_seat_rows.append(BookingSeat(booking=booking, show_seat=show_seat, price=Decimal("250.00")))
                Payment.objects.bulk_create(payment_rows, batch_size=batch_size)
                ShowSeat.objects.bulk_create(show_seat_rows, batch_size=batch_size)
                BookingSeat.objects.bulk_create(booking_seat_rows, batch_size=batch_size)
                for booking, status in zip(rows, target_statuses): booking.status = status
                Booking.objects.bulk_update(rows, ["status"], batch_size=batch_size)
            seeded += len(rows)

        seed_seconds = time.perf_counter() - seed_started

        status_counts = dict(Booking.objects.values_list("status").annotate(total=Count("pk")))
        expected_status_counts = {
            Booking.CONFIRMED: sum(1 for i in range(count) if i % 10 < 6),
            Booking.PENDING: sum(1 for i in range(count) if 6 <= i % 10 < 8),
            Booking.CANCELLED: sum(1 for i in range(count) if i % 10 == 8),
            Booking.REFUNDED: sum(1 for i in range(count) if i % 10 == 9),
        }
        mismatched_associations = BookingSeat.objects.exclude(booking__show_id=F("show_seat__show_id")).count()
        payment_mismatches = Payment.objects.exclude(amount=F("booking__total_amount")).count()
        payment_count_mismatches = Booking.objects.annotate(payment_count=Count("payments")).exclude(payment_count=1).count()
        payment_state_mismatches = (
            Booking.objects.filter(status=Booking.CONFIRMED).exclude(payments__status=Payment.CAPTURED).count()
            + Booking.objects.filter(status=Booking.PENDING).exclude(payments__status=Payment.CREATED).count()
            + Booking.objects.filter(status=Booking.CANCELLED).exclude(payments__status=Payment.CANCELLED).count()
            + Booking.objects.filter(status=Booking.REFUNDED).exclude(payments__status=Payment.REFUNDED).count()
        )
        confirmed_seat_mismatches = ShowSeat.objects.filter(status=ShowSeat.BOOKED).exclude(
            active_booking__status=Booking.CONFIRMED,
            active_booking__booking_seats__show_seat_id=F("pk"),
        ).count()
        pending_hold_mismatches = ShowSeat.objects.filter(status=ShowSeat.HELD).exclude(
            active_booking__status=Booking.PENDING,
            active_booking__user_id=F("held_by_id"),
            active_booking__booking_seats__show_seat_id=F("pk"),
        ).count()
        seeded_counts = {
            "bookings": Booking.objects.count(),
            "payments": Payment.objects.count(),
            "show_seats": ShowSeat.objects.count(),
            "booking_seats": BookingSeat.objects.count(),
            "confirmed_bookings": Booking.objects.filter(status=Booking.CONFIRMED).count(),
            "pending_bookings": Booking.objects.filter(status=Booking.PENDING).count(),
            "cancelled_bookings": Booking.objects.filter(status=Booking.CANCELLED).count(),
            "refunded_bookings": Booking.objects.filter(status=Booking.REFUNDED).count(),
            "booked_show_seats": ShowSeat.objects.filter(status=ShowSeat.BOOKED).count(),
            "held_show_seats": ShowSeat.objects.filter(status=ShowSeat.HELD).count(),
            "available_show_seats": ShowSeat.objects.filter(status=ShowSeat.AVAILABLE).count(),
        }
        if (seeded != count or seeded_counts["bookings"] != count or seeded_counts["payments"] != count
                or seeded_counts["show_seats"] != count or seeded_counts["booking_seats"] != count
                or status_counts != expected_status_counts or mismatched_associations or payment_mismatches
                or payment_count_mismatches or payment_state_mismatches or confirmed_seat_mismatches or pending_hold_mismatches
                or seeded_counts["booked_show_seats"] != expected_status_counts[Booking.CONFIRMED]
                or seeded_counts["held_show_seats"] != expected_status_counts[Booking.PENDING]
                or seeded_counts["available_show_seats"] != expected_status_counts[Booking.CANCELLED] + expected_status_counts[Booking.REFUNDED]):
            raise CommandError("Seeded benchmark rows failed the booking/payment/seat consistency audit. Discard and recreate this disposable database.")

        end = timezone.now()
        booking_query = Booking.objects.filter(booked_at__gte=end - timedelta(days=30), booked_at__lte=end)
        payment_query = Payment.objects.filter(status=Payment.CAPTURED, captured_at__gte=end - timedelta(days=30), captured_at__lte=end)
        queryset_factories = {
            "daily_bookings": lambda: booking_query.annotate(day=TruncDate("booked_at")).values("day").annotate(total=Count("id")),
            "daily_revenue": lambda: payment_query.annotate(day=TruncDate("captured_at")).values("day").annotate(total=Sum("amount")),
            "top_movies": lambda: booking_query.filter(status=Booking.CONFIRMED).values("movie__name").annotate(total=Count("id")).order_by("-total")[:10],
        }
        metrics = {}
        for name, queryset_factory in queryset_factories.items():
            times, query_counts = [], []
            tracemalloc.start()
            for _ in range(20):
                with CaptureQueriesContext(connection) as captured:
                    started_query = time.perf_counter()
                    list(queryset_factory())
                    times.append((time.perf_counter() - started_query) * 1000)
                    query_counts.append(len(captured))
            _, peak_bytes = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            ordered = sorted(times)
            metrics[name] = {"query_count": max(query_counts), "p50_ms": round(statistics.median(times), 2),
                             "p95_ms": round(ordered[min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1)], 2), "python_peak_bytes": peak_bytes,
                             "explain_analyze_buffers": queryset_factory().explain(analyze=True, buffers=True)}

        tracemalloc.start()
        export_started = time.perf_counter()
        export_rows = sum(1 for _ in Booking.objects.filter(booked_at__gte=end - timedelta(days=30), booked_at__lte=end).order_by("pk").values_list("reference", "status").iterator(chunk_size=1000))
        export_ms = (time.perf_counter() - export_started) * 1000
        _, export_peak_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_database_size(current_database())")
            database_size_bytes = cursor.fetchone()[0]
            cursor.execute("SELECT current_setting('max_connections'), current_setting('shared_buffers'), current_setting('work_mem'), current_setting('effective_cache_size')")
            max_connections, shared_buffers, work_mem, effective_cache_size = cursor.fetchone()
        report = {
            "assessment": "100000+ bookings", "seeded_bookings": seeded, "seeded_payments": seeded,
            "database_vendor": connection.vendor, "database_version": connection.pg_version,
            "python": platform.python_version(), "platform": platform.platform(), "cpu_count": os.cpu_count(),
            "database_size_bytes_after_seed": database_size_bytes,
            "postgres_settings": {"max_connections": max_connections, "shared_buffers": shared_buffers,
                                  "work_mem": work_mem, "effective_cache_size": effective_cache_size},
            "batch_size": batch_size, "seed_seconds": round(seed_seconds, 2),
            "seeded_counts": seeded_counts,
            "integrity_audit": {"status_counts": status_counts, "expected_status_counts": expected_status_counts,
                                "booking_seat_show_mismatches": mismatched_associations,
                                "payment_amount_mismatches": payment_mismatches,
                                "booking_payment_count_mismatches": payment_count_mismatches,
                                "booking_payment_state_mismatches": payment_state_mismatches,
                                "confirmed_seat_mismatches": confirmed_seat_mismatches,
                                "pending_hold_mismatches": pending_hold_mismatches},
            "date_range": {"start": (end - timedelta(days=30)).isoformat(), "end": end.isoformat()},
            "queries": metrics,
            "streaming_export": {"rows": export_rows, "elapsed_ms": round(export_ms, 2), "python_peak_bytes": export_peak_bytes, "chunk_size": 1000},
            "notice": "This command inserts durable synthetic rows. Run only against a disposable PostgreSQL database and save this JSON with the database hardware details.",
        }
        self.stdout.write(json.dumps(report, indent=2, default=str))

    @staticmethod
    def _validate_target(vendor, database_name, confirmed):
        if vendor != "postgresql":
            raise CommandError("This scale assessment requires PostgreSQL; current database vendor is " + vendor)
        if not confirmed:
            raise CommandError("Pass --confirm-disposable only after selecting a dedicated disposable benchmark database.")
        name = str(database_name).lower()
        if not any(marker in name for marker in ("bench", "benchmark", "disposable")):
            raise CommandError("Benchmark database name must include 'bench', 'benchmark', or 'disposable'.")

    def _users(self):
        User = get_user_model()
        users = list(User.objects.order_by("pk")[:100])
        if len(users) >= 100:
            return users
        tag = uuid.uuid4().hex[:10]
        additional = [User(username=f"bench-{tag}-{i}", email=f"bench-{tag}-{i}@example.invalid") for i in range(100 - len(users))]
        for user in additional:
            user.set_unusable_password()
        User.objects.bulk_create(additional, batch_size=100)
        return users + additional
