import hashlib
import hmac
import json
import io
import os
import re
import subprocess
import sys
import time
from datetime import datetime, time as day_time, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core import signing
from django.core import mail
from django.db import IntegrityError, transaction
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (Booking, BookingSeat, City, Genre, Language, Movie, Payment,
                     PaymentWebhookEvent, RecentlyViewedMovie, Review, Screen,
                     Seat, Show, ShowSeat, Theater, extract_youtube_video_id, validate_poster)
from .tasks import _render_pdf, recover_ticket_deliveries, refund_late_payment, release_expired_holds, send_booking_ticket
from .views import _confirm_payment, _process_payment_webhook

User = get_user_model()


class DomainTestCase(TestCase):
    def test_create_demo_admin_uses_environment_password_and_scoped_permission(self):
        from io import StringIO
        from django.contrib.auth.models import Group
        output = StringIO()
        with patch.dict(os.environ, {"BOOKMYSEAT_DEMO_ADMIN_PASSWORD": "DemoEnvironmentPass!842"}):
            call_command("create_demo_admin", stdout=output)
        demo = User.objects.get(username="demo_admin")
        self.assertTrue(demo.check_password("DemoEnvironmentPass!842"))
        self.assertTrue(demo.is_staff)
        self.assertFalse(demo.is_superuser)
        self.assertIn("DEMO ONLY", demo.last_name)
        group = Group.objects.get(name="BookMySeat Demo Administrators")
        demo.refresh_from_db()
        self.assertIn(group, demo.groups.all())
        self.assertTrue(demo.has_perm("movies.view_booking_analytics"))
        self.assertEqual(set(group.permissions.values_list("codename", flat=True)), {"view_booking_analytics"})
        self.assertNotIn("DemoEnvironmentPass!842", output.getvalue())
        self.client.force_login(demo)
        self.assertEqual(self.client.get(reverse("analytics_dashboard")).status_code, 200)

    def test_create_demo_admin_generates_and_displays_password_once(self):
        from io import StringIO
        output = StringIO()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BOOKMYSEAT_DEMO_ADMIN_PASSWORD", None)
            call_command("create_demo_admin", stdout=output)
        result = output.getvalue()
        marker = "One-time generated password for this demo environment: "
        self.assertEqual(result.count(marker), 1)
        password = result.split(marker, 1)[1].splitlines()[0]
        self.assertGreaterEqual(len(password), 32)
        demo = User.objects.get(username="demo_admin")
        self.assertTrue(demo.check_password(password))
        self.assertFalse(demo.is_superuser)

    def test_create_demo_admin_refuses_existing_account_without_mutating_credentials(self):
        from io import StringIO
        original = User.objects.create_superuser(
            "demo_admin", "original@example.test", "OriginalAdminSecret!987"
        )
        before_hash = original.password
        output = StringIO()
        with patch.dict(os.environ, {"BOOKMYSEAT_DEMO_ADMIN_PASSWORD": "MustNotBeApplied!847"}):
            with self.assertRaises(CommandError):
                call_command("create_demo_admin", stdout=output)
        original.refresh_from_db()
        self.assertEqual(original.password, before_hash)
        self.assertTrue(original.check_password("OriginalAdminSecret!987"))
        self.assertFalse(original.groups.exists())
        self.assertNotIn("OriginalAdminSecret!987", output.getvalue())
        self.assertNotIn("MustNotBeApplied!847", output.getvalue())

    def test_settings_fail_closed_without_an_explicit_secret(self):
        from pathlib import Path
        project_root = Path(__file__).resolve().parent.parent
        env = os.environ.copy()
        for name in ("DJANGO_SECRET_KEY", "DJANGO_DEBUG", "DATABASE_URL"):
            env.pop(name, None)
        env["PYTHON_DOTENV_DISABLED"] = "1"
        result = subprocess.run(
            [sys.executable, "-c", "import bookmyseat.settings"],
            cwd=project_root, env=env, capture_output=True, text=True, timeout=10,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Set DJANGO_SECRET_KEY", result.stderr)

    def setUp(self):
        self.user = User.objects.create_user("viewer", "viewer@example.test", "StrongPass!234")
        self.other = User.objects.create_user("other", "other@example.test", "StrongPass!234")
        self.city = City.objects.create(name="Test City", slug="test-city")
        self.genre = Genre.objects.create(name="Drama")
        self.language = Language.objects.create(name="English")
        self.movie = Movie.objects.create(name="A Film", duration_minutes=120)
        self.movie.genres.add(self.genre); self.movie.languages.add(self.language)
        self.theater = Theater.objects.create(name="Cinema", movie=self.movie, time=timezone.now()+timedelta(days=1), city=self.city)
        self.screen = Screen.objects.create(theater=self.theater, name="Screen 1")
        self.show = Show.objects.create(theater=self.theater, screen=self.screen, movie=self.movie, starts_at=timezone.now()+timedelta(days=1), price=Decimal("250.00"))
        self.seats = [Seat.objects.create(screen=self.screen, seat_number=f"A{i}") for i in range(1, 4)]
        self.show_seats = [ShowSeat.objects.create(show=self.show, seat=seat) for seat in self.seats]

    def create_confirmed_booking(self, user=None, show=None, amount=None):
        from uuid import uuid4
        user, show = user or self.user, show or self.show
        amount = Decimal(str(amount if amount is not None else show.price))
        booking = Booking.objects.create(user=user, movie=show.movie, theater=show.theater, show=show, total_amount=amount)
        for seat in Seat.objects.filter(screen=show.screen):
            row, _ = ShowSeat.objects.get_or_create(show=show, seat=seat)
            if row.status == ShowSeat.AVAILABLE:
                row.status, row.active_booking = ShowSeat.BOOKED, booking
                row.save(update_fields=["status", "active_booking"])
                BookingSeat.objects.create(booking=booking, show_seat=row, price=amount)
                break
        else:
            raise AssertionError("Test show has no available seats")
        key = uuid4().hex
        Payment.objects.create(booking=booking, amount=amount, status=Payment.CAPTURED,
                               transaction_id=f"test-{key}", captured_at=timezone.now(), idempotency_key=key)
        booking.status = Booking.CONFIRMED
        booking.save(update_fields=["status"])
        return booking

    def test_home_omits_static_placeholder_sections(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        for placeholder in ("Event 1", "Premiere 1", "Music 1"):
            self.assertNotContains(response, placeholder)

    def test_home_shows_upcoming_catalogue_before_first_paid_booking(self):
        # A fresh demo has eligible upcoming movies but no captured bookings yet.
        # The homepage should not be blank just because nothing is trending.
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.movie, list(response.context["movies"]))
        self.assertContains(response, "A Film")

    def test_youtube_url_validation_normalizes_and_rejects_unsafe_domains(self):
        for source in ("abcdefghijk", "https://youtu.be/abcdefghijk", "https://www.youtube.com/watch?v=abcdefghijk&t=5", "https://youtube.com/embed/abcdefghijk"):
            self.assertEqual(extract_youtube_video_id(source), "abcdefghijk")
        for source in ("javascript:alert(1)", "https://youtube.com.evil.test/embed/abcdefghijk", "<iframe src=x>", "http://youtube.com/watch?v=abcdefghijk", "https://youtube.com/embed/short"):
            with self.assertRaises(ValidationError): extract_youtube_video_id(source)
        self.movie.trailer_video_id = "https://youtu.be/abcdefghijk"
        self.movie.save()
        self.movie.refresh_from_db()
        self.assertEqual(self.movie.trailer_video_id, "abcdefghijk")

    def test_uploaded_posters_are_validated_from_real_image_bytes(self):
        from PIL import Image
        image_bytes = io.BytesIO()
        Image.new("RGB", (2, 2), color="red").save(image_bytes, format="PNG")
        valid = SimpleUploadedFile("poster.png", image_bytes.getvalue(), content_type="image/jpeg")
        validate_poster(valid)  # File signature wins over a spoofed client MIME type.
        invalid = SimpleUploadedFile("poster.png", b"not an image", content_type="image/png")
        with self.assertRaises(ValidationError): validate_poster(invalid)
        validate_poster(None)

    def test_admin_can_create_and_edit_movie_with_legacy_cast_and_related_values(self):
        from django.urls import reverse
        admin_user = User.objects.create_superuser("movie-admin", "movie-admin@example.test", "StrongPass!234")
        self.client.force_login(admin_user)
        add_url = reverse("admin:movies_movie_add")
        self.assertEqual(self.client.get(add_url).status_code, 200)
        data = {"name": "Admin Feature", "image": "", "rating": "0.0", "cast": "Legacy Performer", "description": "Added through admin", "release_date": "", "duration_minutes": "120", "certification": "PG", "trailer_video_id": "", "genres": [str(self.genre.pk)], "languages": [str(self.language.pk)], "review_count": "0", "posters-TOTAL_FORMS": "0", "posters-INITIAL_FORMS": "0", "posters-MIN_NUM_FORMS": "0", "posters-MAX_NUM_FORMS": "1000", "cast_members-TOTAL_FORMS": "0", "cast_members-INITIAL_FORMS": "0", "cast_members-MIN_NUM_FORMS": "0", "cast_members-MAX_NUM_FORMS": "1000", "_save": "Save"}
        created = self.client.post(add_url, data, follow=True)
        self.assertEqual(created.status_code, 200)
        movie = Movie.objects.get(name="Admin Feature")
        self.assertEqual(movie.cast, "Legacy Performer")
        change_url = reverse("admin:movies_movie_change", args=[movie.pk])
        data.update({"name": "Admin Feature Edited", "cast": "Updated Legacy Cast"})
        edited = self.client.post(change_url, data, follow=True)
        self.assertEqual(edited.status_code, 200)
        movie.refresh_from_db()
        self.assertEqual(movie.name, "Admin Feature Edited")
        self.assertEqual(movie.cast, "Updated Legacy Cast")

    def test_admin_can_delete_unreferenced_movie(self):
        movie = Movie.objects.create(name="Removable Catalog Entry")
        admin_user = User.objects.create_superuser("delete-admin", "delete-admin@example.test", "StrongPass!234")
        self.client.force_login(admin_user)
        url = reverse("admin:movies_movie_delete", args=[movie.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.post(url, {"post": "yes"}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Movie.objects.filter(pk=movie.pk).exists())

    def test_admin_can_manage_catalog_schedules_and_related_objects(self):
        admin_user = User.objects.create_superuser("catalog-admin", "catalog-admin@example.test", "StrongPass!234")
        self.client.force_login(admin_user)

        def add(model, payload):
            response = self.client.post(reverse(f"admin:movies_{model}_add"), payload)
            errors = response.context["adminform"].errors if response.context and "adminform" in response.context else None
            self.assertEqual(response.status_code, 302, f"Admin add failed for {model}: {response.status_code}; errors={errors}")

        add("genre", {"name": "Catalog Thriller", "_save": "Save"})
        add("language", {"name": "Catalog Language", "_save": "Save"})
        add("city", {"name": "Admin City", "slug": "admin-city", "_save": "Save"})
        city = City.objects.get(slug="admin-city")
        add("theater", {"name": "Admin Theater", "movie": "", "time": "", "city": city.pk, "address": "Test address", "_save": "Save"})
        theater = Theater.objects.get(name="Admin Theater")
        add("screen", {"theater": theater.pk, "name": "Admin Screen", "_save": "Save"})
        screen = Screen.objects.get(theater=theater, name="Admin Screen")
        add("seat", {"screen": screen.pk, "seat_number": "Q1", "_save": "Save"})
        seat = Seat.objects.get(screen=screen, seat_number="Q1")
        starts_at = timezone.localtime(timezone.now() + timedelta(days=2))
        add("show", {"theater": theater.pk, "screen": screen.pk, "movie": self.movie.pk,
                     "starts_at_0": starts_at.strftime("%Y-%m-%d"), "starts_at_1": starts_at.strftime("%H:%M:%S"),
                     "price": "275.00", "is_active": "on", "_save": "Save"})
        show = Show.objects.get(theater=theater, screen=screen, movie=self.movie)
        self.assertEqual(show.price, Decimal("275.00"))
        self.assertEqual(show.screen.seats.get(), seat)

    def test_admin_can_edit_and_delete_related_catalog_records(self):
        admin_user = User.objects.create_superuser("catalog-crud-admin", "catalog-crud@example.test", "StrongPass!234")
        self.client.force_login(admin_user)

        def submit(model, action, obj=None, payload=None):
            url = reverse(f"admin:movies_{model}_{action}", args=[obj.pk] if obj else None)
            response = self.client.post(url, payload or {"post": "yes"})
            self.assertEqual(response.status_code, 302, f"Admin {action} failed for {model}: {response.status_code}")

        genre = Genre.objects.create(name="CRUD Genre")
        submit("genre", "change", genre, {"name": "CRUD Genre Edited", "_save": "Save"})
        genre.refresh_from_db(); self.assertEqual(genre.name, "CRUD Genre Edited")
        submit("genre", "delete", genre); self.assertFalse(Genre.objects.filter(pk=genre.pk).exists())

        language = Language.objects.create(name="CRUD Language")
        submit("language", "change", language, {"name": "CRUD Language Edited", "_save": "Save"})
        language.refresh_from_db(); self.assertEqual(language.name, "CRUD Language Edited")
        submit("language", "delete", language); self.assertFalse(Language.objects.filter(pk=language.pk).exists())

        city = City.objects.create(name="CRUD City", slug="crud-city")
        submit("city", "change", city, {"name": "CRUD City Edited", "slug": "crud-city-edited", "_save": "Save"})
        city.refresh_from_db(); self.assertEqual(city.slug, "crud-city-edited")
        submit("city", "delete", city); self.assertFalse(City.objects.filter(pk=city.pk).exists())

        theater = Theater.objects.create(name="CRUD Theater")
        submit("theater", "change", theater, {"name": "CRUD Theater Edited", "movie": "", "time": "", "city": "", "address": "Edited", "_save": "Save"})
        theater.refresh_from_db(); self.assertEqual(theater.address, "Edited")
        screen = Screen.objects.create(theater=theater, name="CRUD Screen")
        submit("screen", "change", screen, {"theater": str(theater.pk), "name": "CRUD Screen Edited", "_save": "Save"})
        screen.refresh_from_db(); self.assertEqual(screen.name, "CRUD Screen Edited")
        seat = Seat.objects.create(screen=screen, seat_number="C1")
        submit("seat", "change", seat, {"screen": str(screen.pk), "seat_number": "C2", "_save": "Save"})
        seat.refresh_from_db(); self.assertEqual(seat.seat_number, "C2")
        starts_at = timezone.localtime(timezone.now() + timedelta(days=3))
        show = Show.objects.create(theater=theater, screen=screen, movie=self.movie, starts_at=starts_at, price=Decimal("100.00"))
        edited_start = timezone.localtime(timezone.now() + timedelta(days=4))
        submit("show", "change", show, {
            "theater": str(theater.pk), "screen": str(screen.pk), "movie": str(self.movie.pk),
            "starts_at_0": edited_start.strftime("%Y-%m-%d"), "starts_at_1": edited_start.strftime("%H:%M:%S"),
            "price": "150.00", "is_active": "on", "_save": "Save",
        })
        show.refresh_from_db(); self.assertEqual(show.price, Decimal("150.00"))
        submit("show", "delete", show); self.assertFalse(Show.objects.filter(pk=show.pk).exists())
        submit("seat", "delete", seat); self.assertFalse(Seat.objects.filter(pk=seat.pk).exists())
        submit("screen", "delete", screen); self.assertFalse(Screen.objects.filter(pk=screen.pk).exists())
        submit("theater", "delete", theater); self.assertFalse(Theater.objects.filter(pk=theater.pk).exists())

    def test_non_admin_cannot_create_catalog_objects_through_admin_post(self):
        response = self.client.post(reverse("admin:movies_genre_add"), {"name": "Unauthorized", "_save": "Save"})
        self.assertIn(response.status_code, (302, 403))
        self.assertFalse(Genre.objects.filter(name="Unauthorized").exists())

    def test_admin_movie_form_creates_multiple_posters_and_ordered_cast(self):
        import tempfile
        from PIL import Image
        from django.test import override_settings

        with tempfile.TemporaryDirectory() as media_root:
            image_file = io.BytesIO()
            Image.new("RGB", (4, 4), color="blue").save(image_file, format="PNG")
            files = [SimpleUploadedFile(f"poster-{i}.png", image_file.getvalue(), content_type="image/png") for i in range(2)]
            admin_user = User.objects.create_superuser("inline-admin", "inline-admin@example.test", "StrongPass!234")
            self.client.force_login(admin_user)
            form = {
                "name": "Inline Catalog Movie", "image": "", "rating": "0.0", "cast": "",
                "description": "Admin-created movie", "release_date": "", "duration_minutes": "100",
                "certification": "PG", "trailer_video_id": "", "genres": [str(self.genre.pk)],
                "languages": [str(self.language.pk)], "review_count": "0", "_save": "Save",
                "posters-TOTAL_FORMS": "2", "posters-INITIAL_FORMS": "0", "posters-MIN_NUM_FORMS": "0", "posters-MAX_NUM_FORMS": "1000",
                "posters-0-alt_text": "Primary poster", "posters-0-sort_order": "0", "posters-0-is_primary": "on",
                "posters-1-alt_text": "Alternate poster", "posters-1-sort_order": "1",
                "cast_members-TOTAL_FORMS": "2", "cast_members-INITIAL_FORMS": "0", "cast_members-MIN_NUM_FORMS": "0", "cast_members-MAX_NUM_FORMS": "1000",
                "cast_members-0-name": "Lead Actor", "cast_members-0-role": "Lead", "cast_members-0-sort_order": "1",
                "cast_members-1-name": "Supporting Actor", "cast_members-1-role": "Support", "cast_members-1-sort_order": "2",
            }
            form.update({"posters-0-image": files[0], "posters-1-image": files[1]})
            with override_settings(MEDIA_ROOT=media_root):
                response = self.client.post(reverse("admin:movies_movie_add"), form)
            self.assertEqual(response.status_code, 302)
            movie = Movie.objects.get(name="Inline Catalog Movie")
            self.assertEqual(list(movie.cast_members.values_list("name", flat=True)), ["Lead Actor", "Supporting Actor"])
            self.assertEqual(movie.posters.count(), 2)
            self.assertEqual(movie.posters.get(is_primary=True).alt_text, "Primary poster")

            posters = list(movie.posters.order_by("sort_order"))
            cast = list(movie.cast_members.order_by("sort_order"))
            change = {
                "name": movie.name, "image": "", "rating": str(movie.rating), "cast": movie.cast,
                "description": movie.description or "", "release_date": "", "duration_minutes": str(movie.duration_minutes),
                "certification": movie.certification, "trailer_video_id": movie.trailer_video_id,
                "genres": [str(self.genre.pk)], "languages": [str(self.language.pk)], "review_count": str(movie.review_count),
                "_save": "Save",
                "posters-TOTAL_FORMS": "2", "posters-INITIAL_FORMS": "2", "posters-MIN_NUM_FORMS": "0", "posters-MAX_NUM_FORMS": "1000",
                "posters-0-id": str(posters[0].pk), "posters-0-movie": str(movie.pk), "posters-0-alt_text": "Edited primary poster",
                "posters-0-sort_order": "0", "posters-0-is_primary": "on",
                "posters-1-id": str(posters[1].pk), "posters-1-movie": str(movie.pk), "posters-1-alt_text": "Remove this poster",
                "posters-1-sort_order": "1", "posters-1-DELETE": "on",
                "cast_members-TOTAL_FORMS": "2", "cast_members-INITIAL_FORMS": "2", "cast_members-MIN_NUM_FORMS": "0", "cast_members-MAX_NUM_FORMS": "1000",
                "cast_members-0-id": str(cast[0].pk), "cast_members-0-movie": str(movie.pk), "cast_members-0-name": "Lead Actor",
                "cast_members-0-role": "Updated Lead", "cast_members-0-sort_order": "1",
                "cast_members-1-id": str(cast[1].pk), "cast_members-1-movie": str(movie.pk), "cast_members-1-name": "Supporting Actor",
                "cast_members-1-role": "Support", "cast_members-1-sort_order": "2", "cast_members-1-DELETE": "on",
            }
            with override_settings(MEDIA_ROOT=media_root):
                edited = self.client.post(reverse("admin:movies_movie_change", args=[movie.pk]), change)
            self.assertEqual(edited.status_code, 302)
            self.assertEqual(movie.posters.count(), 1)
            self.assertEqual(movie.posters.get().alt_text, "Edited primary poster")
            self.assertEqual(movie.cast_members.count(), 1)
            self.assertEqual(movie.cast_members.get().role, "Updated Lead")
            for poster in movie.posters.all():
                poster.image.delete(save=False)

    def test_ticket_delivery_recovery_requeues_pending_and_failed_jobs(self):
        booking = Booking.objects.create(user=self.user, movie=self.movie, theater=self.theater, show=self.show)
        with patch("movies.tasks.send_booking_ticket.delay") as dispatch:
            self.assertEqual(recover_ticket_deliveries.run(), 0)
        booking = self.create_confirmed_booking()
        with patch("movies.tasks.send_booking_ticket.delay") as dispatch:
            count = recover_ticket_deliveries.run()
        self.assertEqual(count, 1)
        dispatch.assert_called_once_with(booking.pk)

    def test_ticket_delivery_recovery_requeues_booking_after_email_becomes_available(self):
        no_email_user = User.objects.create_user("email-later", "", "StrongPass!234")
        booking = self.create_confirmed_booking(user=no_email_user)
        self.assertEqual(send_booking_ticket.run(booking.pk), "no_email")
        booking.refresh_from_db()
        self.assertEqual(booking.ticket_delivery_status, "no_email")

        no_email_user.email = "email-later@example.test"
        no_email_user.save(update_fields=["email"])
        with patch("movies.tasks.send_booking_ticket.delay") as dispatch:
            self.assertEqual(recover_ticket_deliveries.run(), 1)
        dispatch.assert_called_once_with(booking.pk)

    def test_profile_booking_history_is_complete_paginated_and_user_scoped(self):
        own_bookings = [
            Booking.objects.create(
                user=self.user, movie=self.movie, theater=self.theater, show=self.show,
                total_amount=Decimal("250.00"),
            )
            for _ in range(25)
        ]
        other_booking = Booking.objects.create(
            user=self.other, movie=self.movie, theater=self.theater, show=self.show,
            total_amount=Decimal("250.00"),
        )
        self.client.force_login(self.user)

        first_page = self.client.get(reverse("profile"))
        self.assertEqual(first_page.status_code, 200)
        self.assertEqual(first_page.context["page_obj"].paginator.count, 25)
        self.assertEqual(len(first_page.context["bookings"]), 20)
        self.assertContains(first_page, "Booking reference:")
        self.assertNotContains(first_page, str(other_booking.reference))

        second_page = self.client.get(reverse("profile"), {"page": "2"})
        self.assertEqual(len(second_page.context["bookings"]), 5)
        self.assertIn(own_bookings[0], second_page.context["bookings"])
        self.assertContains(second_page, str(own_bookings[0].reference))
        self.assertNotContains(second_page, str(other_booking.reference))

        last_page = self.client.get(reverse("profile"), {"page": "999"})
        self.assertEqual(last_page.context["page_obj"].number, 2)

    def test_demo_movie_uses_curated_static_poster_without_upload(self):
        demo = Movie.objects.create(name="Demo: Monsoon Circuit", duration_minutes=132)
        self.assertEqual(demo.poster_url, "")
        self.assertTrue(demo.display_poster_url.endswith("/static/posters/demo-monsoon-circuit.svg"))

    def test_unknown_movie_uses_generic_static_poster_without_upload(self):
        movie = Movie.objects.create(name="An Untitled Test Film")
        self.assertTrue(movie.display_poster_url.endswith("/static/posters/poster-default.svg"))

    def test_movie_detail_renders_safe_trailer_and_missing_poster_fallback(self):
        self.movie.description = "<script>alert('x')</script>"
        self.movie.trailer_video_id = "abcdefghijk"
        self.movie.save()
        response = self.client.get(reverse("movie_detail", args=[self.movie.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "https://www.youtube-nocookie.com/embed/abcdefghijk")
        self.assertContains(response, 'sandbox="allow-scripts allow-same-origin allow-presentation"')
        self.assertContains(response, "/static/posters/poster-default.svg")
        self.assertNotContains(response, "Poster unavailable")
        self.assertContains(response, "&lt;script&gt;")
        self.assertNotContains(response, "<script>alert('x')</script>")

    def test_reservation_is_atomic_when_any_requested_seat_conflicts(self):
        self.client.force_login(self.user)
        first = self.client.post(reverse("reserve_seats", args=[self.show.id]), {"seats": [self.seats[0].id]})
        self.assertEqual(first.status_code, 201)
        self.client.force_login(self.other)
        response = self.client.post(reverse("reserve_seats", args=[self.show.id]), {"seats": [self.seats[0].id, self.seats[1].id]})
        self.assertEqual(response.status_code, 409)
        self.show_seats[1].refresh_from_db()
        self.assertEqual(self.show_seats[1].status, ShowSeat.AVAILABLE)
        self.assertEqual(Booking.objects.filter(user=self.other).count(), 0)

    def test_seats_are_screen_scoped_and_show_rejects_screen_theater_mismatch(self):
        screen2 = Screen.objects.create(theater=self.theater, name="Screen 2")
        seat2 = Seat.objects.create(screen=screen2, seat_number="A1")
        show2 = Show.objects.create(theater=self.theater, screen=screen2, movie=self.movie, starts_at=timezone.now()+timedelta(days=1), price=300)
        ShowSeat.objects.create(show=show2, seat=seat2)
        self.client.force_login(self.user)
        seat_page = self.client.get(reverse("seat_selection", args=[self.show.id]))
        self.assertEqual(seat_page.context["seats"].count(), 3)
        self.assertContains(
            seat_page,
            reverse("create_payment_order", args=["00000000-0000-0000-0000-000000000000"]),
        )
        self.assertContains(seat_page, reverse("verify_payment", args=[123456789]))
        self.assertContains(seat_page, reverse("profile"))
        response = self.client.post(reverse("reserve_seats", args=[self.show.id]), {"seats": [self.seats[0].id]})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(show2.show_seats.get(seat=seat2).status, ShowSeat.AVAILABLE)
        wrong_screen = Screen.objects.create(theater=Theater.objects.create(name="Other Cinema"), name="Screen 1")
        with self.assertRaises(ValidationError):
            Show.objects.create(theater=self.theater, screen=wrong_screen, movie=self.movie, starts_at=timezone.now(), price=100)
        with self.assertRaises(ValidationError):
            ShowSeat.objects.create(show=self.show, seat=seat2)

    def test_new_booking_defaults_to_pending(self):
        booking = Booking.objects.create(user=self.user, movie=self.movie, theater=self.theater, show=self.show)
        self.assertEqual(booking.status, Booking.PENDING)
        booking.status = Booking.CONFIRMED
        with self.assertRaises(ValidationError): booking.save(update_fields=["status"])

    def test_only_owning_booking_can_confirm_a_held_seat(self):
        first = self.client.post(reverse("reserve_seats", args=[self.show.id]), {"seats": [self.seats[0].id]})
        self.assertEqual(first.status_code, 302)  # anonymous user is redirected
        self.client.force_login(self.user)
        reserved = self.client.post(reverse("reserve_seats", args=[self.show.id]), {"seats": [self.seats[0].id]})
        first_booking = Booking.objects.get(reference=reserved.json()["booking"])
        self.client.force_login(self.other)
        second = self.client.post(reverse("reserve_seats", args=[self.show.id]), {"seats": [self.seats[1].id]})
        second_booking = Booking.objects.get(reference=second.json()["booking"])
        shared = first_booking.booking_seats.first().show_seat
        BookingSeat.objects.create(booking=second_booking, show_seat=shared, price=self.show.price)
        payment = Payment.objects.create(booking=second_booking, amount=self.show.price, idempotency_key="ownership-invariant", provider_order_id="order-ownership")
        result = _confirm_payment(payment, "pay-ownership")
        second_booking.refresh_from_db(); shared.refresh_from_db(); payment.refresh_from_db()
        self.assertEqual(result.status_code, 409)
        self.assertEqual(second_booking.status, Booking.FAILED)
        self.assertEqual(shared.active_booking_id, first_booking.pk)
        self.assertEqual(shared.status, ShowSeat.HELD)

    def test_expired_hold_cannot_be_confirmed_after_provider_capture(self):
        booking = Booking.objects.create(user=self.user, movie=self.movie, theater=self.theater, show=self.show, status=Booking.PENDING, total_amount=self.show.price)
        row = self.show_seats[0]
        row.status, row.held_by, row.hold_expires_at, row.active_booking = ShowSeat.HELD, self.user, timezone.now()-timedelta(seconds=1), booking
        row.save(); BookingSeat.objects.create(booking=booking, show_seat=row, price=self.show.price)
        payment = Payment.objects.create(booking=booking, amount=booking.total_amount, idempotency_key="late-expired", provider_order_id="order-late")
        response = _confirm_payment(payment, "pay-late")
        booking.refresh_from_db(); row.refresh_from_db(); payment.refresh_from_db()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(booking.status, Booking.FAILED)
        self.assertEqual(row.status, ShowSeat.AVAILABLE)
        self.assertEqual(payment.status, Payment.CAPTURED)
        self.assertEqual(payment.refund_reason, "late_payment")

    def test_duplicate_provider_capture_is_recorded_and_refunded_once(self):
        self.client.force_login(self.user)
        reserved = self.client.post(reverse("reserve_seats", args=[self.show.pk]), {"seats": [self.seats[0].pk]})
        booking = Booking.objects.get(reference=reserved.json()["booking"])
        payment = Payment.objects.create(booking=booking, amount=booking.total_amount,
                                         provider_order_id="order-double-charge", idempotency_key="original-charge")
        with patch("movies.views._enqueue_ticket"), patch("movies.views._enqueue_late_refund") as enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(_confirm_payment(payment, "pay-original").status_code, 200)
            with self.captureOnCommitCallbacks(execute=True):
                duplicate_response = _confirm_payment(payment, "pay-duplicate")
            self.assertEqual(duplicate_response.status_code, 409)
            self.assertEqual(json.loads(duplicate_response.content)["status"], "duplicate_charge_refund_pending")
            duplicate = Payment.objects.get(transaction_id="pay-duplicate")
            self.assertEqual(duplicate.refund_reason, "duplicate_charge")
            self.assertEqual(duplicate.refund_status, "pending")
            with self.captureOnCommitCallbacks(execute=True):
                repeated = _confirm_payment(payment, "pay-duplicate")
            self.assertEqual(repeated.status_code, 409)
            enqueue.assert_called_once_with(duplicate.pk)
        booking.refresh_from_db(); payment.refresh_from_db()
        self.assertEqual(booking.status, Booking.CONFIRMED)
        self.assertEqual(payment.status, Payment.CAPTURED)
        self.assertEqual(booking.booking_seats.count(), 1)

    def test_database_rejects_screen_and_confirmation_mismatches(self):
        wrong_theater = Theater.objects.create(name="Wrong Theater")
        wrong_screen = Screen.objects.create(theater=wrong_theater, name="Wrong Screen")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Show.objects.filter(pk=self.show.pk).update(screen=wrong_screen)
        second_screen = Screen.objects.create(theater=self.theater, name="Screen 2")
        second_seat = Seat.objects.create(screen=second_screen, seat_number="Z1")
        with self.assertRaises(IntegrityError), transaction.atomic():
            ShowSeat.objects.filter(pk=self.show_seats[0].pk).update(seat=second_seat)
        pending = Booking.objects.create(user=self.user, movie=self.movie, theater=self.theater, show=self.show)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Booking.objects.filter(pk=pending.pk).update(status=Booking.CONFIRMED)

    def test_same_user_reservation_adds_seats_to_one_active_booking(self):
        self.client.force_login(self.user)
        url = reverse("reserve_seats", args=[self.show.id])
        first = self.client.post(url, {"seats": [self.seats[0].id]})
        booking = Booking.objects.get(reference=first.json()["booking"])
        stale_order = Payment.objects.create(
            booking=booking, amount=booking.total_amount,
            idempotency_key="one-seat-order", provider_order_id="order_one_seat",
        )
        # Existing seat prices are snapshots; only the newly added seat uses
        # the show's current price.
        self.show.price = Decimal("300.00")
        self.show.save(update_fields=["price"])
        second = self.client.post(url, {"seats": [self.seats[1].id]})
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(first.json()["booking"], second.json()["booking"])
        self.assertEqual(Booking.objects.filter(user=self.user, status=Booking.PENDING).count(), 1)
        booking.refresh_from_db()
        self.assertEqual(booking.booking_seats.count(), 2)
        self.assertEqual(set(booking.booking_seats.values_list("show_seat__seat_id", flat=True)), {self.seats[0].id, self.seats[1].id})
        self.assertEqual(booking.total_amount, sum(booking.booking_seats.values_list("price", flat=True), Decimal("0.00")))
        self.assertEqual(booking.total_amount, Decimal("550.00"))
        stale_order.refresh_from_db()
        self.assertEqual(stale_order.status, Payment.FAILED)
        # The stale one-seat provider order is not returned for the new total.
        with override_settings(RAZORPAY_KEY_ID="", RAZORPAY_KEY_SECRET=""):
            order_response = self.client.post(reverse("create_payment_order", args=[booking.reference]))
        self.assertEqual(order_response.status_code, 503)
        self.assertNotEqual(order_response.json()["payment"], stale_order.pk)
        current_payment = Payment.objects.get(pk=order_response.json()["payment"])
        self.assertEqual(current_payment.amount, Decimal("550.00"))
        self.assertEqual(current_payment.status, Payment.CREATED)

    def test_modify_reservation_adds_and_drops_seats_atomically(self):
        self.client.force_login(self.user)
        url = reverse("reserve_seats", args=[self.show.pk])
        initial = self.client.post(url, {"seats": [self.seats[0].pk, self.seats[1].pk]})
        self.assertEqual(initial.status_code, 201)
        booking = Booking.objects.get(reference=initial.json()["booking"])
        stale_order = Payment.objects.create(
            booking=booking, amount=booking.total_amount,
            idempotency_key="before-seat-change", provider_order_id="order_before_change",
        )
        self.show.price = Decimal("300.00")
        self.show.save(update_fields=["price"])
        modify_url = reverse("modify_reservation", args=[booking.reference])
        changed = self.client.post(modify_url, {"seats": [self.seats[1].pk, self.seats[2].pk]})
        self.assertEqual(changed.status_code, 200)
        booking.refresh_from_db()
        self.assertEqual(set(booking.booking_seats.values_list("show_seat__seat_id", flat=True)), {self.seats[1].pk, self.seats[2].pk})
        self.assertEqual(booking.total_amount, sum(booking.booking_seats.values_list("price", flat=True), Decimal("0.00")))
        self.assertEqual(booking.total_amount, Decimal("550.00"))
        stale_order.refresh_from_db()
        self.assertEqual(stale_order.status, Payment.FAILED)
        for row in self.show_seats:
            row.refresh_from_db()
        self.assertEqual(self.show_seats[0].status, ShowSeat.AVAILABLE)
        self.assertEqual(self.show_seats[1].active_booking_id, booking.pk)
        self.assertEqual(self.show_seats[2].active_booking_id, booking.pk)

    def test_modify_conflict_rolls_back_without_releasing_or_adding_seats(self):
        self.client.force_login(self.user)
        first = self.client.post(reverse("reserve_seats", args=[self.show.pk]), {"seats": [self.seats[0].pk]})
        self.assertEqual(first.status_code, 201)
        booking = Booking.objects.get(reference=first.json()["booking"])
        self.client.force_login(self.other)
        second = self.client.post(reverse("reserve_seats", args=[self.show.pk]), {"seats": [self.seats[1].pk]})
        self.assertEqual(second.status_code, 201)
        self.client.force_login(self.user)

        response = self.client.post(reverse("modify_reservation", args=[booking.reference]),
                                    {"seats": [self.seats[0].pk, self.seats[1].pk]})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(set(booking.booking_seats.values_list("show_seat__seat_id", flat=True)), {self.seats[0].pk})
        for row in self.show_seats:
            row.refresh_from_db()
        self.assertEqual(self.show_seats[0].active_booking_id, booking.pk)
        self.assertEqual(self.show_seats[1].active_booking_id, Booking.objects.get(reference=second.json()["booking"]).pk)
        self.assertEqual(self.show_seats[2].status, ShowSeat.AVAILABLE)

    def test_expired_reservation_cannot_be_modified_and_can_be_cleanly_rebooked(self):
        self.client.force_login(self.user)
        url = reverse("reserve_seats", args=[self.show.pk])
        initial = self.client.post(url, {"seats": [self.seats[0].pk, self.seats[1].pk]})
        self.assertEqual(initial.status_code, 201)
        previous = Booking.objects.get(reference=initial.json()["booking"])
        ShowSeat.objects.filter(active_booking=previous).update(hold_expires_at=timezone.now()-timedelta(seconds=1))
        modify = self.client.post(reverse("modify_reservation", args=[previous.reference]), {"seats": [self.seats[2].pk]})
        self.assertEqual(modify.status_code, 409)
        replacement = self.client.post(url, {"seats": [self.seats[2].pk]})
        self.assertEqual(replacement.status_code, 201)
        previous.refresh_from_db()
        self.assertEqual(previous.status, Booking.FAILED)
        self.assertFalse(previous.booking_seats.exists())
        self.assertEqual(Booking.objects.filter(user=self.user, status=Booking.PENDING).count(), 1)
        self.show_seats[0].refresh_from_db(); self.show_seats[1].refresh_from_db(); self.show_seats[2].refresh_from_db()
        self.assertEqual([row.status for row in self.show_seats], [ShowSeat.AVAILABLE, ShowSeat.AVAILABLE, ShowSeat.HELD])
        self.assertEqual(self.show_seats[2].active_booking_id, Booking.objects.get(reference=replacement.json()["booking"]).pk)

    def test_seat_availability_hides_other_owner_and_expires_stale_holds(self):
        booking = Booking.objects.create(user=self.user, movie=self.movie, theater=self.theater, show=self.show, status=Booking.PENDING)
        self.show_seats[0].status = ShowSeat.HELD
        self.show_seats[0].held_by = self.user
        self.show_seats[0].hold_expires_at = timezone.now()+timedelta(seconds=30)
        self.show_seats[0].active_booking = booking
        self.show_seats[0].save()
        self.client.force_login(self.other)
        payload = self.client.get(reverse("seat_availability", args=[self.show.id])).json()
        self.assertEqual(payload["seats"][0]["status"], "held")
        self.assertFalse(payload["seats"][0]["mine"])
        self.show_seats[0].hold_expires_at = timezone.now()-timedelta(seconds=1)
        self.show_seats[0].save()
        payload = self.client.get(reverse("seat_availability", args=[self.show.id])).json()
        self.assertEqual(payload["seats"][0]["status"], "available")

    def test_cleanup_task_releases_expired_holds_and_fails_pending_booking(self):
        booking = Booking.objects.create(user=self.user, movie=self.movie, theater=self.theater, show=self.show, status=Booking.PENDING)
        row = self.show_seats[0]
        row.status, row.held_by, row.hold_expires_at = ShowSeat.HELD, self.user, timezone.now()-timedelta(seconds=1)
        row.active_booking = booking
        row.save()
        BookingSeat.objects.create(booking=booking, show_seat=row, price=self.show.price)
        release_expired_holds.run()
        row.refresh_from_db(); booking.refresh_from_db()
        self.assertEqual(row.status, ShowSeat.AVAILABLE)
        self.assertEqual(booking.status, Booking.FAILED)

    @override_settings(RAZORPAY_KEY_SECRET="test_secret")
    def test_payment_callback_confirms_only_owned_unexpired_holds_and_is_idempotent(self):
        booking = Booking.objects.create(user=self.user, movie=self.movie, theater=self.theater, show=self.show, status=Booking.PENDING, total_amount=Decimal("250.00"))
        self.show_seats[0].status = ShowSeat.HELD
        self.show_seats[0].held_by = self.user
        self.show_seats[0].hold_expires_at = timezone.now()+timedelta(minutes=1)
        self.show_seats[0].active_booking = booking
        self.show_seats[0].save()
        BookingSeat.objects.create(booking=booking, show_seat=self.show_seats[0], price=self.show.price)
        payment = Payment.objects.create(booking=booking, amount=booking.total_amount, idempotency_key="payment-test-1", provider_order_id="order_1")
        first = _confirm_payment(payment, "pay_1")
        booking.refresh_from_db(); self.show_seats[0].refresh_from_db(); payment.refresh_from_db()
        self.assertEqual(booking.status, Booking.CONFIRMED)
        self.assertEqual(self.show_seats[0].status, ShowSeat.BOOKED)
        self.assertEqual(payment.status, Payment.CAPTURED)
        second = _confirm_payment(payment, "pay_1")
        self.assertEqual(second.status_code, 200)
        self.assertEqual(Booking.objects.filter(pk=booking.pk).count(), 1)

    def test_ticket_dispatch_is_registered_after_commit_without_inline_pdf_or_email(self):
        booking = Booking.objects.create(
            user=self.user, movie=self.movie, theater=self.theater, show=self.show,
            status=Booking.PENDING, total_amount=Decimal("250.00"),
        )
        row = self.show_seats[0]
        row.status, row.held_by = ShowSeat.HELD, self.user
        row.hold_expires_at = timezone.now() + timedelta(minutes=1)
        row.active_booking = booking
        row.save()
        BookingSeat.objects.create(booking=booking, show_seat=row, price=self.show.price)
        payment = Payment.objects.create(
            booking=booking, amount=booking.total_amount,
            idempotency_key="ticket-after-commit", provider_order_id="order-after-commit",
        )

        with patch("movies.views._enqueue_ticket") as enqueue, \
                patch("movies.tasks._render_pdf") as render_pdf, \
                patch("django.core.mail.EmailMessage.send") as send_email:
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                response = _confirm_payment(payment, "pay-after-commit")
                self.assertEqual(response.status_code, 200)
                enqueue.assert_not_called()
                render_pdf.assert_not_called()
                send_email.assert_not_called()
            self.assertEqual(len(callbacks), 1)
            callbacks[0]()
            enqueue.assert_called_once_with(booking.pk)
            render_pdf.assert_not_called()
            send_email.assert_not_called()

    def test_review_requires_captured_paid_past_show_and_rating_aggregate_updates(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("submit_review", args=[self.movie.id]), {"rating": 5, "body": "Great"})
        self.assertEqual(response.status_code, 403)
        past = Show.objects.create(theater=self.theater, screen=self.screen, movie=self.movie, starts_at=timezone.now()-timedelta(days=1), price=100)
        booking = self.create_confirmed_booking(show=past, amount=100)
        response = self.client.post(reverse("submit_review", args=[self.movie.id]), {"rating": 5, "body": "Great"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Review.objects.get(movie=self.movie, user=self.user).verified_viewer)
        other_booking = self.create_confirmed_booking(user=self.other, show=past, amount=100)
        Review.objects.create(movie=self.movie, user=self.other, rating=3, body="Okay")
        self.movie.refresh_from_db()
        self.assertEqual(self.movie.review_count, 2)
        self.assertEqual(float(self.movie.rating), 4.0)
        review = Review.objects.get(movie=self.movie, user=self.other)
        review.is_hidden = True; review.save()
        self.movie.refresh_from_db()
        self.assertEqual(self.movie.review_count, 1)
        self.assertEqual(float(self.movie.rating), 5.0)

    def test_review_eligibility_rejects_future_and_nonconfirmed_booking_states(self):
        from movies.models import eligible_viewer
        future = Show.objects.create(theater=self.theater, screen=self.screen, movie=self.movie,
                                     starts_at=timezone.now()+timedelta(days=2), price=100)
        self.create_confirmed_booking(user=self.user, show=future, amount=100)
        self.assertFalse(eligible_viewer(self.user.pk, self.movie.pk))
        self.assertEqual(self.client.post(reverse("submit_review", args=[self.movie.pk]), {"rating": 5, "body": "Anonymous"}).status_code, 302)
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(reverse("submit_review", args=[self.movie.pk]), {"rating": 5, "body": "Too soon"}).status_code, 403)

        past = Show.objects.create(theater=self.theater, screen=self.screen, movie=self.movie,
                                   starts_at=timezone.now()-timedelta(days=1), price=100)
        for index, status in enumerate((Booking.PENDING, Booking.FAILED, Booking.CANCELLED, Booking.REFUNDED)):
            user = User.objects.create_user(f"ineligible-{index}", password="StrongPass!234")
            booking = Booking.objects.create(user=user, movie=self.movie, theater=self.theater, show=past,
                                             status=status, total_amount=Decimal("100.00"))
            Payment.objects.create(booking=booking, amount=booking.total_amount, status=Payment.CAPTURED,
                                   transaction_id=f"ineligible-pay-{index}", idempotency_key=f"ineligible-key-{index}")
            self.assertFalse(eligible_viewer(user.pk, self.movie.pk), status)
            review = Review.objects.create(movie=self.movie, user=user, rating=5, body="Legacy ineligible review")
            self.assertFalse(review.verified_viewer)

        self.create_confirmed_booking(user=self.user, show=past, amount=100)
        self.assertTrue(eligible_viewer(self.user.pk, self.movie.pk))
        response = self.client.post(reverse("submit_review", args=[self.movie.pk]), {"rating": 4, "body": "Completed show"})
        self.assertEqual(response.status_code, 302)
        review = Review.objects.get(movie=self.movie, user=self.user)
        self.assertTrue(review.verified_viewer)
        self.movie.refresh_from_db()
        self.assertEqual(self.movie.review_count, 1)
        self.assertEqual(self.movie.rating, Decimal("4.0"))
        self.assertEqual(self.movie.average_rating, Decimal("4.0"))

    def test_review_edits_and_deletes_keep_eligible_visible_aggregate(self):
        past = Show.objects.create(theater=self.theater, screen=self.screen, movie=self.movie,
                                   starts_at=timezone.now()-timedelta(days=1), price=100)
        self.create_confirmed_booking(user=self.user, show=past, amount=100)
        self.create_confirmed_booking(user=self.other, show=past, amount=100)
        review = Review.objects.create(movie=self.movie, user=self.user, rating=3, body="Original")
        other_review = Review.objects.create(movie=self.movie, user=self.other, rating=5, body="Other")
        # A legacy/ineligible visible row is deliberately present to ensure the
        # request path never replaces the eligibility-aware signal aggregate.
        unqualified = User.objects.create_user("unqualified-reviewer", password="StrongPass!234")
        Review.objects.create(movie=self.movie, user=unqualified, rating=1, body="Not eligible")
        self.movie.refresh_from_db()
        self.assertEqual(self.movie.review_count, 2)
        self.assertEqual(self.movie.rating, Decimal("4.0"))

        self.client.force_login(self.user)
        response = self.client.post(reverse("edit_review", args=[review.pk]), {"rating": 4, "body": "Edited"})
        self.assertEqual(response.status_code, 200)
        self.movie.refresh_from_db()
        self.assertEqual(self.movie.review_count, 2)
        self.assertEqual(self.movie.rating, Decimal("4.5"))

        other_review.delete()
        self.movie.refresh_from_db()
        self.assertEqual(self.movie.review_count, 1)
        self.assertEqual(self.movie.rating, Decimal("4.0"))

    def test_review_authorization_and_report_uniqueness(self):
        review = Review.objects.create(movie=self.movie, user=self.user, rating=4, body="Review")
        self.client.force_login(self.other)
        response = self.client.post(reverse("edit_review", args=[review.id]), {"rating": 1, "body": "hacked"})
        self.assertEqual(response.status_code, 403)
        response = self.client.post(reverse("report_review", args=[review.id]), {"reason": "abuse"})
        self.assertEqual(response.status_code, 201)
        response = self.client.post(reverse("report_review", args=[review.id]), {"reason": "duplicate"})
        self.assertEqual(response.status_code, 409)
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(reverse("report_review", args=[review.id]), {"reason": "self report"}).status_code, 403)
        past = Show.objects.create(theater=self.theater, screen=self.screen, movie=self.movie, starts_at=timezone.now()-timedelta(days=1), price=100)
        self.create_confirmed_booking(show=past, amount=100)
        self.assertEqual(self.client.post(reverse("edit_review", args=[review.id]), {"rating": 4, "body": ""}).status_code, 400)

    def test_admin_can_hide_review_and_hidden_review_is_not_public(self):
        review = Review.objects.create(movie=self.movie, user=self.user, rating=1, body="Needs moderation")
        admin_user = User.objects.create_superuser("review-admin", "review-admin@example.test", "StrongPass!234")
        self.client.force_login(admin_user)
        url = reverse("admin:movies_review_change", args=[review.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.post(url, {"movie": self.movie.pk, "user": self.user.pk, "rating": 1, "body": "Needs moderation", "is_hidden": "on", "_save": "Save"}, follow=True)
        review.refresh_from_db()
        self.assertTrue(review.is_hidden)
        detail = self.client.get(reverse("movie_detail", args=[self.movie.pk]))
        self.assertNotContains(detail, "Needs moderation")

    def test_second_review_is_rejected_instead_of_overwriting_first(self):
        from datetime import timedelta
        past = Show.objects.create(theater=self.theater, screen=self.screen, movie=self.movie, starts_at=timezone.now()-timedelta(days=1), price=100)
        booking = self.create_confirmed_booking(show=past, amount=100)
        self.client.force_login(self.user)
        url = reverse("submit_review", args=[self.movie.id])
        self.assertEqual(self.client.post(url, {"rating": 4, "body": "First"}).status_code, 302)
        response = self.client.post(url, {"rating": 2, "body": "Second"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(Review.objects.get(movie=self.movie, user=self.user).body, "First")

    @override_settings(RAZORPAY_WEBHOOK_SECRET="webhook-secret")
    def test_webhook_hmac_and_event_replay_are_handled(self):
        booking = Booking.objects.create(user=self.user, movie=self.movie, theater=self.theater, show=self.show, status=Booking.PENDING, total_amount=Decimal("250.00"))
        self.show_seats[0].status = ShowSeat.HELD
        self.show_seats[0].held_by = self.user
        self.show_seats[0].hold_expires_at = timezone.now()+timedelta(minutes=1)
        self.show_seats[0].active_booking = booking
        self.show_seats[0].save()
        BookingSeat.objects.create(booking=booking, show_seat=self.show_seats[0], price=self.show.price)
        Payment.objects.create(booking=booking, amount=booking.total_amount, idempotency_key="webhook-payment-1", provider_order_id="order_webhook")
        raw = json.dumps({"event": "payment.captured", "payload": {"payment": {"entity": {"order_id": "order_webhook", "id": "pay_webhook", "status": "captured", "currency": "INR", "amount": 25000}}}}).encode()
        signature = hmac.new(b"webhook-secret", raw, hashlib.sha256).hexdigest()
        url = reverse("razorpay_webhook")
        first = self.client.post(url, raw, content_type="application/json", HTTP_X_RAZORPAY_SIGNATURE=signature, HTTP_X_RAZORPAY_EVENT_ID="evt-1")
        replay = self.client.post(url, raw, content_type="application/json", HTTP_X_RAZORPAY_SIGNATURE=signature, HTTP_X_RAZORPAY_EVENT_ID="evt-1")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(replay.json()["status"], "duplicate")
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.CONFIRMED)
        event = PaymentWebhookEvent.objects.get(event_id="evt-1")
        self.assertEqual(event.status, "processed")
        self.assertEqual(event.attempts, 1)

    @override_settings(RAZORPAY_WEBHOOK_SECRET="webhook-secret")
    def test_failed_webhook_processing_is_recoverable_on_provider_retry(self):
        booking = Booking.objects.create(user=self.user, movie=self.movie, theater=self.theater, show=self.show, status=Booking.PENDING, total_amount=Decimal("250.00"))
        row = self.show_seats[0]
        row.status, row.held_by, row.hold_expires_at, row.active_booking = ShowSeat.HELD, self.user, timezone.now()+timedelta(minutes=1), booking
        row.save(); BookingSeat.objects.create(booking=booking, show_seat=row, price=self.show.price)
        Payment.objects.create(booking=booking, amount=booking.total_amount, idempotency_key="webhook-retry", provider_order_id="order_retry")
        data = {"event": "payment.captured", "payload": {"payment": {"entity": {"order_id": "order_retry", "id": "pay_retry", "status": "captured", "currency": "INR", "amount": 25000}}}}
        raw = json.dumps(data).encode(); signature = hmac.new(b"webhook-secret", raw, hashlib.sha256).hexdigest()
        url = reverse("razorpay_webhook")
        with patch("movies.views._process_payment_webhook", side_effect=RuntimeError("temporary")):
            failed = self.client.post(url, raw, content_type="application/json", HTTP_X_RAZORPAY_SIGNATURE=signature, HTTP_X_RAZORPAY_EVENT_ID="evt-retry")
        self.assertEqual(failed.status_code, 500)
        event = PaymentWebhookEvent.objects.get(event_id="evt-retry")
        self.assertEqual(event.status, "failed"); self.assertEqual(event.attempts, 1)
        succeeded = self.client.post(url, raw, content_type="application/json", HTTP_X_RAZORPAY_SIGNATURE=signature, HTTP_X_RAZORPAY_EVENT_ID="evt-retry")
        event.refresh_from_db(); booking.refresh_from_db()
        self.assertEqual(succeeded.status_code, 200)
        self.assertEqual(event.status, "processed"); self.assertEqual(event.attempts, 2)
        self.assertEqual(booking.status, Booking.CONFIRMED)

    def test_paid_cancellation_waits_for_verified_refund_and_releases_seats(self):
        booking = self.create_confirmed_booking()
        row = booking.active_show_seats.first()
        payment = booking.payments.get(status=Payment.CAPTURED)
        self.client.force_login(self.user)
        with patch("movies.views._enqueue_refund") as enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(reverse("cancel_booking", args=[booking.reference]), HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 202); self.assertEqual(response.json()["status"], "refund_pending")
        enqueue.assert_called_once_with(payment.pk)
        booking.refresh_from_db(); row.refresh_from_db(); payment.refresh_from_db()
        self.assertEqual(booking.status, Booking.CANCELLATION_PENDING)
        self.assertEqual(row.status, ShowSeat.BOOKED)
        self.assertEqual(payment.status, Payment.CAPTURED)
        payment.refresh_from_db(); payment.refund_status = "pending"; payment.refund_id = "rfnd_1"; payment.save()
        _process_payment_webhook({"event": "refund.processed", "payload": {"refund": {"entity": {"id": "rfnd_1", "payment_id": payment.transaction_id, "amount": 25000, "status": "processed"}}}})
        booking.refresh_from_db(); row.refresh_from_db(); payment.refresh_from_db()
        self.assertEqual(booking.status, Booking.REFUNDED)
        self.assertEqual(row.status, ShowSeat.AVAILABLE)
        self.assertEqual(row.active_booking_id, None)
        self.assertEqual(payment.status, Payment.REFUNDED)

    @override_settings(RAZORPAY_WEBHOOK_SECRET="webhook-secret")
    def test_refund_webhook_replay_is_idempotent(self):
        booking = self.create_confirmed_booking()
        payment = booking.payments.get(status=Payment.CAPTURED)
        payment.refund_status = "pending"
        payment.refund_id = "rfnd_replay"
        payment.save(update_fields=["refund_status", "refund_id", "updated_at"])
        payload = {"event": "refund.processed", "payload": {"refund": {"entity": {
            "id": "rfnd_replay", "payment_id": payment.transaction_id,
            "amount": 25000, "status": "processed",
        }}}}
        raw = json.dumps(payload).encode()
        signature = hmac.new(b"webhook-secret", raw, hashlib.sha256).hexdigest()
        url = reverse("razorpay_webhook")
        headers = {"content_type": "application/json", "HTTP_X_RAZORPAY_SIGNATURE": signature,
                   "HTTP_X_RAZORPAY_EVENT_ID": "evt-refund-replay"}
        first = self.client.post(url, raw, **headers)
        replay = self.client.post(url, raw, **headers)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(replay.json()["status"], "duplicate")
        payment.refresh_from_db(); booking.refresh_from_db()
        self.assertEqual(payment.status, Payment.REFUNDED)
        self.assertEqual(booking.status, Booking.REFUNDED)
        self.assertEqual(booking.booking_seats.select_related("show_seat").get().show_seat.status, ShowSeat.AVAILABLE)
        self.assertEqual(PaymentWebhookEvent.objects.get(event_id="evt-refund-replay").attempts, 1)

    def test_delayed_payment_failure_and_refund_events_cannot_downgrade_final_states(self):
        booking = self.create_confirmed_booking()
        payment = booking.payments.get(status=Payment.CAPTURED)
        payment.provider_order_id = "order-delayed-failure"
        payment.refund_id = "rfnd-final"
        payment.refund_status = "pending"
        payment.save(update_fields=["provider_order_id", "refund_id", "refund_status", "updated_at"])
        row = booking.active_show_seats.get()

        _process_payment_webhook({"event": "payment.failed", "payload": {"payment": {"entity": {
            "order_id": payment.provider_order_id, "id": "pay-late-failure", "status": "failed",
            "currency": "INR", "amount": 25000,
        }}}})
        booking.refresh_from_db(); payment.refresh_from_db(); row.refresh_from_db()
        self.assertEqual(booking.status, Booking.CONFIRMED)
        self.assertEqual(payment.status, Payment.CAPTURED)
        self.assertEqual(row.status, ShowSeat.BOOKED)

        _process_payment_webhook({"event": "refund.processed", "payload": {"refund": {"entity": {
            "id": "rfnd-final", "payment_id": payment.transaction_id, "amount": 25000, "status": "processed",
        }}}})
        payment.refresh_from_db(); booking.refresh_from_db()
        self.assertEqual(payment.status, Payment.REFUNDED)
        self.assertEqual(payment.refund_status, "processed")
        for event_name, status in (("refund.created", "created"), ("refund.failed", "failed")):
            _process_payment_webhook({"event": event_name, "payload": {"refund": {"entity": {
                "id": "rfnd-final", "payment_id": payment.transaction_id, "amount": 25000, "status": status,
            }}}})
            payment.refresh_from_db(); booking.refresh_from_db()
            self.assertEqual(payment.status, Payment.REFUNDED)
            self.assertEqual(payment.refund_status, "processed")
            self.assertEqual(booking.status, Booking.REFUNDED)

    @override_settings(RAZORPAY_KEY_ID="test-key", RAZORPAY_KEY_SECRET="test-secret")
    def test_refund_task_records_pending_without_duplicate_provider_refund(self):
        booking = Booking.objects.create(user=self.user, movie=self.movie, theater=self.theater, show=self.show, status=Booking.CANCELLATION_PENDING, total_amount=Decimal("250.00"))
        payment = Payment.objects.create(booking=booking, amount=booking.total_amount, status=Payment.CAPTURED, transaction_id="pay_pending_refund", refund_status="requested", refund_reason="customer_cancellation", idempotency_key="refund-pending")
        client = patch("razorpay.Client").start()
        self.addCleanup(patch.stopall)
        client.return_value.payment.refund.return_value = {"id": "rfnd_pending", "status": "pending"}
        self.assertEqual(refund_late_payment.run(payment.pk), "awaiting_provider_confirmation")
        self.assertEqual(refund_late_payment.run(payment.pk), "awaiting_provider_confirmation")
        self.assertEqual(client.return_value.payment.refund.call_count, 1)
        payment.refresh_from_db(); booking.refresh_from_db()
        self.assertEqual(payment.status, Payment.CAPTURED)
        self.assertEqual(payment.refund_status, "pending")
        self.assertEqual(booking.status, Booking.CANCELLATION_PENDING)

    @override_settings(RAZORPAY_KEY_ID="test-key", RAZORPAY_KEY_SECRET="test-secret")
    def test_failed_refund_is_never_reported_as_successful(self):
        booking = Booking.objects.create(user=self.user, movie=self.movie, theater=self.theater, show=self.show, status=Booking.CANCELLATION_PENDING, total_amount=Decimal("250.00"))
        payment = Payment.objects.create(booking=booking, amount=booking.total_amount, status=Payment.CAPTURED, transaction_id="pay_failed_refund", refund_status="requested", idempotency_key="refund-failed")
        with patch("razorpay.Client") as client:
            client.return_value.payment.refund.return_value = {"id": "rfnd_failed", "status": "failed"}
            result = refund_late_payment.run(payment.pk)
        payment.refresh_from_db(); booking.refresh_from_db()
        self.assertEqual(result, "provider_refund_failed")
        self.assertEqual(payment.status, Payment.CAPTURED)
        self.assertEqual(payment.refund_status, "failed")
        self.assertEqual(booking.status, Booking.CANCELLATION_PENDING)

    def test_discovery_combined_filters_sort_counts_and_pagination(self):
        self.movie.rating = Decimal("4.5"); self.movie.release_date = timezone.localdate()-timedelta(days=5); self.movie.save()
        next_movie = Movie.objects.create(name="A New Film", rating=Decimal("4.8"), release_date=timezone.localdate(), duration_minutes=100)
        next_movie.genres.add(self.genre); next_movie.languages.add(self.language)
        Show.objects.create(theater=self.theater, screen=self.screen, movie=next_movie, starts_at=timezone.now()+timedelta(days=1), price=Decimal("150"))
        url = reverse("movie_list")
        params = {"genre": self.genre.pk, "language": self.language.pk, "city": self.city.slug, "theater": self.theater.pk, "release_from": (timezone.localdate()-timedelta(days=7)).isoformat(), "release_to": timezone.localdate().isoformat(), "min_rating": "4", "show_date": (timezone.localdate()+timedelta(days=1)).isoformat(), "sort": "release"}
        response = self.client.get(url, params)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["matching_count"], 2)
        self.assertEqual([movie.pk for movie in response.context["movies"]], [next_movie.pk, self.movie.pk])
        self.assertContains(response, "2 matching movies")
        self.assertContains(response, "name=\"theater\"")

    def test_discovery_sorting_popularity_price_time_and_filter_validation(self):
        self.movie.rating = Decimal("3.0"); self.movie.release_date = timezone.localdate()-timedelta(days=100); self.movie.save()
        cheaper = Movie.objects.create(name="Cheaper", rating=Decimal("4.7"), release_date=timezone.localdate())
        Show.objects.create(theater=self.theater, screen=self.screen, movie=cheaper, starts_at=timezone.now()+timedelta(days=1), price=Decimal("100"))
        self.create_confirmed_booking()
        url = reverse("movie_list")
        by_price = self.client.get(url, {"sort": "price"})
        self.assertEqual(by_price.context["movies"][0], cheaper)
        self.assertEqual(self.client.get(url, {"sort": "price_desc"}).context["movies"][0], self.movie)
        by_popularity = self.client.get(url, {"sort": "popular"})
        self.assertEqual(by_popularity.context["movies"][0], self.movie)
        show_date = (timezone.localdate()+timedelta(days=1)).isoformat()
        after_time = (timezone.localtime(self.show.starts_at) - timedelta(minutes=1)).strftime("%H:%M")
        by_time = self.client.get(url, {"show_date": show_date, "show_time": after_time})
        self.assertEqual(by_time.context["matching_count"], 2)
        self.assertEqual(self.client.get(url, {"min_rating": "5.1"}).status_code, 400)
        self.assertEqual(self.client.get(url, {"genre": "not-a-number"}).status_code, 400)
        for invalid in ({"release_from": "not-a-date"}, {"release_to": "2025-99-99"},
                        {"show_date": "invalid"}, {"show_time": "25:61"},
                        {"release_from": "2025-02-02", "release_to": "2025-02-01"}):
            with self.subTest(filters=invalid):
                self.assertEqual(self.client.get(url, invalid).status_code, 400)

    def test_discovery_multiselect_time_window_ajax_and_server_rendered_parity(self):
        second_genre = Genre.objects.create(name="Thriller")
        second_language = Language.objects.create(name="Hindi")
        self.movie.genres.add(second_genre)
        self.movie.languages.add(second_language)
        tomorrow = timezone.localdate() + timedelta(days=1)
        first_show_time = timezone.make_aware(
            datetime.combine(tomorrow, day_time(19, 0)), timezone.get_current_timezone()
        )
        self.show.starts_at = first_show_time
        self.show.save(update_fields=["starts_at"])

        second_movie = Movie.objects.create(name="Second Filter Match")
        second_movie.genres.add(second_genre)
        second_movie.languages.add(second_language)
        second_show = Show.objects.create(
            theater=self.theater, screen=self.screen, movie=second_movie,
            starts_at=first_show_time + timedelta(hours=1), price=Decimal("200.00"),
        )
        ShowSeat.objects.get_or_create(show=second_show, seat=self.seats[0])

        params = {
            "genre": [str(self.genre.pk), str(second_genre.pk)],
            "language": [str(self.language.pk), str(second_language.pk)],
            "show_date": tomorrow.isoformat(),
            "show_time_from": "18:00",
            "show_time_to": "20:00",
        }
        normal = self.client.get(reverse("movie_list"), params)
        self.assertEqual(normal.status_code, 200)
        expected_ids = {self.movie.pk, second_movie.pk}
        self.assertEqual({movie.pk for movie in normal.context["movies"]}, expected_ids)
        self.assertEqual(normal.context["matching_count"], 2)
        self.assertEqual(self.client.get(reverse("movie_list"), {**params, "show_time_from": "21:00", "show_time_to": "19:00"}).status_code, 400)

        ajax = self.client.get(
            reverse("movie_list"), params,
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(ajax.status_code, 200)
        payload = ajax.json()
        self.assertEqual(payload["matching_count"], normal.context["matching_count"])
        self.assertEqual(payload["page"], normal.context["page_obj"].number)
        self.assertEqual(
            {int(value) for value in re.findall(r'data-movie-id="(\d+)"', payload["html"])},
            expected_ids,
        )
        invalid_ajax = self.client.get(
            reverse("movie_list"), {"genre": "bad"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(invalid_ajax.status_code, 400)
        self.assertIn("error", invalid_ajax.json())

    def test_discovery_pagination_preserves_multiselect_filters_and_handles_bad_pages(self):
        movies = [self.movie]
        for index in range(12):
            movie = Movie.objects.create(name=f"Filtered page movie {index:02d}")
            movie.genres.add(self.genre)
            movie.languages.add(self.language)
            movies.append(movie)
        params = {
            "genre": [str(self.genre.pk)],
            "language": [str(self.language.pk)],
            "sort": "unrecognized-sort",
        }
        first = self.client.get(reverse("movie_list"), params)
        self.assertEqual(first.context["matching_count"], 13)
        self.assertEqual(first.context["page_obj"].number, 1)
        self.assertEqual(len(first.context["movies"]), 12)
        self.assertIn(self.movie, first.context["movies"])
        self.assertContains(first, f"genre={self.genre.pk}")
        self.assertContains(first, f"language={self.language.pk}")
        second = self.client.get(reverse("movie_list"), {**params, "page": "2"})
        self.assertEqual(second.context["page_obj"].number, 2)
        self.assertEqual(len(second.context["movies"]), 1)
        self.assertEqual(second.context["movies"][0], movies[-1])
        invalid = self.client.get(reverse("movie_list"), {**params, "page": "not-a-page"})
        self.assertEqual(invalid.context["page_obj"].number, 1)
        out_of_range = self.client.get(reverse("movie_list"), {**params, "page": "999"})
        self.assertEqual(out_of_range.context["page_obj"].number, 2)
        empty = self.client.get(reverse("movie_list"), {"search": "no matching movie"})
        self.assertEqual(empty.context["matching_count"], 0)
        self.assertContains(empty, "No movies found.")

    def test_discovery_and_detail_query_counts_are_bounded(self):
        with CaptureQueriesContext(connection) as list_queries:
            response = self.client.get(reverse("movie_list"), {"search": "A Film"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["matching_count"], 1)
        self.assertEqual(response.context["movies"][0], self.movie)
        self.assertLessEqual(len(list_queries), 10)
        with CaptureQueriesContext(connection) as detail_queries:
            self.client.get(reverse("movie_detail", args=[self.movie.pk]))
        self.assertLessEqual(len(detail_queries), 18)

    def test_recent_viewed_movies_personalize_and_trending_is_fallback(self):
        adjacent = Movie.objects.create(name="Genre Match", rating=Decimal("4.2"))
        adjacent.genres.add(self.genre)
        Show.objects.create(
            theater=self.theater, screen=self.screen, movie=adjacent,
            starts_at=timezone.now() + timedelta(days=2), price=Decimal("180.00"),
        )
        self.client.force_login(self.user)
        self.client.get(reverse("movie_detail", args=[self.movie.pk]))
        view = RecentlyViewedMovie.objects.get(user=self.user, movie=self.movie)
        self.assertEqual(view.views, 1)
        response = self.client.get(reverse("movie_detail", args=[self.movie.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertIn(adjacent, list(response.context["personalized"]))
        comedy = Genre.objects.create(name="Comedy")
        other_history = Movie.objects.create(name="Comedy History")
        comedy_match = Movie.objects.create(name="Comedy Match")
        other_history.genres.add(comedy); comedy_match.genres.add(comedy)
        Show.objects.create(
            theater=self.theater, screen=self.screen, movie=comedy_match,
            starts_at=timezone.now() + timedelta(days=2), price=Decimal("180.00"),
        )
        self.client.force_login(self.other)
        response = self.client.get(reverse("movie_detail", args=[other_history.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertIn(comedy_match, list(response.context["personalized"]))
        self.assertNotIn(adjacent, list(response.context["personalized"]))
        no_genres = Movie.objects.create(name="Unclassified Film")
        booked_viewer = User.objects.create_user("booked-viewer", "booked@example.test", "StrongPass!234")
        self.create_confirmed_booking(user=booked_viewer)
        self.client.force_login(booked_viewer)
        response = self.client.get(reverse("movie_detail", args=[no_genres.pk]))
        self.assertIn(adjacent, list(response.context["personalized"]))
        no_history = User.objects.create_user("new-viewer", "new@example.test", "StrongPass!234")
        self.client.force_login(no_history)
        response = self.client.get(reverse("movie_detail", args=[no_genres.pk]))
        self.assertTrue(RecentlyViewedMovie.objects.filter(user=no_history, movie=no_genres).exists())
        self.assertIn(self.movie, list(response.context["personalized"]))

    def test_recommendations_apply_documented_overlap_weights_and_eligibility(self):
        second_genre = Genre.objects.create(name="Mystery")
        second_language = Language.objects.create(name="French")
        self.movie.genres.add(second_genre)
        self.movie.languages.add(second_language)
        history_movie = Movie.objects.create(name="History", duration_minutes=100)
        history_movie.genres.add(self.genre, second_genre)
        history_movie.languages.add(self.language, second_language)
        self.client.force_login(self.user)
        self.client.get(reverse("movie_detail", args=[history_movie.pk]))

        genre_and_language = Movie.objects.create(name="A Genre And Language")
        genre_and_language.genres.add(self.genre)
        genre_and_language.languages.add(self.language)
        two_genres = Movie.objects.create(name="B Two Genres")
        two_genres.genres.add(self.genre, second_genre)
        one_genre = Movie.objects.create(name="C One Genre")
        one_genre.genres.add(self.genre)
        one_language = Movie.objects.create(name="D One Language")
        one_language.languages.add(self.language)
        no_upcoming_show = Movie.objects.create(name="No Eligible Show")
        no_upcoming_show.genres.add(self.genre, second_genre)

        for movie in (genre_and_language, two_genres, one_genre, one_language):
            Show.objects.create(
                theater=self.theater, screen=self.screen, movie=movie,
                starts_at=timezone.now() + timedelta(days=2), price=Decimal("180.00"),
            )
        booked = Movie.objects.create(name="Already Booked")
        booked.genres.add(self.genre, second_genre)
        booked_show = Show.objects.create(
            theater=self.theater, screen=self.screen, movie=booked,
            starts_at=timezone.now() + timedelta(days=2), price=Decimal("180.00"),
        )
        self.create_confirmed_booking(user=self.user, show=booked_show)

        response = self.client.get(reverse("movie_detail", args=[self.movie.pk]))
        recommendations = list(response.context["personalized"])
        self.assertEqual(
            recommendations,
            [two_genres, genre_and_language, one_genre, one_language],
        )
        self.assertNotIn(booked, recommendations)
        self.assertNotIn(no_upcoming_show, recommendations)

    def test_recently_viewed_history_is_capped_at_thirty_movies(self):
        old_movies = [Movie.objects.create(name=f"Old view {index}") for index in range(31)]
        RecentlyViewedMovie.objects.bulk_create([
            RecentlyViewedMovie(user=self.user, movie=movie) for movie in old_movies
        ])
        newest = Movie.objects.create(name="Newest view")
        self.client.force_login(self.user)
        self.client.get(reverse("movie_detail", args=[newest.pk]))
        self.assertEqual(RecentlyViewedMovie.objects.filter(user=self.user).count(), 30)
        self.assertTrue(RecentlyViewedMovie.objects.filter(user=self.user, movie=newest).exists())

    def test_recommendation_ties_have_stable_order(self):
        first = Movie.objects.create(name="A Genre Match", rating=Decimal("4.0"), release_date=timezone.localdate())
        second = Movie.objects.create(name="B Genre Match", rating=Decimal("4.0"), release_date=timezone.localdate())
        first.genres.add(self.genre); second.genres.add(self.genre)
        response = self.client.get(reverse("movie_detail", args=[self.movie.pk]))
        self.assertEqual(list(response.context["similar"]), [first, second])
        self.assertEqual(list(response.context["recent"]), [first, second])

        for movie in (first, second):
            show = Show.objects.create(theater=self.theater, screen=self.screen, movie=movie,
                                       starts_at=timezone.now() + timedelta(days=2), price=Decimal("150.00"))
            self.create_confirmed_booking(show=show)
        response = self.client.get(reverse("movie_detail", args=[self.movie.pk]))
        self.assertEqual(list(response.context["trending"]), [first, second])

    def test_failed_refund_retry_is_available_in_booking_history(self):
        booking = self.create_confirmed_booking()
        payment = booking.payments.get(status=Payment.CAPTURED)
        booking.status = Booking.CANCELLATION_PENDING
        booking.save(update_fields=["status"])
        payment.refund_status = "failed"
        payment.save(update_fields=["refund_status", "updated_at"])
        newer_pending_refund = Payment.objects.create(booking=booking, amount=booking.total_amount,
                                                       status=Payment.CAPTURED, transaction_id="pay-newer-pending",
                                                       refund_status="pending", idempotency_key="newer-pending-refund")
        self.client.force_login(self.user)
        history = self.client.get(reverse("profile"))
        self.assertEqual(history.status_code, 200)
        self.assertContains(history, "Retry refund request")
        with patch("movies.views._enqueue_refund") as enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(reverse("cancel_booking", args=[booking.reference]), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "refund request is being processed")
        enqueue.assert_called_once_with(payment.pk)
        payment.refresh_from_db(); booking.refresh_from_db()
        self.assertEqual(payment.refund_status, "failed")
        newer_pending_refund.refresh_from_db()
        self.assertEqual(newer_pending_refund.refund_status, "pending")
        self.assertEqual(booking.status, Booking.CANCELLATION_PENDING)

    @override_settings(RAZORPAY_KEY_ID="test-key", RAZORPAY_KEY_SECRET="test-secret")
    def test_explicitly_failed_refund_with_provider_id_can_be_retried(self):
        booking = Booking.objects.create(user=self.user, movie=self.movie, theater=self.theater, show=self.show,
                                         status=Booking.CANCELLATION_PENDING, total_amount=Decimal("250.00"))
        payment = Payment.objects.create(booking=booking, amount=booking.total_amount, status=Payment.CAPTURED,
                                         transaction_id="pay_retryable", refund_id="rfnd_failed_before",
                                         refund_status="failed", idempotency_key="failed-refund-retry")
        with patch("razorpay.Client") as client:
            client.return_value.payment.refund.return_value = {"id": "rfnd_retry", "status": "pending"}
            self.assertEqual(refund_late_payment.run(payment.pk), "awaiting_provider_confirmation")
            client.return_value.payment.refund.assert_called_once_with(
                "pay_retryable", {"amount": 25000})
        payment.refresh_from_db()
        self.assertEqual(payment.refund_id, "rfnd_retry")
        self.assertEqual(payment.refund_status, "pending")

    def test_unpaid_cancellation_form_redirects_with_message_and_releases_seat(self):
        self.client.force_login(self.user)
        reserved = self.client.post(reverse("reserve_seats", args=[self.show.pk]), {"seats": [self.seats[0].pk]})
        booking = Booking.objects.get(reference=reserved.json()["booking"])
        response = self.client.post(reverse("cancel_booking", args=[booking.reference]), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "unpaid reservation was cancelled")
        booking.refresh_from_db(); self.show_seats[0].refresh_from_db()
        self.assertEqual(booking.status, Booking.CANCELLED)
        self.assertEqual(self.show_seats[0].status, ShowSeat.AVAILABLE)

    def test_analytics_permission_metrics_date_filter_and_streaming_csv_safety(self):
        permission = Permission.objects.get(codename="view_booking_analytics")
        paid = self.create_confirmed_booking()
        self.assertEqual(self.client.get(reverse("analytics_dashboard")).status_code, 302)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("analytics_dashboard")).status_code, 403)
        self.assertEqual(self.client.get(reverse("export_analytics_csv")).status_code, 403)
        self.other.user_permissions.add(permission)
        self.assertEqual(self.client.get(reverse("analytics_dashboard")).status_code, 403)
        self.assertEqual(self.client.get(reverse("export_bookings")).status_code, 403)
        self.assertEqual(self.client.get(reverse("export_analytics_csv")).status_code, 403)
        staff = User.objects.create_user("analytics", "analytics@example.test", "StrongPass!234", is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(reverse("export_bookings")).status_code, 403)
        staff.user_permissions.add(permission)
        start = timezone.localdate().isoformat(); url = reverse("analytics_dashboard")
        response = self.client.get(url, {"start": start, "end": start})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["summary"]["gross_revenue"], Decimal("250.00"))
        self.assertEqual(self.client.get(url, {"start": "bad", "end": start}).status_code, 400)
        self.movie.name = "=1+1"; self.movie.save()
        csv_response = self.client.get(reverse("export_bookings"), {"start": start, "end": start})
        self.assertTrue(csv_response.streaming)
        body = b"".join(csv_response.streaming_content)
        self.assertIn(b"reference,status,booked_at,movie,amount", body)
        self.assertIn(b"'=1+1", body)

        all_reports = self.client.get(
            reverse("export_analytics_csv"), {"start": start, "end": start},
        )
        self.assertTrue(all_reports.streaming)
        all_reports_body = b"".join(all_reports.streaming_content)
        self.assertIn(b"report,item,period,count,value,details", all_reports_body)
        for report in (
            b"summary,", b"revenue,daily", b"revenue,weekly", b"booking_trends,",
            b"occupancy,", b"top_movies,", b"top_theaters,", b"peak_hours,",
            b"user_growth,", b"bookings,",
        ):
            self.assertIn(report, all_reports_body)
        self.assertIn(b"'=1+1", all_reports_body)
        self.assertIn(str(paid.reference).encode(), all_reports_body)


    def _run_checkout_options_js(self, order):
        import re, shutil, subprocess
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        self.client.force_login(self.user)
        html = self.client.get(reverse("seat_selection", args=[self.show.id])).content.decode()
        match = re.search(r"function buildCheckoutOptions\(order\) \{.*?\n  \}\n", html, re.S)
        self.assertIsNotNone(match)
        script = match.group(0) + "console.log(JSON.stringify(buildCheckoutOptions(%s)));" % json.dumps(order)
        out = subprocess.run([node, "-e", script], capture_output=True, text=True, check=True, timeout=20)
        return json.loads(out.stdout)

    def test_checkout_options_have_no_runtime_display_override(self):
        base = {"key": "rzp_test_abc123", "order_id": "order_X", "amount": 25000, "currency": "INR"}
        for test_mode in (True, False):
            with self.subTest(test_mode=test_mode):
                options = self._run_checkout_options_js({**base, "test_mode": test_mode})
                self.assertEqual(options["order_id"], "order_X")
                self.assertEqual(options["amount"], 25000)
                self.assertNotIn("config", options)
                self.assertNotIn("checkout_config_id", options)
                self.assertNotIn("handler", options)

    def test_checkout_options_pass_dashboard_config_id_in_every_mode(self):
        base = {"key": "rzp_test_abc123", "order_id": "order_X", "amount": 25000, "currency": "INR",
                "checkout_config_id": "config_Ts1NJnhIOTM0SO"}
        for test_mode in (True, False):
            with self.subTest(test_mode=test_mode):
                options = self._run_checkout_options_js({**base, "test_mode": test_mode})
                self.assertEqual(options["checkout_config_id"], "config_Ts1NJnhIOTM0SO")
                self.assertNotIn("config", options)

    def test_pending_unpaid_reservation_can_be_cancelled_after_show_starts(self):
        self.client.force_login(self.user)
        reserved = self.client.post(
            reverse("reserve_seats", args=[self.show.pk]), {"seats": [self.seats[0].pk]}
        )
        self.assertEqual(reserved.status_code, 201)
        booking = Booking.objects.get(reference=reserved.json()["booking"])
        payment = Payment.objects.create(
            booking=booking, amount=booking.total_amount, idempotency_key="test-late-cancel-order",
            status=Payment.CREATED, order_state=Payment.ORDER_CREATED,
            provider_order_id="order_late_cancel",
        )
        Show.objects.filter(pk=self.show.pk).update(starts_at=timezone.now() - timedelta(minutes=1))

        response = self.client.post(
            reverse("cancel_booking", args=[booking.reference]), follow=True
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "The show has started, so this unpaid reservation was closed")
        booking.refresh_from_db()
        payment.refresh_from_db()
        self.show_seats[0].refresh_from_db()
        self.assertEqual(booking.status, Booking.CANCELLED)
        self.assertEqual(payment.status, Payment.CANCELLED)
        self.assertEqual(
            (self.show_seats[0].status, self.show_seats[0].held_by_id,
             self.show_seats[0].hold_expires_at, self.show_seats[0].active_booking_id),
            (ShowSeat.AVAILABLE, None, None, None),
        )

    def test_analytics_metrics_include_refunds_cancellations_occupancy_and_growth(self):
        from movies.views import _mark_payment_refunded
        now = timezone.now()
        today = timezone.localdate()
        analytics_show = Show.objects.create(
            theater=self.theater, screen=self.screen, movie=self.movie,
            starts_at=timezone.localtime(now).replace(hour=12, minute=0, second=0, microsecond=0),
            price=Decimal("250.00"),
        )
        captured_booking = self.create_confirmed_booking(user=self.user, show=analytics_show)
        refunded_booking = self.create_confirmed_booking(user=self.other, show=analytics_show)
        captured_payment = captured_booking.payments.get(status=Payment.CAPTURED)
        refund_payment = refunded_booking.payments.get(status=Payment.CAPTURED)
        _mark_payment_refunded(refund_payment.pk, "rfnd_analytics")
        cancelled = Booking.objects.create(
            user=User.objects.create_user("cancelled-viewer", password="StrongPass!234"),
            movie=self.movie, theater=self.theater, show=analytics_show,
            status=Booking.CANCELLED, total_amount=Decimal("250.00"),
        )
        admin_user = User.objects.create_superuser("analytics-metrics", "analytics-metrics@example.test", "StrongPass!234")
        self.client.force_login(admin_user)
        response = self.client.get(reverse("analytics_dashboard"), {"start": today.isoformat(), "end": today.isoformat()})
        self.assertEqual(response.status_code, 200)
        summary = response.context["summary"]
        self.assertEqual(summary["bookings"], 3)
        self.assertEqual(summary["paid_bookings"], 1)
        self.assertEqual(summary["gross_revenue"], captured_payment.amount)
        self.assertEqual(summary["refund_amount"], refund_payment.amount)
        self.assertEqual(summary["refund_count"], 1)
        self.assertEqual(summary["net_revenue"], Decimal("0.00"))
        self.assertEqual(summary["cancelled"], 2)
        self.assertEqual(summary["cancellation_rate"], 66.67)
        self.assertEqual(len(response.context["trends"]["daily"]), 1)
        self.assertEqual(len(response.context["trends"]["weekly"]), 1)
        self.assertEqual(len(response.context["trends"]["monthly"]), 1)
        self.assertEqual(len(response.context["trends"]["yearly"]), 1)
        self.assertEqual(response.context["booking_trends"][0]["bookings"], 3)
        self.assertEqual(response.context["booking_trends"][0]["cancelled"], 2)
        self.assertEqual(response.context["top_movies"][0]["bookings"], 1)
        self.assertEqual(response.context["top_theaters"][0]["bookings"], 1)
        self.assertEqual(sum(row["bookings"] for row in response.context["peak_hours"]), 3)
        self.assertEqual(response.context["occupancy"][0]["capacity"], 3)
        self.assertEqual(response.context["occupancy"][0]["booked"], 1)
        self.assertEqual(response.context["occupancy"][0]["percent"], 33.33)
        self.assertTrue(response.context["user_growth"])
        for row in response.context["user_growth"]:
            self.assertGreaterEqual(row["cumulative_users"], row["users"])
        self.assertContains(response, "cumulative users")
        empty = self.client.get(reverse("analytics_dashboard"), {
            "start": (today - timedelta(days=2)).isoformat(),
            "end": (today - timedelta(days=2)).isoformat(),
        })
        self.assertEqual(empty.context["summary"]["bookings"], 0)
        self.assertEqual(empty.context["summary"]["gross_revenue"], Decimal("0.00"))
        self.assertFalse(empty.context["occupancy"])

    def test_analytics_booking_date_range_uses_inclusive_local_calendar_days(self):
        from datetime import datetime, time
        today = timezone.localdate()
        start = timezone.make_aware(datetime.combine(today, time.min))
        next_day = start + timedelta(days=1)
        user = User.objects.create_superuser("analytics-boundary", "analytics-boundary@example.test", "StrongPass!234")
        for name, stamp in (("before", start - timedelta(microseconds=1)), ("at-start", start), ("at-end", next_day)):
            booking = Booking.objects.create(user=user, movie=self.movie, theater=self.theater, show=self.show)
            Booking.objects.filter(pk=booking.pk).update(booked_at=stamp)
        self.client.force_login(user)
        response = self.client.get(reverse("analytics_dashboard"), {"start": today.isoformat(), "end": today.isoformat()})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["summary"]["bookings"], 1)

    def test_staff_without_dashboard_permission_is_denied_and_superuser_allowed(self):
        staff = User.objects.create_user("staff-no-perm", "staff-no-perm@example.test", "StrongPass!234", is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(reverse("analytics_dashboard")).status_code, 403)
        root = User.objects.create_superuser("root", "root@example.test", "StrongPass!234")
        self.client.force_login(root)
        self.assertEqual(self.client.get(reverse("analytics_dashboard")).status_code, 200)

    def test_provider_payment_order_id_is_unique(self):
        first = Booking.objects.create(user=self.user, movie=self.movie, theater=self.theater, show=self.show)
        second = Booking.objects.create(user=self.other, movie=self.movie, theater=self.theater, show=self.show)
        Payment.objects.create(booking=first, amount=100, idempotency_key="order-uniq-1", provider_order_id="provider_order_same")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Payment.objects.create(booking=second, amount=100, idempotency_key="order-uniq-2", provider_order_id="provider_order_same")

    def test_benchmark_harness_refuses_sqlite_before_seeding(self):
        from django.db import connection
        if connection.vendor == "postgresql":
            self.skipTest("SQLite refusal is exercised only by the SQLite suite")
        with self.assertRaises(CommandError): call_command("benchmark_bookings", count=100000)
        self.assertEqual(Booking.objects.count(), 0)

    def test_benchmark_harness_requires_a_confirmed_empty_named_database(self):
        from movies.management.commands.benchmark_bookings import Command
        with self.assertRaises(CommandError):
            Command._validate_target("postgresql", "bookmyseat", True)
        with self.assertRaises(CommandError):
            Command._validate_target("postgresql", "bookmyseat_bench", False)
        Command._validate_target("postgresql", "bookmyseat_bench", True)

    def test_ticket_pdf_contains_signature_and_download_requires_owner(self):
        from pypdf import PdfReader
        booking = self.create_confirmed_booking()
        pdf = _render_pdf(booking, "signed-token")
        self.assertTrue(pdf.startswith(b"%PDF-"))
        text = "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages)
        payment = booking.payments.get(status=Payment.CAPTURED)
        self.assertIn(booking.movie.name, text)
        self.assertIn(booking.theater.name, text)
        self.assertIn(booking.show.screen.name, text)
        self.assertIn(booking.booking_seats.get().show_seat.seat.seat_number, text)
        self.assertIn(str(booking.reference), text)
        self.assertIn(payment.transaction_id, text)
        booking.ticket_file.save("test.pdf", ContentFile(pdf), save=True)
        url = reverse("ticket_download", args=[booking.reference])
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b"%PDF-"))

    def test_ticket_task_database_path_handles_show_and_legacy_booking_without_show(self):
        """Exercise task DB loading for both sides of the nullable show join.

        Rendering, storage, and email are mocked; this checks the database path
        and does not claim SMTP, Redis, or Celery worker operation.
        """
        with_show = self.create_confirmed_booking()
        legacy = self.create_confirmed_booking()
        # Preserve the old booking's paid seat history while removing the
        # nullable Show relation that existed before scheduled shows.
        Booking.objects.filter(pk=legacy.pk).update(show=None)
        legacy.refresh_from_db()
        self.assertIsNone(legacy.show_id)
        bookings = [with_show, legacy]

        with patch("movies.tasks._render_pdf", return_value=b"%PDF-test"), \
                patch("django.db.models.fields.files.FieldFile.save"), \
                patch("movies.tasks.EmailMessage.send", return_value=1) as send:
            self.assertEqual(send_booking_ticket.run(bookings[0].pk), "sent")
            self.assertEqual(send_booking_ticket.run(bookings[1].pk), "sent")
        self.assertEqual(send.call_count, 2)
        for booking in bookings:
            booking.refresh_from_db()
            self.assertEqual(booking.ticket_delivery_status, "sent")

    def test_ticket_task_postgresql_lock_sql_targets_booking_only(self):
        from django.db import connection as default_connection
        from django.db.backends.postgresql.base import DatabaseWrapper

        database_settings = default_connection.settings_dict.copy()
        database_settings.update({
            "ENGINE": "django.db.backends.postgresql", "NAME": "compile_only",
            "USER": "compile_only", "PASSWORD": "", "HOST": "localhost", "PORT": "5432",
        })
        postgres = DatabaseWrapper(database_settings, alias="postgres_sql_compile")
        # Compile-only check: no PostgreSQL connection is opened.
        postgres.get_autocommit = lambda: False
        query = Booking.objects.select_for_update(of=("self",)).select_related(
            "user", "movie", "theater", "show__screen"
        ).filter(pk=1)
        sql, _params = query.query.get_compiler(connection=postgres).as_sql()
        self.assertIn('LEFT OUTER JOIN "movies_show"', sql)
        self.assertIn('FOR UPDATE OF "movies_booking"', sql)
        lock_clause = sql.rsplit("FOR UPDATE", 1)[-1]
        self.assertNotIn('"movies_show"', lock_clause)
        self.assertNotIn('"movies_screen"', lock_clause)

    def test_ticket_verification_rejects_bad_token_and_refunded_booking(self):
        booking = self.create_confirmed_booking()
        token = signing.dumps({"ref": str(booking.reference)}, salt="bookmyseat.ticket")
        self.assertTrue(self.client.get(reverse("verify_ticket", args=[token])).json()["valid"])
        self.assertEqual(self.client.get(reverse("verify_ticket", args=["invalid-token"])).status_code, 400)
        booking.status = Booking.REFUNDED; booking.save(update_fields=["status"])
        self.assertFalse(self.client.get(reverse("verify_ticket", args=[token])).json()["valid"])

    @override_settings(SITE_URL="https://bookmyseat.test")
    def test_qr_round_trip_from_generated_pdf_to_verification_endpoint(self):
        from pypdf import PdfReader
        from PIL import Image
        import zxingcpp
        booking = self.create_confirmed_booking()
        token = signing.dumps({"ref": str(booking.reference)}, salt="bookmyseat.ticket")
        pdf_bytes = _render_pdf(booking, token)
        reader = PdfReader(io.BytesIO(pdf_bytes))
        self.assertEqual(len(reader.pages), 1)
        self.assertTrue(reader.pages[0].images)
        image = Image.open(io.BytesIO(reader.pages[0].images[0].data)).convert("RGB")
        decoded = zxingcpp.read_barcode(image)
        self.assertIsNotNone(decoded)
        self.assertEqual(decoded.text, f"https://bookmyseat.test{reverse('verify_ticket', args=[token])}")
        self.assertTrue(self.client.get(reverse("verify_ticket", args=[token])).json()["valid"])
        tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
        self.assertFalse(self.client.get(reverse("verify_ticket", args=[tampered])).json()["valid"])
        with patch("django.core.signing.time.time", return_value=time.time() - 366 * 24 * 60 * 60):
            expired = signing.dumps({"ref": str(booking.reference)}, salt="bookmyseat.ticket")
        self.assertEqual(self.client.get(reverse("verify_ticket", args=[expired])).status_code, 400)
        booking.status = Booking.REFUNDED; booking.save(update_fields=["status"])
        self.assertFalse(self.client.get(reverse("verify_ticket", args=[token])).json()["valid"])

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_ticket_email_retry_recovers_and_success_is_not_resent(self):
        booking = self.create_confirmed_booking()
        with patch("movies.tasks.EmailMessage.send", side_effect=OSError("temporary SMTP error")):
            with self.assertRaises(Exception): send_booking_ticket.run(booking.pk)
        booking.refresh_from_db()
        self.assertEqual(booking.ticket_delivery_status, "failed")
        self.assertEqual(booking.delivery_attempts, 1)
        result = send_booking_ticket.run(booking.pk)
        self.assertEqual(result, "sent")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].attachments[0][2], "application/pdf")
        self.assertEqual(send_booking_ticket.run(booking.pk), "skipped")
        self.assertEqual(len(mail.outbox), 1)
        booking.refresh_from_db()
        booking.ticket_file.delete(save=False)

    def test_ticket_delivery_retries_use_bounded_exponential_backoff(self):
        booking = self.create_confirmed_booking()
        self.assertEqual(send_booking_ticket.max_retries, 5)
        send_booking_ticket.push_request(retries=2)
        try:
            with patch("movies.tasks.EmailMessage.send", side_effect=OSError("temporary SMTP error")), \
                    patch.object(send_booking_ticket, "retry", side_effect=RuntimeError("retry scheduled")) as retry:
                with self.assertRaisesRegex(RuntimeError, "retry scheduled"):
                    send_booking_ticket.run(booking.pk)
        finally:
            send_booking_ticket.pop_request()
        self.assertEqual(retry.call_count, 1)
        self.assertEqual(retry.call_args.kwargs["countdown"], 240)
        booking.refresh_from_db()
        self.assertEqual(booking.ticket_delivery_status, "failed")

    def test_staff_csv_export_is_protected_and_streams_data(self):
        url = reverse("export_bookings")
        self.assertEqual(self.client.get(url).status_code, 302)
        staff = User.objects.create_superuser("staff", "staff@example.test", "StrongPass!234")
        self.client.force_login(staff)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.streaming)
        self.assertIn(b"reference,status,booked_at,movie,amount", b"".join(response.streaming_content))


    # ---- Final acceptance audit regressions (B1-B5, C1) --------------------

    def _reserved_booking_with_order(self, order_id="order-sm"):
        self.client.force_login(self.user)
        reserved = self.client.post(reverse("reserve_seats", args=[self.show.pk]), {"seats": [self.seats[0].pk]})
        self.assertEqual(reserved.status_code, 201)
        booking = Booking.objects.get(reference=reserved.json()["booking"])
        payment = Payment.objects.create(booking=booking, amount=booking.total_amount, provider_order_id=order_id,
                                         idempotency_key=f"key-{order_id}")
        return booking, payment

    @staticmethod
    def _capture_event(payment, txn_id):
        return {"event": "payment.captured", "payload": {"payment": {"entity": {
            "order_id": payment.provider_order_id, "id": txn_id, "status": "captured",
            "currency": "INR", "amount": int(payment.amount * 100)}}}}

    @staticmethod
    def _authorization_event(payment, txn_id):
        event = DomainTestCase._capture_event(payment, txn_id)
        event["event"] = "payment.authorized"
        event["payload"]["payment"]["entity"]["status"] = "authorized"
        return event

    @staticmethod
    def _refund_event(payment_txn, refund_id, amount, event="refund.processed", status="processed"):
        return {"event": event, "payload": {"refund": {"entity": {
            "id": refund_id, "payment_id": payment_txn, "amount": amount, "status": status}}}}

    def test_replayed_capture_after_refund_does_not_reopen_refunded_payment(self):
        booking, payment = self._reserved_booking_with_order()
        with patch("movies.views._enqueue_ticket") as ticket, patch("movies.views._enqueue_late_refund") as refund_enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                _process_payment_webhook(self._capture_event(payment, "pay-sm-1"))
            with self.captureOnCommitCallbacks(execute=True):
                _process_payment_webhook(self._refund_event("pay-sm-1", "rfnd-sm-1", 25000))
            payment.refresh_from_db(); booking.refresh_from_db()
            self.assertEqual((payment.status, payment.refund_status, payment.refund_id), (Payment.REFUNDED, "processed", "rfnd-sm-1"))
            self.assertEqual(booking.status, Booking.REFUNDED)
            refunded_at, captured_at = payment.refunded_at, payment.captured_at
            self.assertIsNotNone(refunded_at)
            # 1 + 2: delayed replay of the original capture, delivered twice.
            for _ in range(2):
                with self.captureOnCommitCallbacks(execute=True):
                    _process_payment_webhook(self._capture_event(payment, "pay-sm-1"))
                payment.refresh_from_db(); booking.refresh_from_db()
                self.assertEqual((payment.status, payment.refund_status, payment.refund_id, payment.transaction_id),
                                 (Payment.REFUNDED, "processed", "rfnd-sm-1", "pay-sm-1"))
                self.assertEqual((payment.refunded_at, payment.captured_at), (refunded_at, captured_at))
                self.assertEqual(booking.status, Booking.REFUNDED)
                self.assertFalse(ShowSeat.objects.filter(active_booking=booking).exists())
            self.assertEqual(Payment.objects.filter(booking=booking).count(), 1)
            ticket.assert_called_once_with(booking.pk)
            refund_enqueue.assert_not_called()

    def test_direct_confirm_of_refunded_payment_is_a_noop(self):
        booking, payment = self._reserved_booking_with_order("order-sm-direct")
        with patch("movies.views._enqueue_ticket"), patch("movies.views._enqueue_late_refund") as refund_enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                _confirm_payment(payment, "pay-direct")
            _process_payment_webhook(self._refund_event("pay-direct", "rfnd-direct", 25000))
            response = _confirm_payment(payment, "pay-direct")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(json.loads(response.content)["status"], "already_refunded")
            payment.refresh_from_db()
            self.assertEqual((payment.status, payment.refund_status), (Payment.REFUNDED, "processed"))
            refund_enqueue.assert_not_called()

    def test_different_capture_after_refund_is_a_separate_duplicate_charge(self):
        booking, payment = self._reserved_booking_with_order("order-sm-dup")
        with patch("movies.views._enqueue_ticket"), patch("movies.views._enqueue_late_refund") as refund_enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                _process_payment_webhook(self._capture_event(payment, "pay-orig"))
            _process_payment_webhook(self._refund_event("pay-orig", "rfnd-orig", 25000))
            for _ in range(2):  # second delivery must not create or enqueue again
                with self.captureOnCommitCallbacks(execute=True):
                    _process_payment_webhook(self._capture_event(payment, "pay-other"))
            original = Payment.objects.get(transaction_id="pay-orig")
            other = Payment.objects.get(transaction_id="pay-other")
            self.assertEqual((original.status, original.refund_status), (Payment.REFUNDED, "processed"))
            self.assertEqual((other.status, other.refund_reason, other.refund_status), (Payment.CAPTURED, "duplicate_charge", "pending"))
            self.assertEqual(Payment.objects.filter(booking=booking).count(), 2)
            refund_enqueue.assert_called_once_with(other.pk)

    def test_refund_event_before_capture_is_retryable_and_changes_nothing(self):
        booking, payment = self._reserved_booking_with_order("order-sm-early")
        with self.assertRaises(ValueError):
            _process_payment_webhook(self._refund_event("pay-early", "rfnd-early", 25000))
        payment.refresh_from_db(); booking.refresh_from_db()
        self.assertEqual((payment.status, payment.refund_id, booking.status), (Payment.CREATED, "", Booking.PENDING))
        with patch("movies.views._enqueue_ticket"), patch("movies.views._enqueue_late_refund"):
            with self.captureOnCommitCallbacks(execute=True):
                _process_payment_webhook(self._capture_event(payment, "pay-early"))
            # provider retry of the early refund event now succeeds, and repeats are idempotent
            for _ in range(3):
                _process_payment_webhook(self._refund_event("pay-early", "rfnd-early", 25000))
        payment.refresh_from_db(); booking.refresh_from_db()
        self.assertEqual((payment.status, payment.refund_status, payment.refund_id), (Payment.REFUNDED, "processed", "rfnd-early"))
        self.assertEqual(booking.status, Booking.REFUNDED)
        with self.assertRaises(ValueError):  # conflicting refund id never overwrites the final one
            _process_payment_webhook(self._refund_event("pay-early", "rfnd-conflict", 25000))
        payment.refresh_from_db()
        self.assertEqual(payment.refund_id, "rfnd-early")

    def test_late_authorized_event_after_refund_is_ignored(self):
        booking, payment = self._reserved_booking_with_order("order-sm-auth")
        with patch("movies.views._enqueue_ticket"):
            with self.captureOnCommitCallbacks(execute=True):
                _process_payment_webhook(self._capture_event(payment, "pay-auth"))
        _process_payment_webhook(self._refund_event("pay-auth", "rfnd-auth", 25000))
        event = self._capture_event(payment, "pay-auth"); event["event"] = "payment.authorized"
        event["payload"]["payment"]["entity"]["status"] = "authorized"
        _process_payment_webhook(event)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.REFUNDED)

    @override_settings(RAZORPAY_KEY_ID="test-key", RAZORPAY_KEY_SECRET="test-secret")
    def test_authorized_payment_blocks_second_checkout_without_new_order(self):
        booking, payment = self._reserved_booking_with_order("order-auth-checkout")
        _process_payment_webhook(self._authorization_event(payment, "pay-auth-checkout"))
        payment.refresh_from_db()
        self.assertEqual((payment.status, payment.transaction_id, payment.order_state),
                         (Payment.AUTHORIZED, "pay-auth-checkout", Payment.ORDER_CREATED))

        with patch("razorpay.Client") as provider:
            response = self.client.post(reverse("create_payment_order", args=[booking.reference]))
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["status"], "payment_authorization_unresolved")
            self.assertIn("may still be captured", response.json()["error"])
            provider.assert_not_called()

        booking.refresh_from_db(); payment.refresh_from_db(); self.show_seats[0].refresh_from_db()
        self.assertEqual(Payment.objects.filter(booking=booking).count(), 1)
        self.assertEqual((booking.status, booking.total_amount), (Booking.PENDING, Decimal("250.00")))
        self.assertEqual((payment.status, payment.transaction_id, payment.provider_order_id),
                         (Payment.AUTHORIZED, "pay-auth-checkout", "order-auth-checkout"))
        self.assertEqual((self.show_seats[0].status, self.show_seats[0].active_booking_id, self.show_seats[0].held_by_id),
                         (ShowSeat.HELD, booking.pk, self.user.pk))

    @override_settings(RAZORPAY_KEY_ID="test-key", RAZORPAY_KEY_SECRET="test-secret")
    def test_authorized_payment_blocks_selection_change_and_cancellation(self):
        booking, payment = self._reserved_booking_with_order("order-auth-edit")
        _process_payment_webhook(self._authorization_event(payment, "pay-auth-edit"))
        modify_url = reverse("modify_reservation", args=[booking.reference])
        reserve_url = reverse("reserve_seats", args=[self.show.pk])
        cancel_url = reverse("cancel_booking", args=[booking.reference])
        with patch("razorpay.Client") as provider:
            modified = self.client.post(modify_url, {"seats": [self.seats[1].pk]})
            added = self.client.post(reserve_url, {"seats": [self.seats[1].pk]})
            cancelled = self.client.post(cancel_url, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
            provider.assert_not_called()
        self.assertEqual((modified.status_code, added.status_code, cancelled.status_code), (409, 409, 409))
        for result in (modified, added, cancelled):
            self.assertEqual(result.json()["status"], "payment_authorization_unresolved")
        booking.refresh_from_db(); payment.refresh_from_db()
        self.assertEqual((booking.status, booking.total_amount), (Booking.PENDING, Decimal("250.00")))
        self.assertEqual(list(booking.booking_seats.values_list("show_seat__seat_id", flat=True)), [self.seats[0].pk])
        self.assertEqual((payment.status, payment.transaction_id), (Payment.AUTHORIZED, "pay-auth-edit"))
        self.show_seats[0].refresh_from_db(); self.show_seats[1].refresh_from_db()
        self.assertEqual((self.show_seats[0].status, self.show_seats[0].active_booking_id), (ShowSeat.HELD, booking.pk))
        self.assertEqual((self.show_seats[1].status, self.show_seats[1].active_booking_id), (ShowSeat.AVAILABLE, None))

    @override_settings(RAZORPAY_KEY_ID="test-key", RAZORPAY_KEY_SECRET="test-secret")
    def test_in_flight_order_creation_blocks_second_request_without_second_provider_call(self):
        booking, _ = self._reserved_booking_with_order("unused")
        Payment.objects.filter(booking=booking).delete()
        inflight = Payment.objects.create(booking=booking, amount=booking.total_amount, idempotency_key="inflight-1",
                                          order_state=Payment.ORDER_CREATING)
        with patch("razorpay.Client") as client:
            response = self.client.post(reverse("create_payment_order", args=[booking.reference]))
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["status"], "order_creation_in_progress")
            client.return_value.order.create.assert_not_called()
        self.assertEqual(Payment.objects.filter(booking=booking).count(), 1)
        # The database itself refuses a second in-flight row for the booking.
        with self.assertRaises(IntegrityError), transaction.atomic():
            Payment.objects.create(booking=booking, amount=booking.total_amount, idempotency_key="inflight-2",
                                   order_state=Payment.ORDER_CREATING)
        inflight.refresh_from_db()
        self.assertEqual(inflight.order_state, Payment.ORDER_CREATING)

    @override_settings(RAZORPAY_KEY_ID="test-key", RAZORPAY_KEY_SECRET="test-secret")
    def test_order_creation_reuses_completed_order_and_records_state(self):
        booking, _ = self._reserved_booking_with_order("unused-2")
        Payment.objects.filter(booking=booking).delete()
        with patch("razorpay.Client") as client:
            client.return_value.order.create.return_value = {"id": "order_new_1"}
            first = self.client.post(reverse("create_payment_order", args=[booking.reference]))
            second = self.client.post(reverse("create_payment_order", args=[booking.reference]))
            self.assertEqual(client.return_value.order.create.call_count, 1)
        self.assertEqual((first.status_code, second.status_code), (200, 200))
        self.assertEqual(first.json()["order_id"], second.json()["order_id"])
        self.assertEqual(first.json()["amount"], 25000)
        payment = Payment.objects.get(booking=booking)
        self.assertEqual((payment.provider_order_id, payment.order_state, payment.status), ("order_new_1", Payment.ORDER_CREATED, Payment.CREATED))
        self.assertEqual(client.return_value.order.create.call_args[0][0]["receipt"], payment.idempotency_key)

    # --- Optional Razorpay Payment Configuration ID (mocked SDK tests; not a real provider transaction) ---
    @override_settings(RAZORPAY_KEY_ID="test-key", RAZORPAY_KEY_SECRET="test-secret", RAZORPAY_CONFIG_ID="")
    def test_checkout_payload_omits_config_id_when_not_configured(self):
        booking, _ = self._reserved_booking_with_order("unused-cfg-0")
        Payment.objects.filter(booking=booking).delete()
        with patch("razorpay.Client") as client:
            client.return_value.order.create.return_value = {"id": "order_cfg_0"}
            response = self.client.post(reverse("create_payment_order", args=[booking.reference]))
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertNotIn("checkout_config_id", body)
        self.assertEqual((body["key"], body["order_id"], body["amount"], body["currency"]), ("test-key", "order_cfg_0", 25000, "INR"))
        self.assertNotIn("test-secret", json.dumps(body))

    @override_settings(RAZORPAY_KEY_ID="test-key", RAZORPAY_KEY_SECRET="test-secret", RAZORPAY_WEBHOOK_SECRET="hook-secret",
                       RAZORPAY_CONFIG_ID="config_Abc123DefGhi")
    def test_checkout_payload_includes_config_id_only_and_order_request_is_unchanged(self):
        booking, _ = self._reserved_booking_with_order("unused-cfg-1")
        Payment.objects.filter(booking=booking).delete()
        with patch("razorpay.Client") as client:
            client.return_value.order.create.return_value = {"id": "order_cfg_1"}
            response = self.client.post(reverse("create_payment_order", args=[booking.reference]))
            order_request = client.return_value.order.create.call_args[0][0]
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["checkout_config_id"], "config_Abc123DefGhi")
        self.assertEqual(set(order_request), {"amount", "currency", "receipt"})
        dumped = json.dumps(body)
        self.assertNotIn("test-secret", dumped)
        self.assertNotIn("hook-secret", dumped)

    @override_settings(RAZORPAY_KEY_ID="test-key", RAZORPAY_KEY_SECRET="test-secret", RAZORPAY_CONFIG_ID='not a config id"<script>')
    def test_malformed_config_id_is_not_forwarded_to_browser(self):
        booking, _ = self._reserved_booking_with_order("unused-cfg-2")
        Payment.objects.filter(booking=booking).delete()
        with patch("razorpay.Client") as client:
            client.return_value.order.create.return_value = {"id": "order_cfg_2"}
            response = self.client.post(reverse("create_payment_order", args=[booking.reference]))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("checkout_config_id", response.json())

    @override_settings(RAZORPAY_KEY_ID="test-key", RAZORPAY_KEY_SECRET="test-secret", RAZORPAY_CONFIG_ID="config_Abc123DefGhi")
    def test_rejected_order_with_config_id_is_safe_and_never_marks_paid(self):
        BadRequestError = type("BadRequestError", (Exception,), {})  # view classifies by class name
        booking, _ = self._reserved_booking_with_order("unused-cfg-3")
        Payment.objects.filter(booking=booking).delete()
        with patch("razorpay.Client") as client:
            client.return_value.order.create.side_effect = BadRequestError("rejected")
            response = self.client.post(reverse("create_payment_order", args=[booking.reference]))
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("checkout_config_id", response.json())
        self.assertNotIn("order_id", response.json())
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.PENDING)
        self.assertFalse(Payment.objects.filter(booking=booking, status=Payment.CAPTURED).exists())

    @override_settings(RAZORPAY_KEY_ID="test-key", RAZORPAY_KEY_SECRET="test-secret")
    def test_provider_timeout_is_recorded_as_uncertain_and_blocks_blind_retry(self):
        booking, _ = self._reserved_booking_with_order("unused-3")
        Payment.objects.filter(booking=booking).delete()
        with patch("razorpay.Client") as client:
            client.return_value.order.create.side_effect = TimeoutError("read timed out")
            first = self.client.post(reverse("create_payment_order", args=[booking.reference]))
            second = self.client.post(reverse("create_payment_order", args=[booking.reference]))
            self.assertEqual(client.return_value.order.create.call_count, 1)
        self.assertEqual(first.status_code, 502)
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.json()["status"], "order_uncertain_manual_review")
        payment = Payment.objects.get(booking=booking)
        self.assertEqual(Payment.objects.filter(booking=booking).count(), 1)
        self.assertEqual((payment.status, payment.order_state, payment.provider_order_id, payment.amount),
                         (Payment.CREATED, Payment.ORDER_UNCERTAIN, "", Decimal("250.00")))
        booking.refresh_from_db(); self.show_seats[0].refresh_from_db()
        self.assertEqual((booking.status, booking.total_amount), (Booking.PENDING, Decimal("250.00")))
        self.assertEqual((self.show_seats[0].status, self.show_seats[0].active_booking_id, self.show_seats[0].held_by_id),
                         (ShowSeat.HELD, booking.pk, self.user.pk))

    @override_settings(RAZORPAY_KEY_ID="test-key", RAZORPAY_KEY_SECRET="test-secret")
    def test_definite_provider_order_failure_allows_a_new_attempt(self):
        booking, _ = self._reserved_booking_with_order("unused-definite-failure")
        Payment.objects.filter(booking=booking).delete()
        BadRequestError = type("BadRequestError", (Exception,), {})
        with patch("razorpay.Client") as provider:
            provider.return_value.order.create.side_effect = [BadRequestError("rejected"), {"id": "order_retry_after_rejection"}]
            first = self.client.post(reverse("create_payment_order", args=[booking.reference]))
            second = self.client.post(reverse("create_payment_order", args=[booking.reference]))
            self.assertEqual(provider.return_value.order.create.call_count, 2)
        self.assertEqual((first.status_code, second.status_code), (502, 200))
        payments = list(Payment.objects.filter(booking=booking).order_by("pk"))
        self.assertEqual(len(payments), 2)
        failed, current = payments
        self.assertEqual((failed.status, failed.order_state, failed.provider_order_id, failed.amount),
                         (Payment.FAILED, Payment.ORDER_FAILED, "", Decimal("250.00")))
        self.assertEqual((current.status, current.order_state, current.provider_order_id, current.amount),
                         (Payment.CREATED, Payment.ORDER_CREATED, "order_retry_after_rejection", Decimal("250.00")))
        booking.refresh_from_db(); self.show_seats[0].refresh_from_db()
        self.assertEqual((booking.status, booking.total_amount), (Booking.PENDING, Decimal("250.00")))
        self.assertEqual((self.show_seats[0].status, self.show_seats[0].active_booking_id, self.show_seats[0].held_by_id),
                         (ShowSeat.HELD, booking.pk, self.user.pk))

    @override_settings(RAZORPAY_KEY_ID="test-key", RAZORPAY_KEY_SECRET="test-secret")
    def test_reconciliation_attaches_or_fails_uncertain_orders(self):
        from .tasks import reconcile_uncertain_payment_orders
        booking, _ = self._reserved_booking_with_order("unused-4")
        Payment.objects.filter(booking=booking).delete()
        payment = Payment.objects.create(booking=booking, amount=booking.total_amount, idempotency_key="recon-key",
                                         order_state=Payment.ORDER_UNCERTAIN)
        with patch("razorpay.Client") as client:
            client.return_value.order.all.return_value = {"items": [{"id": "order_found", "receipt": "recon-key", "amount": 25000}]}
            self.assertEqual(reconcile_uncertain_payment_orders.run(), 1)
        payment.refresh_from_db()
        self.assertEqual((payment.provider_order_id, payment.order_state), ("order_found", Payment.ORDER_CREATED))
        other = Payment.objects.create(booking=booking, amount=booking.total_amount, idempotency_key="recon-none",
                                       order_state=Payment.ORDER_UNCERTAIN, status=Payment.FAILED)
        Payment.objects.filter(pk=other.pk).update(updated_at=timezone.now() - timedelta(hours=1))
        with patch("razorpay.Client") as client:
            client.return_value.order.all.return_value = {"items": []}
            self.assertEqual(reconcile_uncertain_payment_orders.run(), 1)
        other.refresh_from_db()
        self.assertEqual(other.order_state, Payment.ORDER_FAILED)

    @override_settings(RAZORPAY_KEY_ID="test-key", RAZORPAY_KEY_SECRET="test-secret")
    def test_reconciliation_keeps_malformed_or_mismatched_receipt_uncertain(self):
        from .tasks import reconcile_uncertain_payment_orders
        booking, _ = self._reserved_booking_with_order("unused-reconcile-malformed")
        Payment.objects.filter(booking=booking).delete()
        incomplete = Payment.objects.create(
            booking=booking, amount=booking.total_amount, idempotency_key="recon-incomplete",
            order_state=Payment.ORDER_UNCERTAIN,
        )
        mismatch = Payment.objects.create(
            booking=booking, amount=booking.total_amount, idempotency_key="recon-mismatch",
            order_state=Payment.ORDER_UNCERTAIN,
        )
        old = timezone.now() - timedelta(hours=1)
        Payment.objects.filter(pk__in=[incomplete.pk, mismatch.pk]).update(updated_at=old)
        with patch("razorpay.Client") as client:
            client.return_value.order.all.side_effect = [
                {"items": [{"receipt": incomplete.idempotency_key, "amount": 25000}]},
                {"items": [{"id": "wrong-amount", "receipt": mismatch.idempotency_key, "amount": 30000}]},
            ]
            self.assertEqual(reconcile_uncertain_payment_orders.run(), 0)
        for payment in (incomplete, mismatch):
            payment.refresh_from_db()
            self.assertEqual((payment.order_state, payment.provider_order_id, payment.status),
                             (Payment.ORDER_UNCERTAIN, "", Payment.CREATED))

    @override_settings(RAZORPAY_KEY_ID="test-key", RAZORPAY_KEY_SECRET="test-secret")
    def test_delayed_capture_after_selection_change_is_refunded(self):
        booking, _ = self._reserved_booking_with_order("unused-5")
        Payment.objects.filter(booking=booking).delete()
        reserve_url = reverse("reserve_seats", args=[self.show.pk])
        with patch("razorpay.Client") as client:
            client.return_value.order.create.return_value = {"id": "order_stale"}
            created = self.client.post(reverse("create_payment_order", args=[booking.reference]))
            self.assertEqual(client.return_value.order.create.call_count, 1)
        self.assertEqual((created.status_code, created.json()["amount"], created.json()["order_id"]), (200, 25000, "order_stale"))
        self.assertEqual(self.client.post(reserve_url, {"seats": [self.seats[1].pk]}).status_code, 201)
        booking.refresh_from_db()
        self.assertEqual(booking.total_amount, Decimal("500.00"))
        stale = Payment.objects.get(booking=booking)
        self.assertEqual((stale.status, stale.amount, stale.provider_order_id, stale.order_state),
                         (Payment.FAILED, Decimal("250.00"), "order_stale", Payment.ORDER_CREATED))
        # A delayed authorization after the seat change is retained as unresolved,
        # then its capture is routed to late-refund recovery, never confirmation.
        with patch("movies.views._enqueue_late_refund") as refund_enqueue:
            _process_payment_webhook(self._authorization_event(stale, "pay-stale"))
            stale.refresh_from_db()
            self.assertEqual((stale.status, stale.transaction_id), (Payment.AUTHORIZED, "pay-stale"))
            booking.refresh_from_db()
            self.assertEqual((booking.status, booking.total_amount), (Booking.PENDING, Decimal("500.00")))
            with self.captureOnCommitCallbacks(execute=True):
                _process_payment_webhook(self._capture_event(stale, "pay-stale"))
            refund_enqueue.assert_called_once_with(stale.pk)
        stale.refresh_from_db(); booking.refresh_from_db()
        self.assertEqual((stale.status, stale.transaction_id, stale.refund_status, stale.refund_reason),
                         (Payment.CAPTURED, "pay-stale", "pending", "late_payment"))
        self.assertEqual(Payment.objects.filter(booking=booking).count(), 1)
        self.assertEqual((booking.status, booking.total_amount, booking.booking_seats.count()),
                         (Booking.FAILED, Decimal("500.00"), 2))
        rows = list(ShowSeat.objects.filter(show=self.show, seat__in=self.seats[:2]))
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row.status == ShowSeat.AVAILABLE and row.active_booking_id is None and row.held_by_id is None for row in rows))
        with self.captureOnCommitCallbacks(execute=True):
            _process_payment_webhook(self._capture_event(stale, "pay-stale"))
        self.assertEqual(refund_enqueue.call_count, 1)

    def test_started_or_past_shows_reject_new_reservations(self):
        self.client.force_login(self.user)
        now = timezone.now()
        for label, delta, expect_ok in (("past", timedelta(hours=-3), False), ("exact boundary", timedelta(0), False), ("future", timedelta(minutes=5), True)):
            show = Show.objects.create(theater=self.theater, screen=self.screen, movie=self.movie, starts_at=now + delta, price=Decimal("100.00"))
            with patch("movies.models.timezone.now", return_value=now):
                page = self.client.get(reverse("seat_selection", args=[show.pk]))
                post = self.client.post(reverse("reserve_seats", args=[show.pk]), {"seats": [self.seats[0].pk]})
            if expect_ok:
                self.assertEqual((page.status_code, post.status_code), (200, 201), label)
            else:
                self.assertEqual(page.status_code, 302, label)
                self.assertEqual(post.status_code, 409, label)
                self.assertFalse(Booking.objects.filter(show=show).exists(), label)
                self.assertFalse(ShowSeat.objects.filter(show=show, status=ShowSeat.HELD).exists(), label)

    @override_settings(RAZORPAY_KEY_ID="test-key", RAZORPAY_KEY_SECRET="test-secret")
    def test_show_passing_cutoff_blocks_order_creation_and_routes_capture_to_refund(self):
        booking, _ = self._reserved_booking_with_order("unused-6")
        Payment.objects.filter(booking=booking).delete()
        order_payment = Payment.objects.create(booking=booking, amount=booking.total_amount, idempotency_key="cutoff-key", provider_order_id="order-cutoff")
        # The show starts while checkout is in progress; provider captures the money anyway.
        Show.objects.filter(pk=self.show.pk).update(starts_at=timezone.now() - timedelta(seconds=1))
        with patch("movies.views._enqueue_ticket") as ticket, patch("movies.views._enqueue_late_refund") as refund_enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                _process_payment_webhook(self._capture_event(order_payment, "pay-cutoff"))
            refund_enqueue.assert_called_once_with(order_payment.pk)
            ticket.assert_not_called()
        order_payment.refresh_from_db(); booking.refresh_from_db()
        self.assertEqual((order_payment.status, order_payment.transaction_id, order_payment.refund_status, order_payment.refund_reason),
                         (Payment.CAPTURED, "pay-cutoff", "pending", "show_started"))
        self.assertEqual(booking.status, Booking.FAILED)
        self.assertFalse(ShowSeat.objects.filter(active_booking=booking).exists())

    @override_settings(RAZORPAY_KEY_ID="test-key", RAZORPAY_KEY_SECRET="test-secret")
    def test_create_payment_order_refuses_started_show_without_provider_call(self):
        booking, _ = self._reserved_booking_with_order("unused-8")
        Payment.objects.filter(booking=booking).delete()
        Show.objects.filter(pk=self.show.pk).update(starts_at=timezone.now() - timedelta(seconds=1))
        with patch("razorpay.Client") as client:
            response = self.client.post(reverse("create_payment_order", args=[booking.reference]))
            client.return_value.order.create.assert_not_called()
        self.assertEqual(response.status_code, 409)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.FAILED)
        self.assertFalse(ShowSeat.objects.filter(active_booking=booking).exists())

    def test_ineligible_legacy_review_is_hidden_and_excluded_from_aggregate(self):
        eligible_user = User.objects.create_user("eligible", "e@example.test", "StrongPass!234")
        past = Show.objects.create(theater=self.theater, screen=self.screen, movie=self.movie, starts_at=timezone.now() - timedelta(days=1), price=Decimal("100.00"))
        self.create_confirmed_booking(user=eligible_user, show=past, amount=Decimal("100.00"))
        Review.objects.create(movie=self.movie, user=eligible_user, rating=4, body="Seen it, good.")
        Review.objects.create(movie=self.movie, user=self.other, rating=1, body="Legacy review without a paid show.")
        response = self.client.get(reverse("movie_detail", args=[self.movie.pk]))
        bodies = [review.body for review in response.context["reviews"]]
        self.assertEqual(bodies, ["Seen it, good."])
        self.assertTrue(all(review.is_verified_viewer for review in response.context["reviews"]))
        self.assertNotContains(response, "Legacy review without a paid show.")
        self.movie.refresh_from_db()
        self.assertEqual((self.movie.rating, self.movie.review_count), (Decimal("4.0"), 1))

    def test_pdf_ticket_time_uses_local_timezone(self):
        from datetime import datetime, timezone as dt_timezone
        from pypdf import PdfReader
        known = datetime(2026, 11, 1, 18, 30, tzinfo=dt_timezone.utc)  # 00:00 IST on 2 Nov
        show = Show.objects.create(theater=self.theater, screen=self.screen, movie=self.movie, starts_at=known, price=Decimal("100.00"))
        booking = self.create_confirmed_booking(show=show, amount=Decimal("100.00"))
        with override_settings(TIME_ZONE="Asia/Kolkata"):
            pdf = _render_pdf(booking, "token")
        text = "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages)
        self.assertIn("02 Nov 2026, 00:00 IST", text)
        self.assertNotIn("01 Nov 2026, 18:30", text)
        show.refresh_from_db()
        self.assertEqual(show.starts_at, known)

    def test_personalized_recommendations_have_a_unique_final_tiebreaker(self):
        twins = [Movie.objects.create(name="Identical Title", rating=Decimal("4.0")) for _ in range(3)]
        for movie in twins:
            movie.genres.add(self.genre)
            Show.objects.create(
                theater=self.theater, screen=self.screen, movie=movie,
                starts_at=timezone.now() + timedelta(days=10), price=Decimal("100.00"),
            )
        self.client.force_login(self.user)
        response = self.client.get(reverse("movie_detail", args=[self.movie.pk]))  # records a view -> personalization
        self.assertEqual([m.pk for m in response.context["personalized"]], sorted(m.pk for m in twins))

    # ---- Defect-closure pass 2 (seat limit, cutoff TOCTOU, mixed expiry, migration 0014) ----

    def _more_seats(self, count):
        extra = [Seat.objects.create(screen=self.screen, seat_number=f"B{i}") for i in range(1, count + 1)]
        rows = [ShowSeat.objects.create(show=self.show, seat=seat) for seat in extra]
        return extra, rows

    def test_adding_a_seat_to_a_ten_seat_booking_is_rejected_atomically(self):
        extra, extra_rows = self._more_seats(8)
        all_seats = self.seats + extra  # 11 seats
        self.client.force_login(self.user)
        url = reverse("reserve_seats", args=[self.show.pk])
        first = self.client.post(url, {"seats": [seat.pk for seat in all_seats[:10]]})
        self.assertEqual(first.status_code, 201)
        booking = Booking.objects.get(reference=first.json()["booking"])
        order = Payment.objects.create(booking=booking, amount=booking.total_amount, idempotency_key="ten-seat-order", provider_order_id="order_ten")
        before_lines = {line.show_seat_id: line.price for line in booking.booking_seats.all()}
        before_total = booking.total_amount
        self.show.price = Decimal("400.00"); self.show.save(update_fields=["price"])
        rejected = self.client.post(url, {"seats": [all_seats[10].pk]})
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(rejected.json()["status"], "seat_limit_exceeded")
        booking.refresh_from_db(); order.refresh_from_db()
        self.assertEqual({line.show_seat_id: line.price for line in booking.booking_seats.all()}, before_lines)
        self.assertEqual(booking.total_amount, before_total)
        self.assertEqual(before_total, Decimal("2500.00"))
        self.assertEqual((order.status, order.provider_order_id), (Payment.CREATED, "order_ten"))
        self.assertEqual(ShowSeat.objects.filter(active_booking=booking, status=ShowSeat.HELD).count(), 10)
        extra_row = ShowSeat.objects.get(show=self.show, seat=all_seats[10])
        self.assertEqual((extra_row.status, extra_row.active_booking_id), (ShowSeat.AVAILABLE, None))
        self.assertEqual(Booking.objects.filter(user=self.user).count(), 1)
        # Modification path enforces the same limit on the final selection.
        too_many = self.client.post(reverse("modify_reservation", args=[booking.reference]), {"seats": [seat.pk for seat in all_seats]})
        self.assertEqual(too_many.status_code, 400)
        booking.refresh_from_db()
        self.assertEqual(booking.booking_seats.count(), 10)

    def _clock(self, before, after):
        """show_is_bookable stand-in: first call sees `before`, later calls see `after`."""
        from .models import show_is_bookable as real
        calls = []
        def fake(show, now=None):
            calls.append(now)
            return real(show, before if len(calls) == 1 else after)
        return fake, calls

    def test_reserve_rechecks_cutoff_after_locks_are_acquired(self):
        self.client.force_login(self.user)
        url = reverse("reserve_seats", args=[self.show.pk])
        start = self.show.starts_at
        for label, after, ok in (("exactly at start", start, False), ("after start", start + timedelta(seconds=1), False), ("still future", start - timedelta(seconds=1), True)):
            fake, calls = self._clock(start - timedelta(hours=1), after)
            with patch("movies.views.show_is_bookable", side_effect=fake):
                response = self.client.post(url, {"seats": [self.seats[0].pk]})
            self.assertGreaterEqual(len(calls), 2, label)  # pre-check AND in-lock recheck
            if ok:
                self.assertEqual(response.status_code, 201, label)
            else:
                self.assertEqual(response.status_code, 409, label)
                self.assertFalse(Booking.objects.filter(user=self.user).exists(), label)
                self.assertFalse(ShowSeat.objects.filter(show=self.show, status=ShowSeat.HELD).exists(), label)

    def test_modify_rechecks_cutoff_after_locks_are_acquired(self):
        self.client.force_login(self.user)
        first = self.client.post(reverse("reserve_seats", args=[self.show.pk]), {"seats": [self.seats[0].pk]})
        booking = Booking.objects.get(reference=first.json()["booking"])
        url = reverse("modify_reservation", args=[booking.reference])
        start = self.show.starts_at
        for label, after in (("exactly at start", start), ("after start", start + timedelta(seconds=5))):
            fake, calls = self._clock(start - timedelta(hours=1), after)
            with patch("movies.views.show_is_bookable", side_effect=fake):
                response = self.client.post(url, {"seats": [self.seats[0].pk, self.seats[1].pk]})
            self.assertEqual(response.status_code, 409, label)
            self.assertGreaterEqual(len(calls), 2, label)
            booking.refresh_from_db()
            self.assertEqual(booking.booking_seats.count(), 1, label)
            self.assertEqual(booking.total_amount, Decimal("250.00"), label)
            self.show_seats[1].refresh_from_db()
            self.assertEqual(self.show_seats[1].status, ShowSeat.AVAILABLE, label)
        fake, _ = self._clock(start - timedelta(hours=1), start - timedelta(seconds=1))
        with patch("movies.views.show_is_bookable", side_effect=fake):
            self.assertEqual(self.client.post(url, {"seats": [self.seats[0].pk, self.seats[1].pk]}).status_code, 200)

    def _two_seat_booking_with_one_expired(self):
        self.client.force_login(self.user)
        first = self.client.post(reverse("reserve_seats", args=[self.show.pk]), {"seats": [self.seats[0].pk, self.seats[1].pk]})
        self.assertEqual(first.status_code, 201)
        booking = Booking.objects.get(reference=first.json()["booking"])
        self.show.price = Decimal("300.00"); self.show.save(update_fields=["price"])  # snapshots stay 250
        ShowSeat.objects.filter(pk=self.show_seats[1].pk).update(hold_expires_at=timezone.now() - timedelta(seconds=1))
        for row in self.show_seats: row.refresh_from_db()
        # The expired row still *looks* owned: that is the mixed-expiry hazard.
        self.assertEqual((self.show_seats[1].status, self.show_seats[1].active_booking_id, self.show_seats[1].held_by_id), (ShowSeat.HELD, booking.pk, self.user.pk))
        return booking, reverse("modify_reservation", args=[booking.reference])

    def test_modify_retaining_an_expired_seat_renews_it_consistently(self):
        booking, url = self._two_seat_booking_with_one_expired()
        order = Payment.objects.create(booking=booking, amount=booking.total_amount, idempotency_key="mixed-keep", provider_order_id="order_mixed_keep")
        response = self.client.post(url, {"seats": [self.seats[0].pk, self.seats[1].pk]})
        self.assertEqual(response.status_code, 200)
        for row in self.show_seats: row.refresh_from_db()
        now = timezone.now()
        for row in self.show_seats[:2]:
            self.assertEqual((row.status, row.held_by_id, row.active_booking_id), (ShowSeat.HELD, self.user.pk, booking.pk))
            self.assertGreater(row.hold_expires_at, now)
        self.assertEqual(self.show_seats[0].hold_expires_at, self.show_seats[1].hold_expires_at)  # window not extended
        booking.refresh_from_db(); order.refresh_from_db()
        self.assertEqual(sorted(booking.booking_seats.values_list("price", flat=True)), [Decimal("250.00"), Decimal("250.00")])
        self.assertEqual(booking.total_amount, Decimal("500.00"))
        self.assertEqual(order.status, Payment.CREATED)  # selection and amount unchanged -> order still valid

    def test_modify_omitting_an_expired_seat_releases_it_and_adds_new_seat_at_current_price(self):
        booking, url = self._two_seat_booking_with_one_expired()
        response = self.client.post(url, {"seats": [self.seats[0].pk, self.seats[2].pk]})
        self.assertEqual(response.status_code, 200)
        for row in self.show_seats: row.refresh_from_db()
        self.assertEqual((self.show_seats[1].status, self.show_seats[1].held_by_id, self.show_seats[1].hold_expires_at, self.show_seats[1].active_booking_id), (ShowSeat.AVAILABLE, None, None, None))
        for row in (self.show_seats[0], self.show_seats[2]):
            self.assertEqual((row.status, row.active_booking_id, row.held_by_id), (ShowSeat.HELD, booking.pk, self.user.pk))
            self.assertGreater(row.hold_expires_at, timezone.now())
        booking.refresh_from_db()
        prices = {line.show_seat.seat_id: line.price for line in booking.booking_seats.select_related("show_seat")}
        self.assertEqual(prices, {self.seats[0].pk: Decimal("250.00"), self.seats[2].pk: Decimal("300.00")})
        self.assertEqual(booking.total_amount, Decimal("550.00"))

    def test_modify_dropping_only_the_expired_seat(self):
        booking, url = self._two_seat_booking_with_one_expired()
        self.assertEqual(self.client.post(url, {"seats": [self.seats[0].pk]}).status_code, 200)
        self.show_seats[1].refresh_from_db(); booking.refresh_from_db()
        self.assertEqual((self.show_seats[1].status, self.show_seats[1].active_booking_id), (ShowSeat.AVAILABLE, None))
        self.assertEqual((booking.booking_seats.count(), booking.total_amount), (1, Decimal("250.00")))

    def test_expired_seat_taken_by_someone_else_conflicts_and_is_never_released_by_us(self):
        booking, url = self._two_seat_booking_with_one_expired()
        self.client.force_login(self.other)
        taken = self.client.post(reverse("reserve_seats", args=[self.show.pk]), {"seats": [self.seats[1].pk]})
        self.assertEqual(taken.status_code, 201)
        thief = Booking.objects.get(reference=taken.json()["booking"])
        self.client.force_login(self.user)
        conflict = self.client.post(url, {"seats": [self.seats[0].pk, self.seats[1].pk]})
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(booking.booking_seats.count(), 2)
        self.show_seats[1].refresh_from_db()
        self.assertEqual(self.show_seats[1].active_booking_id, thief.pk)
        # Dropping the seat now owned by the other booking must not release it.
        self.assertEqual(self.client.post(url, {"seats": [self.seats[0].pk]}).status_code, 200)
        self.show_seats[1].refresh_from_db()
        self.assertEqual((self.show_seats[1].status, self.show_seats[1].active_booking_id, self.show_seats[1].held_by_id), (ShowSeat.HELD, thief.pk, self.other.pk))

    def test_migration_0014_marks_dead_failed_order_payments_failed_only(self):
        from importlib import import_module
        from django.apps import apps
        booking = Booking.objects.create(user=self.user, movie=self.movie, theater=self.theater, show=self.show, total_amount=Decimal("250.00"))
        make = lambda key, **kw: Payment.objects.create(booking=booking, amount=Decimal("250.00"), idempotency_key=key, **kw)
        dead = make("m-dead", order_state="failed")
        open_order = make("m-open", provider_order_id="order_m_open")
        creating = make("m-creating", order_state="creating")
        uncertain = make("m-uncertain", order_state="uncertain")
        captured = make("m-captured", status=Payment.CAPTURED, transaction_id="pay_m", order_state="failed")
        import_module("movies.migrations.0014_retire_failed_order_payments").retire_failed_order_payments(apps, None)
        for payment in (dead, open_order, creating, uncertain, captured): payment.refresh_from_db()
        self.assertEqual(dead.status, Payment.FAILED)
        self.assertEqual([open_order.status, creating.status, uncertain.status, captured.status],
                         [Payment.CREATED, Payment.CREATED, Payment.CREATED, Payment.CAPTURED])

    @override_settings(RAZORPAY_KEY_ID="test-key", RAZORPAY_KEY_SECRET="test-secret")
    def test_configured_order_creation_retires_dead_failed_row_from_unconfigured_attempt(self):
        booking, _ = self._reserved_booking_with_order("unused-9")
        Payment.objects.filter(booking=booking).delete()
        with override_settings(RAZORPAY_KEY_ID="", RAZORPAY_KEY_SECRET=""):
            unconfigured = self.client.post(reverse("create_payment_order", args=[booking.reference]))
        self.assertEqual(unconfigured.status_code, 503)
        dead = Payment.objects.get(pk=unconfigured.json()["payment"])
        self.assertEqual((dead.status, dead.order_state), (Payment.CREATED, Payment.ORDER_FAILED))  # transient, reusable while unconfigured
        with patch("razorpay.Client") as client:
            client.return_value.order.create.return_value = {"id": "order_after_config"}
            configured = self.client.post(reverse("create_payment_order", args=[booking.reference]))
        self.assertEqual(configured.status_code, 200)
        dead.refresh_from_db()
        self.assertEqual(dead.status, Payment.FAILED)
        self.assertEqual(Payment.objects.filter(booking=booking, status=Payment.CREATED).count(), 1)

    @override_settings(RAZORPAY_KEY_ID="test-key", RAZORPAY_KEY_SECRET="test-secret")
    def test_batch_recovery_tasks_use_deterministic_ordering(self):
        """Regression (found by the PostgreSQL full-suite run): sliced batch queries need ORDER BY.

        Without it PostgreSQL returns rows in an unspecified order, so a backlog
        larger than the batch size could repeatedly pick the same rows.
        """
        from .tasks import reconcile_uncertain_payment_orders, recover_late_payment_refunds
        for task in (recover_ticket_deliveries, recover_late_payment_refunds, reconcile_uncertain_payment_orders):
            with patch("razorpay.Client"), CaptureQueriesContext(connection) as queries:
                task.run()
            batch_selects = [q["sql"] for q in queries.captured_queries if "LIMIT 100" in q["sql"]]
            self.assertTrue(batch_selects, task.__name__)
            for sql in batch_selects:
                self.assertIn("ORDER BY", sql.upper(), task.__name__)

    def test_hold_duration_follows_seat_hold_seconds_setting_and_defaults_to_120(self):
        """D6: SEAT_HOLD_SECONDS drives the server-side hold; default is two minutes."""
        from django.conf import settings
        self.assertEqual(settings.SEAT_HOLD_SECONDS, 120)
        self.assertEqual(settings.BOOKING_HOLD_SECONDS, settings.SEAT_HOLD_SECONDS)
        self.client.force_login(self.user)
        before = timezone.now()
        with override_settings(SEAT_HOLD_SECONDS=45):
            response = self.client.post(reverse("reserve_seats", args=[self.show.id]), {"seats": [self.seats[0].id]})
        self.assertEqual(response.status_code, 201)
        self.show_seats[0].refresh_from_db()
        held_for = (self.show_seats[0].hold_expires_at - before).total_seconds()
        self.assertTrue(44 <= held_for <= 50, held_for)

    def test_index_comparison_command_refuses_sqlite_and_unconfirmed_targets(self):
        """Task 4.6 harness must never run on SQLite or without explicit disposable confirmation."""
        with self.assertRaises(CommandError):
            call_command("benchmark_index_comparison", confirm_disposable=True)
        with self.assertRaises(CommandError):
            call_command("benchmark_index_comparison")

    def test_occupancy_sums_per_theater_across_shows_screens_and_excludes_inactive_or_out_of_range(self):
        """Task 4.2 regression for the subquery rewrite: capacity/booked per theater, shows with no
        booked seats count toward capacity only, inactive and out-of-range shows are excluded."""
        from movies.views import _analytics_report_data
        now = timezone.now()
        today = timezone.localdate()
        noon = timezone.localtime(now).replace(hour=12, minute=0, second=0, microsecond=0)
        second_theater = Theater.objects.create(name="Occupancy Second Theater", city=self.theater.city)
        small = Screen.objects.create(theater=second_theater, name="Small")
        small_seats = [Seat.objects.create(screen=small, seat_number=f"S{i}") for i in range(2)]
        first = Show.objects.create(theater=self.theater, screen=self.screen, movie=self.movie, starts_at=noon, price=Decimal("100"))
        second = Show.objects.create(theater=self.theater, screen=self.screen, movie=self.movie, starts_at=noon + timedelta(hours=3), price=Decimal("100"))
        other = Show.objects.create(theater=second_theater, screen=small, movie=self.movie, starts_at=noon, price=Decimal("100"))
        inactive = Show.objects.create(theater=second_theater, screen=small, movie=self.movie, starts_at=noon, price=Decimal("100"), is_active=False)
        outside = Show.objects.create(theater=second_theater, screen=small, movie=self.movie, starts_at=noon - timedelta(days=10), price=Decimal("100"))
        for show in (first, second, other, inactive, outside):
            for seat in Seat.objects.filter(screen=show.screen):
                ShowSeat.objects.get_or_create(show=show, seat=seat)
        for show in (first, other, inactive, outside):
            self.create_confirmed_booking(user=self.user, show=show)
        rows = {r["theater"]: r for r in _analytics_report_data(today, today)["occupancy"]}
        screen_size = Seat.objects.filter(screen=self.screen).count()
        self.assertEqual((rows[self.theater.name]["capacity"], rows[self.theater.name]["booked"]), (2 * screen_size, 1))
        self.assertEqual((rows["Occupancy Second Theater"]["capacity"], rows["Occupancy Second Theater"]["booked"], rows["Occupancy Second Theater"]["percent"]), (2, 1, 50.0))
        self.assertEqual(len(rows), 2)
        with CaptureQueriesContext(connection) as small_run:
            _analytics_report_data(today, today)
        for _ in range(3):
            show = Show.objects.create(theater=second_theater, screen=small, movie=self.movie, starts_at=noon + timedelta(hours=5), price=Decimal("100"))
            ShowSeat.objects.bulk_create([ShowSeat(show=show, seat=seat) for seat in small_seats])
        with CaptureQueriesContext(connection) as larger_run:
            _analytics_report_data(today, today)
        self.assertEqual(len(small_run), len(larger_run))


    def _pending_webhook_booking(self, order_id):
        booking = Booking.objects.create(user=self.user, movie=self.movie, theater=self.theater, show=self.show, status=Booking.PENDING, total_amount=Decimal("250.00"))
        row = self.show_seats[0]
        row.status, row.held_by, row.hold_expires_at, row.active_booking = ShowSeat.HELD, self.user, timezone.now() + timedelta(minutes=1), booking
        row.save(); BookingSeat.objects.create(booking=booking, show_seat=row, price=self.show.price)
        Payment.objects.create(booking=booking, amount=booking.total_amount, idempotency_key="key-" + order_id, provider_order_id=order_id)
        return booking

    def _signed_webhook(self, event, order_id, pay_id, amount, event_id):
        raw = json.dumps({"event": event, "payload": {"payment": {"entity": {"order_id": order_id, "id": pay_id, "status": "captured", "currency": "INR", "amount": amount}},
                                                       "order": {"entity": {"id": order_id, "status": "paid"}}}}).encode()
        signature = hmac.new(b"webhook-secret", raw, hashlib.sha256).hexdigest()
        return self.client.post(reverse("razorpay_webhook"), raw, content_type="application/json", HTTP_X_RAZORPAY_SIGNATURE=signature, HTTP_X_RAZORPAY_EVENT_ID=event_id)

    @override_settings(RAZORPAY_WEBHOOK_SECRET="webhook-secret")
    def test_order_paid_webhook_confirms_once_and_later_payment_captured_does_not_duplicate(self):
        booking = self._pending_webhook_booking("order_paid_evt")
        self.assertEqual(self._signed_webhook("order.paid", "order_paid_evt", "pay_op", 25000, "evt-op-1").status_code, 200)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.CONFIRMED)
        self.assertEqual(self._signed_webhook("payment.captured", "order_paid_evt", "pay_op", 25000, "evt-op-2").status_code, 200)
        self.assertEqual(Booking.objects.filter(pk=booking.pk, status=Booking.CONFIRMED).count(), 1)
        self.assertEqual(BookingSeat.objects.filter(booking=booking).count(), 1)
        self.assertEqual(Payment.objects.filter(booking=booking, status=Payment.CAPTURED).count(), 1)

    @override_settings(RAZORPAY_WEBHOOK_SECRET="webhook-secret")
    def test_order_paid_webhook_with_wrong_amount_does_not_confirm(self):
        booking = self._pending_webhook_booking("order_paid_bad")
        self._signed_webhook("order.paid", "order_paid_bad", "pay_bad", 100, "evt-op-bad")
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.PENDING)
        self.assertFalse(Payment.objects.filter(booking=booking, status=Payment.CAPTURED).exists())


    # ---- Razorpay Checkout diagnosis regressions (config id, verify outcomes, page wiring) ----

    _CFG = "config_Ts1NJnhIOTM0SO"

    def _order_json(self, booking):
        with patch("razorpay.Client") as provider:
            response = self.client.post(reverse("create_payment_order", args=[booking.reference]))
            provider.assert_not_called()  # the existing created order is reused; no provider call
        self.assertEqual(response.status_code, 200)
        return response.json()

    @override_settings(RAZORPAY_KEY_ID="rzp_test_abc123", RAZORPAY_KEY_SECRET="test-secret", RAZORPAY_CONFIG_ID=_CFG)
    def test_valid_checkout_config_id_is_passed_without_leaking_secrets(self):
        booking, _ = self._reserved_booking_with_order("order-cfg")
        payload = self._order_json(booking)
        self.assertEqual(payload["checkout_config_id"], self._CFG)
        self.assertTrue(payload["test_mode"])
        self.assertNotIn("test-secret", json.dumps(payload))
        self.assertEqual(set(payload) - {"checkout_config_id"},
                         {"key", "order_id", "amount", "currency", "payment_id", "test_mode"})

    @override_settings(RAZORPAY_KEY_ID="rzp_test_abc123", RAZORPAY_KEY_SECRET="test-secret", RAZORPAY_CONFIG_ID="")
    def test_no_config_id_falls_back_to_account_default(self):
        booking, _ = self._reserved_booking_with_order("order-nocfg")
        payload = self._order_json(booking)
        self.assertNotIn("checkout_config_id", payload)

    def test_malformed_config_ids_are_ignored_and_logged(self):
        booking, _ = self._reserved_booking_with_order("order-badcfg")
        for bad in ("config_", "config_<script>", "config_a b c d e f", "cfg_Ts1NJnhIOTM0SO", "config_" + "A" * 41,
                    "https://evil.example/config_Ts1NJnhIOTM0SO"):
            with self.subTest(bad=bad), override_settings(
                    RAZORPAY_KEY_ID="rzp_test_abc123", RAZORPAY_KEY_SECRET="test-secret", RAZORPAY_CONFIG_ID=bad):
                with self.assertLogs("movies.views", level="WARNING") as logs:
                    payload = self._order_json(booking)
                self.assertNotIn("checkout_config_id", payload)
                self.assertTrue(any("malformed" in line for line in logs.output))

    @override_settings(RAZORPAY_KEY_ID="rzp_live_abc123", RAZORPAY_KEY_SECRET="live-secret", RAZORPAY_CONFIG_ID="")
    def test_live_key_is_not_reported_as_test_mode(self):
        booking, _ = self._reserved_booking_with_order("order-live")
        self.assertFalse(self._order_json(booking)["test_mode"])

    def _verify_post(self, payment, pay_id="pay_chk1", signature=None, order_id=None):
        signature = signature if signature is not None else hmac.new(
            b"test-secret", f"{payment.provider_order_id}|{pay_id}".encode(), hashlib.sha256).hexdigest()
        return self.client.post(reverse("verify_payment", args=[payment.pk]), {
            "razorpay_payment_id": pay_id, "razorpay_order_id": order_id or payment.provider_order_id,
            "razorpay_signature": signature})

    def _assert_unconfirmed(self, booking, payment):
        booking.refresh_from_db(); payment.refresh_from_db(); self.show_seats[0].refresh_from_db()
        self.assertEqual(booking.status, Booking.PENDING)
        self.assertNotEqual(payment.status, Payment.CAPTURED)
        self.assertEqual((self.show_seats[0].status, self.show_seats[0].active_booking_id), (ShowSeat.HELD, booking.pk))

    @override_settings(RAZORPAY_KEY_ID="rzp_test_abc123", RAZORPAY_KEY_SECRET="test-secret")
    def test_verify_rejects_bad_signature_without_provider_call_or_confirmation(self):
        booking, payment = self._reserved_booking_with_order("order-v-sig")
        with patch("razorpay.Client") as provider:
            response = self._verify_post(payment, signature="0" * 64)
            missing = self._verify_post(payment, signature="")
            wrong_order = self._verify_post(payment, order_id="order-other")
            provider.assert_not_called()
        for result in (response, missing, wrong_order):
            self.assertEqual(result.status_code, 400)
            self.assertEqual(result.json()["status"], "signature_invalid")
        self._assert_unconfirmed(booking, payment)

    @override_settings(RAZORPAY_KEY_ID="rzp_test_abc123", RAZORPAY_KEY_SECRET="test-secret")
    def test_verify_with_valid_signature_but_uncaptured_payment_never_confirms(self):
        booking, payment = self._reserved_booking_with_order("order-v-uncaptured")
        for provider_status, expected in (("authorized", "payment_not_captured"), ("created", "payment_not_captured"),
                                          ("failed", "payment_failed")):
            with self.subTest(provider_status=provider_status):
                with patch("razorpay.Client") as client:
                    client.return_value.payment.fetch.return_value = {
                        "order_id": payment.provider_order_id, "status": provider_status, "amount": 25000, "currency": "INR"}
                    response = self._verify_post(payment)
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json()["status"], expected)
                self._assert_unconfirmed(booking, payment)

    @override_settings(RAZORPAY_KEY_ID="rzp_test_abc123", RAZORPAY_KEY_SECRET="test-secret")
    def test_verify_rejects_amount_currency_or_order_mismatch(self):
        booking, payment = self._reserved_booking_with_order("order-v-mismatch")
        for field, value in (("amount", 100), ("currency", "USD"), ("order_id", "order_someone_else")):
            with self.subTest(field=field):
                provider_payment = {"order_id": payment.provider_order_id, "status": "captured", "amount": 25000, "currency": "INR"}
                provider_payment[field] = value
                with patch("razorpay.Client") as client:
                    client.return_value.payment.fetch.return_value = provider_payment
                    response = self._verify_post(payment)
                self.assertEqual((response.status_code, response.json()["status"]), (409, "payment_mismatch"))
                self._assert_unconfirmed(booking, payment)

    @override_settings(RAZORPAY_KEY_ID="rzp_test_abc123", RAZORPAY_KEY_SECRET="test-secret")
    def test_verify_provider_outage_does_not_confirm(self):
        booking, payment = self._reserved_booking_with_order("order-v-outage")
        with patch("razorpay.Client") as client:
            client.return_value.payment.fetch.side_effect = OSError("provider unreachable")
            response = self._verify_post(payment)
        self.assertEqual((response.status_code, response.json()["status"]), (502, "verification_unavailable"))
        self._assert_unconfirmed(booking, payment)

    @override_settings(RAZORPAY_KEY_ID="", RAZORPAY_KEY_SECRET="")
    def test_verify_refuses_to_run_without_provider_credentials(self):
        booking, payment = self._reserved_booking_with_order("order-v-nocreds")
        forged = hmac.new(b"", f"{payment.provider_order_id}|pay_forged".encode(), hashlib.sha256).hexdigest()
        with patch("razorpay.Client") as client:
            response = self._verify_post(payment, pay_id="pay_forged", signature=forged)
            client.assert_not_called()
        self.assertEqual((response.status_code, response.json()["status"]), (503, "provider_not_configured"))
        self._assert_unconfirmed(booking, payment)

    @override_settings(RAZORPAY_KEY_ID="rzp_test_abc123", RAZORPAY_KEY_SECRET="test-secret")
    def test_verify_confirms_only_after_signature_and_provider_capture_and_is_idempotent(self):
        booking, payment = self._reserved_booking_with_order("order-v-ok")
        with patch("razorpay.Client") as client:
            client.return_value.payment.fetch.return_value = {
                "order_id": "order-v-ok", "status": "captured", "amount": 25000, "currency": "INR"}
            first = self._verify_post(payment)
            replay = self._verify_post(payment)
        self.assertEqual((first.status_code, first.json()["status"]), (200, "confirmed"))
        self.assertEqual((replay.status_code, replay.json()["status"]), (200, "confirmed"))
        booking.refresh_from_db(); payment.refresh_from_db(); self.show_seats[0].refresh_from_db()
        self.assertEqual((booking.status, payment.status, payment.transaction_id), (Booking.CONFIRMED, Payment.CAPTURED, "pay_chk1"))
        self.assertEqual(self.show_seats[0].status, ShowSeat.BOOKED)
        self.assertEqual(Payment.objects.filter(booking=booking, status=Payment.CAPTURED).count(), 1)

    @override_settings(RAZORPAY_KEY_ID="rzp_test_abc123", RAZORPAY_KEY_SECRET="test-secret")
    def test_failed_payment_event_releases_hold_and_never_confirms(self):
        booking, payment = self._reserved_booking_with_order("order-v-failed-evt")
        _process_payment_webhook({"event": "payment.failed", "payload": {"payment": {"entity": {
            "order_id": "order-v-failed-evt", "id": "pay_declined", "status": "failed", "currency": "INR", "amount": 25000}}}})
        booking.refresh_from_db(); payment.refresh_from_db(); self.show_seats[0].refresh_from_db()
        self.assertEqual((booking.status, payment.status), (Booking.FAILED, Payment.FAILED))
        self.assertEqual(self.show_seats[0].status, ShowSeat.AVAILABLE)
        with patch("razorpay.Client") as client:
            client.return_value.payment.fetch.return_value = {
                "order_id": "order-v-failed-evt", "status": "failed", "amount": 25000, "currency": "INR"}
            response = self._verify_post(payment, pay_id="pay_declined")
        self.assertEqual(response.status_code, 409)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.FAILED)

    def test_seat_page_wires_checkout_failure_retry_and_test_mode_feedback(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse("seat_selection", args=[self.show.id]))
        for needle in ("__rzpScriptFailed", 'id="retry-payment"', 'id="test-mode-hint"', "payment.failed",
                       "ondismiss", "signature_invalid", "payment_not_captured",
                       "More than one active reservation exists for this show", "Your Bookings",
                       "scrollIntoView", "reservationSubmitting", "buildCheckoutOptions",
                       "error.metadata?.payment_id"):
            self.assertContains(page, needle)
        for forbidden in ("runtime-test-methods", "testCheckoutConfig", "show_default_blocks", "all_methods", "success@razorpay"):
            self.assertNotContains(page, forbidden)
        self.assertContains(page, "outcome.status === 'confirmed'")


class SeedDemoCatalogueTests(TestCase):
    def test_requires_confirmation_and_is_idempotent_without_overwriting(self):
        from io import StringIO
        from django.core.management import call_command
        call_command("seed_demo_catalogue", stdout=StringIO())
        self.assertEqual(Movie.objects.count(), 0)
        call_command("seed_demo_catalogue", "--yes", stdout=StringIO())
        movies, shows, seats = Movie.objects.count(), Show.objects.count(), Seat.objects.count()
        self.assertEqual((movies, shows, seats), (4, 12, 48))
        scheduled = list(Show.objects.select_related("movie").order_by("starts_at"))
        for earlier, later in zip(scheduled, scheduled[1:]):
            earlier_ends = earlier.starts_at + timedelta(minutes=earlier.movie.duration_minutes)
            self.assertLessEqual(earlier_ends, later.starts_at)
        Movie.objects.filter(name="Demo: Paper Moon Cafe").update(description="edited by admin")
        call_command("seed_demo_catalogue", "--yes", stdout=StringIO())
        self.assertEqual((Movie.objects.count(), Show.objects.count(), Seat.objects.count()), (movies, shows, seats))
        self.assertEqual(Movie.objects.get(name="Demo: Paper Moon Cafe").description, "edited by admin")

    def test_seeded_shows_do_not_overlap_existing_screen_schedule(self):
        from io import StringIO
        from django.core.management import call_command
        from movies.management.commands.seed_demo_catalogue import DEMO_SCREEN, DEMO_THEATER

        theater = Theater.objects.create(name=DEMO_THEATER)
        screen = Screen.objects.create(theater=theater, name=DEMO_SCREEN)
        existing_movie = Movie.objects.create(name="Existing screening", duration_minutes=180)
        existing_start = (timezone.now() + timedelta(days=1)).replace(
            hour=10, minute=0, second=0, microsecond=0
        )
        existing_show = Show.objects.create(
            theater=theater, screen=screen, movie=existing_movie, starts_at=existing_start
        )

        call_command("seed_demo_catalogue", "--yes", stdout=StringIO())

        scheduled = list(Show.objects.filter(screen=screen).select_related("movie").order_by("starts_at"))
        self.assertEqual(scheduled[0].pk, existing_show.pk)
        self.assertEqual(scheduled[0].starts_at, existing_start)
        for earlier, later in zip(scheduled, scheduled[1:]):
            earlier_ends = earlier.starts_at + timedelta(minutes=earlier.movie.duration_minutes)
            self.assertLessEqual(earlier_ends, later.starts_at)
