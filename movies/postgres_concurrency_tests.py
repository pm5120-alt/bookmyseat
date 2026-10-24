"""Real PostgreSQL row-lock acceptance tests; run explicitly on a disposable DB.

These tests intentionally use TransactionTestCase (not TestCase) and each
request runs in its own thread, database connection, authenticated Django
client, and transaction. SQLite is explicitly skipped because it does not
implement SELECT FOR UPDATE row locks.
"""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import threading
from decimal import Decimal
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, connections, transaction
from django.test import Client, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from unittest.mock import patch

from django.test import override_settings

from movies.models import Booking, BookingSeat, City, Movie, Payment, Screen, Seat, Show, ShowSeat, Theater


User = get_user_model()


@skipUnless(
    connection.vendor == "postgresql",
    "BLOCKED/SKIPPED: requires PostgreSQL row-level SELECT FOR UPDATE locks; SQLite cannot verify concurrent reservation behavior.",
)
class PostgreSQLSeatReservationConcurrencyTests(TransactionTestCase):
    reset_sequences = True
    attempt_count = 50
    lock_timeout_ms = 10_000
    statement_timeout_ms = 30_000
    future_timeout_seconds = 45

    def setUp(self):
        self.city = City.objects.create(name="Concurrency Test City", slug="concurrency-test-city")
        self.movie = Movie.objects.create(name="Concurrency Test Film", duration_minutes=100)
        self.theater = Theater.objects.create(name="Concurrency Test Cinema", city=self.city)
        self.screen = Screen.objects.create(theater=self.theater, name="Concurrency Screen")
        self.show = Show.objects.create(
            theater=self.theater,
            screen=self.screen,
            movie=self.movie,
            starts_at=timezone.now() + timedelta(days=1),
            price=Decimal("250.00"),
        )
        self.seats = [
            Seat.objects.create(screen=self.screen, seat_number=f"P{i}")
            for i in range(1, 4)
        ]
        ShowSeat.objects.bulk_create([ShowSeat(show=self.show, seat=seat) for seat in self.seats])
        self.users = [
            User.objects.create_user(username=f"pg-racer-{i:02d}", password="Concurrency-Only-Test-Password")
            for i in range(self.attempt_count)
        ]

    def _race(self, selections):
        self.assertEqual(len(selections), self.attempt_count)
        barrier = threading.Barrier(self.attempt_count)
        url = reverse("reserve_seats", args=[self.show.pk])
        seat_ids = [seat.pk for seat in self.seats]

        def attempt(index, selected_indexes):
            close_old_connections()
            client = Client()
            db = connections["default"]
            try:
                user = User.objects.get(pk=self.users[index].pk)
                client.force_login(user)
                with db.cursor() as cursor:
                    cursor.execute("SELECT set_config('lock_timeout', %s, false)", [f"{self.lock_timeout_ms}ms"])
                    cursor.execute("SELECT set_config('statement_timeout', %s, false)", [f"{self.statement_timeout_ms}ms"])
                    cursor.execute("SELECT pg_backend_pid()")
                    backend_pid = cursor.fetchone()[0]
                barrier.wait(timeout=25)
                response = client.post(url, {"seats": [str(seat_ids[i]) for i in selected_indexes]})
                return {
                    "user_id": user.pk,
                    "backend_pid": backend_pid,
                    "status": response.status_code,
                    "payload": response.json(),
                    "selected_seat_ids": {seat_ids[i] for i in selected_indexes},
                }
            finally:
                db = connections["default"]
                db.close()
                close_old_connections()

        pool = ThreadPoolExecutor(max_workers=self.attempt_count, thread_name_prefix="bookmyseat-pg-race")
        futures = [pool.submit(attempt, index, selected) for index, selected in enumerate(selections)]
        try:
            return [future.result(timeout=self.future_timeout_seconds) for future in futures]
        except Exception:
            barrier.abort()
            for future in futures:
                future.cancel()
            raise
        finally:
            pool.shutdown(wait=True, cancel_futures=True)

    def _assert_single_atomic_winner(self, results, expected_seat_indexes):
        self.assertEqual(len({item["backend_pid"] for item in results}), self.attempt_count,
                         "Every racer must use a distinct PostgreSQL backend connection.")
        status_counts = Counter(item["status"] for item in results)
        self.assertEqual(status_counts, Counter({201: 1, 409: self.attempt_count - 1}),
                         f"Expected one complete hold and {self.attempt_count - 1} conflicts; got {status_counts}.")

        winners = [item for item in results if item["status"] == 201]
        winner = winners[0]
        winner_id = winner["user_id"]
        booking = Booking.objects.get(user_id=winner_id, show=self.show)
        self.assertEqual(booking.status, Booking.PENDING)
        self.assertEqual(Booking.objects.filter(show=self.show).count(), 1,
                         "Conflict responses must not leave partially-created bookings.")
        lines = list(BookingSeat.objects.filter(booking=booking).select_related("show_seat"))
        expected_ids = {self.seats[i].pk for i in expected_seat_indexes}
        self.assertEqual(winner["selected_seat_ids"], expected_ids)
        self.assertEqual({line.show_seat.seat_id for line in lines}, expected_ids)
        self.assertEqual(len(lines), len(expected_seat_indexes))

        rows = list(ShowSeat.objects.filter(show=self.show).order_by("seat_id"))
        held = [row for row in rows if row.status == ShowSeat.HELD]
        self.assertEqual(len(held), len(expected_seat_indexes))
        self.assertEqual({row.seat_id for row in held}, expected_ids)
        self.assertTrue(all(row.active_booking_id == booking.pk and row.held_by_id == winner_id for row in held))
        self.assertTrue(all(row.hold_expires_at and row.hold_expires_at > timezone.now() for row in held))
        self.assertEqual(sum(row.status == ShowSeat.BOOKED for row in rows), 0)
        self.assertEqual(BookingSeat.objects.filter(show_seat__show=self.show).count(), len(expected_seat_indexes))

    def test_existing_multi_seat_booking_lookup_is_lockable_and_deduplicated(self):
        """Exercise the PostgreSQL row-lock query when one booking matches multiple seat rows."""
        client = Client()
        user = self.users[0]
        client.force_login(user)
        url = reverse("reserve_seats", args=[self.show.pk])
        selected = [str(self.seats[0].pk), str(self.seats[1].pk)]

        first = client.post(url, {"seats": selected})
        self.assertEqual(first.status_code, 201, first.content.decode())
        booking = Booking.objects.get(reference=first.json()["booking"])
        self.assertEqual(BookingSeat.objects.filter(booking=booking).count(), 2)

        # The existing-booking predicate matches two BookingSeat rows. The
        # outer Booking query must still lock one row and reuse that booking.
        second = client.post(url, {"seats": selected})
        self.assertEqual(second.status_code, 201, second.content.decode())
        self.assertEqual(second.json()["booking"], first.json()["booking"])
        self.assertEqual(Booking.objects.filter(user=user, show=self.show).count(), 1)
        self.assertEqual(BookingSeat.objects.filter(booking=booking).count(), 2)

        rows = list(ShowSeat.objects.filter(show=self.show, seat__in=self.seats[:2]).order_by("seat_id"))
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row.status == ShowSeat.HELD for row in rows))
        self.assertTrue(all(row.active_booking_id == booking.pk for row in rows))
        self.assertTrue(all(row.held_by_id == user.pk for row in rows))

    def test_same_user_concurrent_disjoint_requests_reuse_one_booking(self):
        user = self.users[0]
        url = reverse("reserve_seats", args=[self.show.pk])
        barrier = threading.Barrier(2)

        def attempt(seat):
            close_old_connections()
            client = Client()
            db = connections["default"]
            try:
                racer = User.objects.get(pk=user.pk)
                client.force_login(racer)
                with db.cursor() as cursor:
                    cursor.execute("SELECT set_config('lock_timeout', %s, false)", [f"{self.lock_timeout_ms}ms"])
                    cursor.execute("SELECT set_config('statement_timeout', %s, false)", [f"{self.statement_timeout_ms}ms"])
                    cursor.execute("SELECT pg_backend_pid()")
                    backend_pid = cursor.fetchone()[0]
                barrier.wait(timeout=25)
                response = client.post(url, {"seats": [str(seat.pk)]})
                return backend_pid, response.status_code, response.json()
            finally:
                connections["default"].close()
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="bookmyseat-same-user") as pool:
            futures = [pool.submit(attempt, seat) for seat in self.seats[:2]]
            results = [future.result(timeout=self.future_timeout_seconds) for future in futures]

        self.assertEqual(len({result[0] for result in results}), 2)
        self.assertEqual([result[1] for result in results], [201, 201])
        self.assertEqual(len({result[2]["booking"] for result in results}), 1)
        booking = Booking.objects.get(user=user, show=self.show)
        self.assertEqual(Booking.objects.filter(user=user, show=self.show).count(), 1)
        lines = list(BookingSeat.objects.filter(booking=booking).select_related("show_seat"))
        self.assertEqual({line.show_seat.seat_id for line in lines}, {self.seats[0].pk, self.seats[1].pk})
        self.assertEqual(booking.total_amount, sum((line.price for line in lines), Decimal("0.00")))
        self.assertEqual(booking.total_amount, self.show.price * 2)
        rows = list(ShowSeat.objects.filter(show=self.show, seat__in=self.seats[:2]))
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row.status == ShowSeat.HELD for row in rows))
        self.assertTrue(all(row.active_booking_id == booking.pk and row.held_by_id == user.pk for row in rows))

    def test_fifty_opposite_order_requests_for_same_seats_have_one_atomic_winner(self):
        forward = [0, 1]
        reverse = [1, 0]
        selections = [forward if index % 2 == 0 else reverse for index in range(self.attempt_count)]
        results = self._race(selections)
        self._assert_single_atomic_winner(results, {0, 1})

    def test_fifty_partially_overlapping_pairs_never_leave_partial_holds(self):
        left = [0, 1]
        right = [1, 2]
        selections = [left if index % 2 == 0 else right for index in range(self.attempt_count)]
        results = self._race(selections)
        winner = next(item for item in results if item["status"] == 201)
        actual = winner["selected_seat_ids"]
        allowed = ({self.seats[0].pk, self.seats[1].pk}, {self.seats[1].pk, self.seats[2].pk})
        self.assertIn(actual, allowed)
        expected_indexes = {i for i, seat in enumerate(self.seats) if seat.pk in actual}
        self._assert_single_atomic_winner(results, expected_indexes)
        self.assertEqual(ShowSeat.objects.filter(show=self.show, status=ShowSeat.AVAILABLE).count(), 1)



@skipUnless(
    connection.vendor == "postgresql",
    "BLOCKED/SKIPPED: requires PostgreSQL row-level SELECT FOR UPDATE locks.",
)
@override_settings(RAZORPAY_KEY_ID="test-key", RAZORPAY_KEY_SECRET="test-secret")
class PostgreSQLPaymentConcurrencyTests(TransactionTestCase):
    """Payment-order creation and Booking->Payment->ShowSeat lock-order checks.

    NOT YET EXECUTED against PostgreSQL by the author of this change (no
    PostgreSQL in the authoring sandbox). Run with scripts/run_postgres_concurrency.ps1.
    """
    reset_sequences = True

    def setUp(self):
        city = City.objects.create(name="Pay Concurrency City", slug="pay-concurrency-city")
        self.movie = Movie.objects.create(name="Pay Concurrency Film", duration_minutes=100)
        self.theater = Theater.objects.create(name="Pay Concurrency Cinema", city=city)
        self.screen = Screen.objects.create(theater=self.theater, name="PC Screen")
        self.show = Show.objects.create(theater=self.theater, screen=self.screen, movie=self.movie,
                                        starts_at=timezone.now() + timedelta(days=1), price=Decimal("250.00"))
        self.seats = [Seat.objects.create(screen=self.screen, seat_number=f"Q{i}") for i in range(1, 3)]
        ShowSeat.objects.bulk_create([ShowSeat(show=self.show, seat=seat) for seat in self.seats])
        self.user = User.objects.create_user(username="pay-racer",password="Concurrency-Only-Test-Password",email="pay-racer@example.test",)
        client = Client(); client.force_login(self.user)
        response = client.post(reverse("reserve_seats", args=[self.show.pk]), {"seats": [self.seats[0].pk]})
        self.assertEqual(response.status_code, 201)
        self.booking = Booking.objects.get(reference=response.json()["booking"])

    def _in_thread(self, func):
        def runner():
            close_old_connections()
            try:
                return func()
            finally:
                connections["default"].close()
        return runner

    def test_concurrent_checkout_requests_create_one_provider_order(self):
        entered, release = threading.Event(), threading.Event()
        calls = []

        def blocking_create(payload):
            calls.append(payload)
            entered.set()
            self.assertTrue(release.wait(timeout=20))
            return {"id": "order_pg_1"}

        def checkout():
            client = Client(); client.force_login(User.objects.get(pk=self.user.pk))
            response = client.post(reverse("create_payment_order", args=[self.booking.reference]))
            return response.status_code, response.json()

        with patch("razorpay.Client") as provider:
            provider.return_value.order.create.side_effect = blocking_create
            pool = ThreadPoolExecutor(max_workers=2)
            try:
                first = pool.submit(self._in_thread(checkout))
                self.assertTrue(entered.wait(timeout=20), "first provider request never started")
                # First request is blocked inside the provider call; its DB transaction is closed.
                row = Payment.objects.get(booking=self.booking)
                self.assertEqual((row.order_state, row.provider_order_id), (Payment.ORDER_CREATING, ""))
                second = pool.submit(self._in_thread(checkout))
                second_status, second_body = second.result(timeout=20)
                self.assertEqual(second_status, 409)
                self.assertEqual(second_body["status"], "order_creation_in_progress")
                release.set()
                first_status, first_body = first.result(timeout=20)
            finally:
                release.set()
                pool.shutdown(wait=True)
            self.assertEqual(provider.return_value.order.create.call_count, 1)
        self.assertEqual(first_status, 200)
        self.assertEqual(first_body["order_id"], "order_pg_1")
        self.assertEqual(first_body["amount"], 25000)
        self.assertEqual(len(calls), 1)
        payment = Payment.objects.get(booking=self.booking)
        self.assertEqual((Payment.objects.filter(booking=self.booking).count(), payment.provider_order_id, payment.order_state, payment.amount, payment.status),
                         (1, "order_pg_1", Payment.ORDER_CREATED, Decimal("250.00"), Payment.CREATED))

    def test_confirm_and_cancel_race_leaves_consistent_state_without_deadlock(self):
        from movies.views import _confirm_payment
        payment = Payment.objects.create(booking=self.booking, amount=self.booking.total_amount,
                                         provider_order_id="order_pg_race", idempotency_key="pg-race")
        barrier = threading.Barrier(2)

        def confirm():
            barrier.wait(timeout=10)
            return _confirm_payment(Payment.objects.get(pk=payment.pk), "pay_pg_race").status_code

        def cancel():
            client = Client(); client.force_login(User.objects.get(pk=self.user.pk))
            barrier.wait(timeout=10)
            return client.post(reverse("cancel_booking", args=[self.booking.reference]),
                               HTTP_X_REQUESTED_WITH="XMLHttpRequest").status_code

        with patch("movies.views._enqueue_ticket"), patch("movies.views._enqueue_refund"), patch("movies.views._enqueue_late_refund"):
            pool = ThreadPoolExecutor(max_workers=2)
            try:
                futures = [pool.submit(self._in_thread(confirm)), pool.submit(self._in_thread(cancel))]
                results = [future.result(timeout=30) for future in futures]
            finally:
                pool.shutdown(wait=True)
        self.assertEqual(len(results), 2)
        payment.refresh_from_db(); self.booking.refresh_from_db()
        self.assertEqual(payment.status, Payment.CAPTURED)
        seats = list(ShowSeat.objects.filter(show=self.show, seat=self.seats[0]))
        if self.booking.status in {Booking.CONFIRMED, Booking.CANCELLATION_PENDING}:
            self.assertEqual([row.status for row in seats], [ShowSeat.BOOKED])
        else:
            self.assertEqual(self.booking.status, Booking.CANCELLED)
            self.assertEqual([row.status for row in seats], [ShowSeat.AVAILABLE])
            self.assertEqual(payment.refund_status, "pending")

    def test_ticket_task_locks_only_booking_for_show_and_legacy_rows(self):
        from movies.tasks import send_booking_ticket

        bookings = [self.booking]
        legacy = Booking.objects.create(
            user=self.user, movie=self.movie, theater=self.theater, show=None,
            total_amount=Decimal("250.00"),
        )
        # Build a legacy-shaped confirmed row while retaining its historical
        # booked seat line required by the database confirmation invariant.
        legacy_row = ShowSeat.objects.get(show=self.show, seat=self.seats[1])
        legacy_row.status = ShowSeat.BOOKED
        legacy_row.active_booking = legacy
        legacy_row.save(update_fields=["status", "active_booking"])
        BookingSeat.objects.create(booking=legacy, show_seat=legacy_row, price=legacy.total_amount)
        Payment.objects.create(
            booking=legacy, amount=legacy.total_amount, status=Payment.CAPTURED,
            transaction_id="pay_ticket_pg_legacy", idempotency_key="ticket-pg-legacy",
        )
        legacy.status = Booking.CONFIRMED
        legacy.save(update_fields=["status"])
        bookings.append(legacy)

        payment = Payment.objects.create(
            booking=self.booking, amount=self.booking.total_amount,
            transaction_id="pay_ticket_pg_show", status=Payment.CAPTURED,
            idempotency_key="ticket-pg-show",
        )
        show_row = ShowSeat.objects.get(show=self.show, seat=self.seats[0])
        show_row.status, show_row.active_booking = ShowSeat.BOOKED, self.booking
        show_row.held_by, show_row.hold_expires_at = None, None
        show_row.save(update_fields=["status", "active_booking", "held_by", "hold_expires_at"])
        BookingSeat.objects.get_or_create(
            booking=self.booking, show_seat=show_row,
            defaults={"price": self.booking.total_amount},
        )
        self.booking.status = Booking.CONFIRMED
        self.booking.save(update_fields=["status"])

        query = Booking.objects.select_for_update(of=("self",)).select_related(
            "user", "movie", "theater", "show__screen"
        ).filter(pk__in=[booking.pk for booking in bookings])
        with transaction.atomic(using=query.db):
            sql = str(query.query)

        self.assertIn("FOR UPDATE OF", sql)
        self.assertIn('"movies_booking"', sql)
        self.assertNotIn('"movies_show"', sql.rsplit("FOR UPDATE", 1)[-1])

        # Invoke only the task's DB path. PDF generation, local file storage,
        # and SMTP are mocked and are not claimed as verified by this test.
        with patch("movies.tasks._render_pdf", return_value=b"%PDF-test"), \
                patch("django.db.models.fields.files.FieldFile.save"), \
                patch("movies.tasks.EmailMessage.send", return_value=1) as send:
            self.assertEqual(send_booking_ticket.run(bookings[0].pk), "sent")
            self.assertEqual(send_booking_ticket.run(bookings[1].pk), "sent")
        self.assertEqual(send.call_count, 2)
        for booking in bookings:
            booking.refresh_from_db()
            self.assertEqual(booking.ticket_delivery_status, "sent")

    def test_refund_and_confirmation_race_has_consistent_final_state(self):
        from movies.tasks import refund_late_payment
        from movies.views import _confirm_payment, _process_payment_webhook

        payment = Payment.objects.create(
            booking=self.booking, amount=self.booking.total_amount,
            provider_order_id="order_pg_refund_race", idempotency_key="pg-refund-race",
        )
        with patch("movies.views._enqueue_ticket"):
            confirmed = _confirm_payment(payment, "pay_pg_refund_race")
        self.assertEqual(confirmed.status_code, 200)
        payment.refresh_from_db()
        payment.refund_status = "pending"
        payment.refund_reason = "late_payment"
        payment.save(update_fields=["refund_status", "refund_reason", "updated_at"])

        provider_entered, provider_release = threading.Event(), threading.Event()

        def delayed_provider_refund(_transaction_id, _payload):
            provider_entered.set()
            if not provider_release.wait(timeout=20):
                raise TimeoutError("test provider response release timed out")
            return {"id": "rfnd_pg_refund_race", "status": "pending"}

        def new_pg_connection():
            close_old_connections()
            db = connections["default"]
            with db.cursor() as cursor:
                cursor.execute("SELECT set_config('lock_timeout', %s, false)", ["10000ms"])
                cursor.execute("SELECT set_config('statement_timeout', %s, false)", ["30000ms"])
                cursor.execute("SELECT pg_backend_pid()")
                backend_pid = cursor.fetchone()[0]
            return db, backend_pid

        def refund_worker():
            db, backend_pid = new_pg_connection()
            try:
                return backend_pid, refund_late_payment.run(payment.pk)
            finally:
                db.close()
                close_old_connections()

        def confirmation_worker():
            db, backend_pid = new_pg_connection()
            try:
                response = _confirm_payment(Payment.objects.get(pk=payment.pk), "pay_pg_refund_race")
                _process_payment_webhook({"event": "refund.processed", "payload": {"refund": {"entity": {
                    "id": "rfnd_pg_refund_race", "payment_id": "pay_pg_refund_race",
                    "amount": 25000, "status": "processed",
                }}}})
                return backend_pid, response.status_code
            finally:
                db.close()
                close_old_connections()

        with patch("razorpay.Client") as client:
            client.return_value.payment.refund.side_effect = delayed_provider_refund
            pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="bookmyseat-refund-pg-race")
            try:
                refund_future = pool.submit(refund_worker)
                self.assertTrue(provider_entered.wait(timeout=15), "refund provider call did not start")
                # The provider call is outside the transaction while an
                # independent connection confirms/reconciles the same payment.
                confirmation_pid, confirm_status = pool.submit(confirmation_worker).result(timeout=30)
                provider_release.set()
                refund_pid, refund_result = refund_future.result(timeout=30)
            finally:
                provider_release.set()
                pool.shutdown(wait=True, cancel_futures=True)

        self.assertNotEqual(refund_pid, confirmation_pid)
        self.assertEqual(confirm_status, 200)
        self.assertEqual(client.return_value.payment.refund.call_count, 1)
        self.assertEqual(refund_result, "already_refunded")
        payment.refresh_from_db(); self.booking.refresh_from_db()
        self.assertEqual((payment.status, payment.refund_status, payment.refund_id),
                         (Payment.REFUNDED, "processed", "rfnd_pg_refund_race"))
        self.assertEqual(self.booking.status, Booking.REFUNDED)
        seat_row = ShowSeat.objects.get(show=self.show, seat=self.seats[0])
        self.assertEqual((seat_row.status, seat_row.active_booking_id, seat_row.held_by_id),
                         (ShowSeat.AVAILABLE, None, None))
