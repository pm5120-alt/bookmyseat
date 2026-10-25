import re
from urllib.parse import urlparse, parse_qs
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.templatetags.static import static
from django.db import models
from django.db.models import Q
from django.utils import timezone
from .storage import private_ticket_storage
from .demo_payments import desktop_demo_payments_enabled


def validate_trailer(value):
    extract_youtube_video_id(value)


def extract_youtube_video_id(value):
    """Validate supported HTTPS YouTube URLs and return only the video ID."""
    value = (value or "").strip()
    if not value:
        return ""
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", value):
        return value
    try:
        parsed = urlparse(value)
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port:
            raise ValueError
        host = (parsed.hostname or "").lower()
        video_id = ""
        if host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
            if parsed.path == "/watch": video_id = parse_qs(parsed.query).get("v", [""])[0]
            elif re.fullmatch(r"/embed/[A-Za-z0-9_-]{11}", parsed.path): video_id = parsed.path.rsplit("/", 1)[-1]
        elif host == "youtu.be" and re.fullmatch(r"/[A-Za-z0-9_-]{11}", parsed.path):
            video_id = parsed.path[1:]
        if re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id) and not parsed.fragment:
            return video_id
    except (ValueError, TypeError):
        pass
    raise ValidationError("Enter a valid YouTube video ID or HTTPS YouTube URL.")


def validate_poster(image):
    if image and image.size > 5 * 1024 * 1024:
        raise ValidationError("Image files must be 5 MB or smaller.")
    if not image:
        return
    is_committed_field_file = bool(getattr(image, "_committed", False))
    file_was_open = getattr(image, "_file", None) is not None
    try:
        from PIL import Image, UnidentifiedImageError
        with Image.open(image) as opened:
            if opened.format not in {"JPEG", "PNG", "WEBP"}:
                raise ValidationError("Upload a JPEG, PNG, or WebP image.")
            if opened.width * opened.height > 40_000_000:
                raise ValidationError("Image dimensions are too large.")
            opened.verify()
        if not is_committed_field_file:
            image.seek(0)
    except ValidationError:
        raise
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        raise ValidationError("The uploaded file is not a valid JPEG, PNG, or WebP image.") from exc
    finally:
        # Pillow may open a model-backed FieldFile while validating an admin
        # change form. Close only handles opened here; upload streams must stay
        # open and rewound for Django to save them after validation.
        if is_committed_field_file and not file_was_open and getattr(image, "_file", None) is not None:
            image.close()


class City(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(unique=True)

    def __str__(self):
        return self.name


class Genre(models.Model):
    name = models.CharField(max_length=80, unique=True)
    def __str__(self): return self.name


class Language(models.Model):
    name = models.CharField(max_length=80, unique=True)
    def __str__(self): return self.name


class Movie(models.Model):
    name = models.CharField(max_length=255)
    image = models.ImageField(upload_to="movies/", blank=True, null=True, validators=[validate_poster])
    rating = models.DecimalField(max_digits=3, decimal_places=1, default=0)
    cast = models.TextField(blank=True)
    description = models.TextField(blank=True, null=True)
    release_date = models.DateField(null=True, blank=True, db_index=True)
    duration_minutes = models.PositiveSmallIntegerField(default=120, validators=[MinValueValidator(1), MaxValueValidator(600)])
    certification = models.CharField(max_length=12, blank=True)
    trailer_video_id = models.CharField(max_length=11, blank=True, validators=[validate_trailer])
    genres = models.ManyToManyField(Genre, blank=True, related_name="movies")
    languages = models.ManyToManyField(Language, blank=True, related_name="movies")
    review_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True, null=True)

    @property
    def average_rating(self):
        # `rating` is the denormalized aggregate maintained by review, booking,
        # and payment signals using the completed-paid-show eligibility rule.
        return self.rating

    @property
    def poster_url(self):
        try:
            return self.image.url if self.image and self.image.storage.exists(self.image.name) else ""
        except (OSError, ValueError):
            return ""

    @property
    def display_poster_url(self):
        """Return a real uploaded poster or a bundled fallback served by Django staticfiles."""
        uploaded = self.poster_url
        if uploaded:
            return uploaded
        demo_posters = {
            "Demo: The Lantern Harbour": "posters/demo-the-lantern-harbour.svg",
            "Demo: Monsoon Circuit": "posters/demo-monsoon-circuit.svg",
            "Demo: Paper Moon Cafe": "posters/demo-paper-moon-cafe.svg",
            "Demo: Signal Garden": "posters/demo-signal-garden.svg",
        }
        return static(demo_posters.get(self.name, "posters/poster-default.svg"))

    def save(self, *args, **kwargs):
        self.trailer_video_id = extract_youtube_video_id(self.trailer_video_id)
        super().save(*args, **kwargs)

    def __str__(self): return self.name


class CastMember(models.Model):
    movie = models.ForeignKey(Movie, on_delete=models.CASCADE, related_name="cast_members")
    name = models.CharField(max_length=160)
    role = models.CharField(max_length=160, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    class Meta: ordering = ["sort_order", "name"]
    def __str__(self): return f"{self.name} ({self.movie})"


class MoviePoster(models.Model):
    movie = models.ForeignKey(Movie, on_delete=models.CASCADE, related_name="posters")
    image = models.ImageField(upload_to="movies/posters/", validators=[validate_poster])
    alt_text = models.CharField(max_length=200, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_primary = models.BooleanField(default=False)
    @property
    def image_url(self):
        try:
            return self.image.url if self.image and self.image.storage.exists(self.image.name) else ""
        except (OSError, ValueError):
            return ""
    class Meta: ordering = ["-is_primary", "sort_order"]


class RecentlyViewedMovie(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="recently_viewed_movies")
    movie = models.ForeignKey(Movie, on_delete=models.CASCADE, related_name="recent_views")
    last_viewed_at = models.DateTimeField(auto_now=True, db_index=True)
    views = models.PositiveIntegerField(default=1)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "movie"], name="uniq_user_recent_movie")]
        ordering = ["-last_viewed_at"]


# Legacy venue/show record retained for data migration compatibility.
class Theater(models.Model):
    name = models.CharField(max_length=255)
    # Optional legacy columns; new schedules live in Show.
    movie = models.ForeignKey(Movie, null=True, blank=True, on_delete=models.SET_NULL, related_name="theaters")
    time = models.DateTimeField(null=True, blank=True)
    city = models.ForeignKey(City, null=True, blank=True, on_delete=models.SET_NULL, related_name="legacy_theaters")
    address = models.CharField(max_length=255, blank=True)
    def __str__(self): return self.name


class Screen(models.Model):
    theater = models.ForeignKey(Theater, on_delete=models.CASCADE, related_name="screens")
    name = models.CharField(max_length=80)
    class Meta: unique_together = [("theater", "name")]
    def __str__(self): return f"{self.theater.name} / {self.name}"


class Show(models.Model):
    theater = models.ForeignKey(Theater, on_delete=models.CASCADE, related_name="shows")
    screen = models.ForeignKey(Screen, on_delete=models.PROTECT, related_name="shows")
    movie = models.ForeignKey(Movie, on_delete=models.PROTECT, related_name="shows")
    starts_at = models.DateTimeField(db_index=True)
    price = models.DecimalField(max_digits=8, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    is_active = models.BooleanField(default=True)
    class Meta: indexes = [models.Index(fields=["movie", "starts_at"])]
    def clean(self):
        super().clean()
        if self.screen_id and self.theater_id and self.screen.theater_id != self.theater_id:
            raise ValidationError({"screen": "The screen must belong to this theater."})
    def save(self, *args, **kwargs):
        if self.screen_id and self.theater_id and not Screen.objects.filter(pk=self.screen_id, theater_id=self.theater_id).exists():
            raise ValidationError({"screen": "The screen must belong to this theater."})
        super().save(*args, **kwargs)
    def __str__(self): return f"{self.movie} at {self.theater} {self.starts_at}"


def show_is_bookable(show, now=None):
    """Booking cutoff: a show is bookable only while active and strictly before
    its (timezone-aware) start. starts_at <= now is NOT bookable."""
    if show is None:
        return True  # legacy bookings without a Show are not subject to the cutoff
    now = now or timezone.now()
    return bool(show.is_active and show.starts_at > now)


class Seat(models.Model):
    screen = models.ForeignKey(Screen, on_delete=models.CASCADE, related_name="seats")
    seat_number = models.CharField(max_length=10)
    is_booked = models.BooleanField(default=False)  # Legacy display field; ShowSeat is authoritative.
    def __str__(self): return f"{self.seat_number} in {self.screen}"


class ShowSeat(models.Model):
    AVAILABLE, HELD, BOOKED = "available", "held", "booked"
    STATUS_CHOICES = [(AVAILABLE, "Available"), (HELD, "Reserved"), (BOOKED, "Booked")]
    show = models.ForeignKey(Show, on_delete=models.CASCADE, related_name="show_seats")
    seat = models.ForeignKey(Seat, on_delete=models.PROTECT, related_name="show_seats")
    active_booking = models.ForeignKey("Booking", null=True, blank=True, on_delete=models.PROTECT, related_name="active_show_seats")
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=AVAILABLE, db_index=True)
    held_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="seat_holds")
    hold_expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["show", "seat"], name="uniq_show_seat"),
                       models.CheckConstraint(condition=Q(status="available", held_by__isnull=True, hold_expires_at__isnull=True, active_booking__isnull=True) | Q(status="held", held_by__isnull=False, hold_expires_at__isnull=False, active_booking__isnull=False) | Q(status="booked", held_by__isnull=True, hold_expires_at__isnull=True, active_booking__isnull=False), name="show_seat_state_consistent")]
    def save(self, *args, **kwargs):
        if self.show_id and self.seat_id and not Show.objects.filter(pk=self.show_id, screen__seats__pk=self.seat_id).exists():
            raise ValidationError("A show's seat must belong to the show's screen.")
        super().save(*args, **kwargs)
    def __str__(self): return f"{self.show}: {self.seat.seat_number} ({self.status})"


class Booking(models.Model):
    PENDING, CONFIRMED, FAILED, CANCELLED, CANCELLATION_PENDING, REFUNDED = "pending", "confirmed", "failed", "cancelled", "cancellation_pending", "refunded"
    STATUS_CHOICES = [(v, v.replace("_", " ").title()) for v in (PENDING, CONFIRMED, FAILED, CANCELLED, CANCELLATION_PENDING, REFUNDED)]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="movie_bookings")
    seat = models.ForeignKey(Seat, null=True, blank=True, on_delete=models.PROTECT, related_name="legacy_bookings")
    movie = models.ForeignKey(Movie, on_delete=models.PROTECT)
    theater = models.ForeignKey(Theater, on_delete=models.PROTECT)
    show = models.ForeignKey(Show, null=True, blank=True, on_delete=models.PROTECT, related_name="bookings")
    seats = models.ManyToManyField(ShowSeat, through="BookingSeat", related_name="bookings")
    reference = models.UUIDField(default=__import__("uuid").uuid4, unique=True, editable=False)
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default=PENDING, db_index=True)
    booked_at = models.DateTimeField(auto_now_add=True)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    ticket_file = models.FileField(upload_to="tickets/", storage=private_ticket_storage, blank=True)
    ticket_delivery_status = models.CharField(max_length=16, default="pending")
    delivery_attempts = models.PositiveSmallIntegerField(default=0)
    delivery_started_at = models.DateTimeField(null=True, blank=True)
    delivery_last_error = models.TextField(blank=True)
    delivery_sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        permissions = [("view_booking_analytics", "Can view booking analytics and exports")]
        indexes = [models.Index(fields=["booked_at", "status"], name="booking_date_status_idx")]

    def save(self, *args, **kwargs):
        if self.status == self.CONFIRMED:
            was_confirmed = self.pk and Booking.objects.filter(pk=self.pk, status=self.CONFIRMED).exists()
            captured_matches = bool(self.pk and self.payments.filter(status=Payment.CAPTURED, amount=self.total_amount).exists())
            demo_matches = bool(
                self.pk and desktop_demo_payments_enabled()
                and self.payments.filter(status=Payment.SIMULATED, amount=self.total_amount).exists()
            )
            if not was_confirmed and not captured_matches and not demo_matches:
                raise ValidationError(
                    "A booking can only be confirmed after a matching captured payment or an explicitly enabled demo simulation."
                )
        super().save(*args, **kwargs)

    def __str__(self): return f"Booking {self.reference} by {self.user}"


class BookingSeat(models.Model):
    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name="booking_seats")
    show_seat = models.ForeignKey(ShowSeat, on_delete=models.PROTECT, related_name="booking_seats")
    price = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    class Meta: constraints = [models.UniqueConstraint(fields=["booking", "show_seat"], name="uniq_booking_showseat")]


class Payment(models.Model):
    CREATED, AUTHORIZED, CAPTURED, FAILED, CANCELLED, REFUNDED, SIMULATED = "created", "authorized", "captured", "failed", "cancelled", "refunded", "simulated"
    STATUS_CHOICES = [(v, v.title()) for v in (CREATED, AUTHORIZED, CAPTURED, FAILED, CANCELLED, REFUNDED, SIMULATED)]
    booking = models.ForeignKey(Booking, on_delete=models.PROTECT, related_name="payments")
    provider_order_id = models.CharField(max_length=100, blank=True, db_index=True)
    transaction_id = models.CharField(max_length=100, blank=True, db_index=True)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=CREATED, db_index=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default="INR")
    idempotency_key = models.CharField(max_length=100, unique=True)
    refund_id = models.CharField(max_length=100, blank=True)
    refund_status = models.CharField(max_length=20, default="none")
    refund_reason = models.CharField(max_length=32, blank=True)
    # Durable provider-order creation state (see docs/PAYMENT_STATE_MACHINE.md):
    # creating -> created | failed | uncertain. "uncertain" means the provider
    # call outcome is unknown and needs reconciliation / manual review.
    ORDER_CREATING, ORDER_CREATED, ORDER_FAILED, ORDER_UNCERTAIN = "creating", "created", "failed", "uncertain"
    order_state = models.CharField(max_length=12, default="created")
    captured_at = models.DateTimeField(null=True, blank=True, db_index=True)
    refunded_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["provider_order_id"], condition=~Q(provider_order_id=""), name="uniq_payment_provider_order"),
            models.UniqueConstraint(fields=["transaction_id"], condition=~Q(transaction_id=""), name="uniq_payment_provider_txn"),
            models.UniqueConstraint(fields=["refund_id"], condition=~Q(refund_id=""), name="uniq_payment_provider_refund"),
            # At most one in-flight provider-order creation per booking.
            models.UniqueConstraint(fields=["booking"], condition=Q(order_state="creating", status="created"), name="uniq_payment_creating_per_booking"),
        ]
        indexes = [
            models.Index(fields=["status", "captured_at"], name="payment_capture_range_idx"),
            models.Index(fields=["status", "refunded_at"], name="payment_refund_range_idx"),
        ]


class Review(models.Model):
    movie = models.ForeignKey(Movie, on_delete=models.CASCADE, related_name="reviews")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="movie_reviews")
    rating = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    body = models.TextField(max_length=3000)
    is_hidden = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["movie", "user"], name="uniq_review_movie_user"),
                       models.CheckConstraint(condition=Q(rating__gte=1, rating__lte=5), name="review_rating_1_to_5")]
        ordering = ["-created_at"]
    @property
    def verified_viewer(self): return eligible_viewer(self.user_id, self.movie_id)


class ReviewReport(models.Model):
    review = models.ForeignKey(Review, on_delete=models.CASCADE, related_name="reports")
    reporter = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    reason = models.CharField(max_length=500)
    resolved_at = models.DateTimeField(null=True, blank=True)
    class Meta: constraints = [models.UniqueConstraint(fields=["review", "reporter"], name="uniq_review_reporter")]


def eligible_viewer(user_id, movie_id, at=None):
    at = at or timezone.now()
    candidates = Booking.objects.filter(user_id=user_id, movie_id=movie_id, status=Booking.CONFIRMED,
        payments__status=Payment.CAPTURED, show__starts_at__isnull=False).select_related("show", "movie").distinct()
    return any(b.show.starts_at + timedelta(minutes=b.movie.duration_minutes) <= at for b in candidates)


class PaymentWebhookEvent(models.Model):
    event_id = models.CharField(max_length=180, unique=True)
    event_type = models.CharField(max_length=100, blank=True)
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=16, default="received", db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.TextField(blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)

