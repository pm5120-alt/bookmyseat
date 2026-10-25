import hashlib
import hmac
import json
import logging
import re
import uuid
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, Exists, F, OuterRef, Q, Subquery, Sum
from django.db.models.functions import Coalesce
from django.http import HttpResponse, JsonResponse, HttpResponseForbidden, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .models import (Booking, BookingSeat, City, Genre, Language, Movie, Payment,
                     PaymentWebhookEvent, RecentlyViewedMovie, Review, ReviewReport, Seat, Show,
                     ShowSeat, Theater, eligible_viewer, show_is_bookable)
from .recommendations import recommendations_for_user

logger = logging.getLogger(__name__)

MAX_SEATS_PER_BOOKING = 10


def movie_list(request):
    from datetime import date
    from django.db.models import Avg, Count, Min

    now = timezone.now()
    qs = Movie.objects.all().prefetch_related("genres", "languages")
    q = request.GET.get("search", "").strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(description__icontains=q))

    def parse_ids(parameter):
        raw_ids = [value.strip() for value in request.GET.getlist(parameter) if value.strip()]
        if any(not value.isdecimal() for value in raw_ids):
            raise ValueError(f"Invalid {parameter} filter.")
        return sorted({int(value) for value in raw_ids})

    try:
        release_from = date.fromisoformat(request.GET["release_from"]) if request.GET.get("release_from") else None
        release_to = date.fromisoformat(request.GET["release_to"]) if request.GET.get("release_to") else None
        show_day = date.fromisoformat(request.GET["show_date"]) if request.GET.get("show_date") else None
        # Keep the old one-sided parameter as an alias for existing bookmarks.
        show_time_from = request.GET.get("show_time_from") or request.GET.get("show_time")
        show_time_to = request.GET.get("show_time_to")
        show_time_from = time.fromisoformat(show_time_from) if show_time_from else None
        show_time_to = time.fromisoformat(show_time_to) if show_time_to else None
        genre_ids = parse_ids("genre")
        language_ids = parse_ids("language")
        theater_values = request.GET.getlist("theater")
        theater_values = [value.strip() for value in theater_values if value.strip()]
        if len(theater_values) > 1 or any(not value.isdecimal() for value in theater_values):
            raise ValueError("Invalid theater filter.")
        theater_id = int(theater_values[0]) if theater_values else None
    except (ValueError, TypeError):
        return JsonResponse({"error": "Invalid date, time, genre, language, or theater filter."}, status=400)
    if release_from and release_to and release_from > release_to:
        return JsonResponse({"error": "Release start date must be on or before the end date."}, status=400)
    if show_time_from and show_time_to and show_time_from > show_time_to:
        return JsonResponse({"error": "Show start time must be on or before the end time."}, status=400)
    if genre_ids:
        qs = qs.filter(genres__id__in=genre_ids)
    if language_ids:
        qs = qs.filter(languages__id__in=language_ids)
    if release_from:
        qs = qs.filter(release_date__gte=release_from)
    if release_to:
        qs = qs.filter(release_date__lte=release_to)
    if request.GET.get("min_rating"):
        try:
            min_rating = Decimal(request.GET["min_rating"])
            if not min_rating.is_finite() or not Decimal("0") <= min_rating <= Decimal("5"):
                return JsonResponse({"error": "Minimum rating must be between 0 and 5."}, status=400)
            qs = qs.filter(rating__gte=min_rating)
        except (ValueError, ArithmeticError): return JsonResponse({"error": "Invalid minimum rating."}, status=400)

    # Keep city, theater, date and time constraints on one matching Show row.
    show_matches = Show.objects.filter(movie_id=OuterRef("pk"), is_active=True, starts_at__gte=now)
    city_slug = request.GET.get("city", "").strip()
    if city_slug:
        show_matches = show_matches.filter(theater__city__slug=city_slug)
    if theater_id:
        show_matches = show_matches.filter(theater_id=theater_id)
    if show_day:
        day_start = timezone.make_aware(datetime.combine(show_day, time.min), timezone.get_current_timezone())
        day_end = day_start + timedelta(days=1)
        show_matches = show_matches.filter(starts_at__lt=day_end)
        if day_start > now:
            show_matches = show_matches.filter(starts_at__gte=day_start)
    if show_time_from:
        show_matches = show_matches.filter(starts_at__time__gte=show_time_from)
    if show_time_to:
        show_matches = show_matches.filter(starts_at__time__lte=show_time_to)
    if city_slug or theater_id or show_day or show_time_from or show_time_to:
        qs = qs.filter(Exists(show_matches))

    qs = qs.distinct()
    matching_count = qs.count()
    sort = request.GET.get("sort", "name")
    if sort == "rating": qs = qs.order_by("-rating", "name", "pk")
    elif sort == "release": qs = qs.order_by("-release_date", "name", "pk")
    elif sort == "popular": qs = qs.annotate(popularity=Count("shows__bookings", filter=Q(shows__bookings__status=Booking.CONFIRMED, shows__bookings__payments__status=Payment.CAPTURED), distinct=True)).order_by("-popularity", "name", "pk")
    elif sort in {"price", "price_asc"}: qs = qs.annotate(next_price=Min("shows__price", filter=Q(shows__is_active=True, shows__starts_at__gte=now))).order_by("next_price", "name", "pk")
    elif sort == "price_desc": qs = qs.annotate(next_price=Min("shows__price", filter=Q(shows__is_active=True, shows__starts_at__gte=now))).order_by("-next_price", "name", "pk")
    else: qs = qs.order_by("name", "pk")

    page = Paginator(qs, 12).get_page(request.GET.get("page"))
    theaters = Theater.objects.filter(shows__is_active=True, shows__starts_at__gte=now).distinct().order_by("name")
    query_params = request.GET.copy()
    query_params.pop("page", None)
    context = {
        "movies": page,
        "page_obj": page,
        "matching_count": matching_count,
        "genres": Genre.objects.all(),
        "languages": Language.objects.all(),
        "cities": City.objects.all(),
        "theaters": theaters,
        "selected_genres": [str(value) for value in genre_ids],
        "selected_languages": [str(value) for value in language_ids],
        "selected_show_time_from": (show_time_from or "").strftime("%H:%M") if show_time_from else "",
        "selected_show_time_to": (show_time_to or "").strftime("%H:%M") if show_time_to else "",
        "pagination_query": query_params.urlencode(),
    }
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        results_html = render_to_string("movies/_movie_list_results.html", context, request=request)
        return JsonResponse({"html": results_html, "matching_count": matching_count, "page": page.number})
    return render(request, "movies/movie_list.html", context)


def movie_detail(request, movie_id):
    from django.db.models import Count, Q
    from datetime import timedelta
    movie = get_object_or_404(Movie.objects.prefetch_related("genres", "languages", "cast_members", "posters", "reviews__user"), id=movie_id)
    if request.user.is_authenticated:
        viewed, created = RecentlyViewedMovie.objects.get_or_create(user=request.user, movie=movie)
        if not created:
            RecentlyViewedMovie.objects.filter(pk=viewed.pk).update(last_viewed_at=timezone.now(), views=F("views") + 1)
        else:
            keep_ids = list(
                RecentlyViewedMovie.objects.filter(user=request.user)
                .order_by("-last_viewed_at", "-pk").values_list("pk", flat=True)[:30]
            )
            RecentlyViewedMovie.objects.filter(user=request.user).exclude(pk__in=keep_ids).delete()
    candidate_reviews = list(movie.reviews.filter(is_hidden=False).select_related("user"))
    review_user_ids = {review.user_id for review in candidate_reviews}
    eligible_users = set()
    for paid_booking in Booking.objects.filter(user_id__in=review_user_ids, movie=movie, status=Booking.CONFIRMED, payments__status=Payment.CAPTURED, show__starts_at__isnull=False).select_related("show", "movie").distinct():
        if paid_booking.show.starts_at + timedelta(minutes=paid_booking.movie.duration_minutes) <= timezone.now():
            eligible_users.add(paid_booking.user_id)
    # Public policy: only reviews by users with a paid, completed show are shown
    # (same rule as the aggregate in signals.refresh_movie_ratings). Ineligible
    # legacy reviews stay stored but are neither displayed nor aggregated.
    reviews = [review for review in candidate_reviews if review.user_id in eligible_users]
    for review in reviews:
        review.is_verified_viewer = True
    similar = Movie.objects.filter(Q(genres__in=movie.genres.all()) | Q(languages__in=movie.languages.all())).exclude(pk=movie.pk).distinct().order_by("-rating", "name", "pk")[:8]
    trending = Movie.objects.filter(shows__bookings__status=Booking.CONFIRMED, shows__bookings__payments__status=Payment.CAPTURED, shows__bookings__booked_at__gte=timezone.now()-timedelta(days=30)).exclude(pk=movie.pk).annotate(recent_bookings=Count("shows__bookings", distinct=True)).order_by("-recent_bookings", "name", "pk")[:8]
    recent = Movie.objects.filter(release_date__isnull=False).exclude(pk=movie.pk).order_by("-release_date", "name", "pk")[:8]
    personalized = recommendations_for_user(request.user, exclude_movie_id=movie.pk)
    shows = movie.shows.filter(is_active=True, starts_at__gte=timezone.now()).select_related("theater", "screen").order_by("starts_at")
    return render(request, "movies/movie_detail.html", {"movie": movie, "reviews": reviews, "similar": similar, "trending": trending, "recent": recent, "personalized": personalized, "shows": shows})


def theater_list(request, movie_id):
    movie = get_object_or_404(Movie, id=movie_id)
    shows = movie.shows.filter(is_active=True, starts_at__gte=timezone.now()).select_related("theater", "screen").order_by("starts_at")
    return render(request, "movies/theater_list.html", {"movie": movie, "shows": shows, "theaters": movie.theaters.all()})


@login_required
@require_GET
def seat_selection(request, show_id):
    show = get_object_or_404(Show.objects.select_related("theater", "screen", "movie"), id=show_id, is_active=True)
    if not show_is_bookable(show):
        messages.error(request, "Booking is closed: this show has already started.")
        return redirect("movie_detail", movie_id=show.movie_id)
    seats = Seat.objects.filter(screen=show.screen).order_by("seat_number")
    if not show.show_seats.exists():
        with transaction.atomic():
            for seat in seats:
                ShowSeat.objects.get_or_create(show=show, seat=seat)
    return render(request, "movies/seat_selection.html", {"show": show, "theater": show.theater, "seats": seats, "show_seats": show.show_seats.select_related("seat")})


@login_required
@require_POST
def reserve_seats(request, show_id):
    show = get_object_or_404(Show, id=show_id, is_active=True)
    if not show_is_bookable(show):
        return JsonResponse({"error": "Booking is closed: this show has already started."}, status=409)
    ids = request.POST.getlist("seats")
    if not ids or len(ids) > MAX_SEATS_PER_BOOKING or len(set(ids)) != len(ids):
        return JsonResponse({"error": "Choose between 1 and 10 different seats."}, status=400)
    try: seat_ids = sorted({int(i) for i in ids})
    except ValueError: return JsonResponse({"error": "Invalid seat selection."}, status=400)
    try:
        with transaction.atomic():
            # Serialize this user's first reservation for a show as well as
            # edits to an existing one. Without a stable row to lock before
            # the lookup, two simultaneous requests for disjoint seats could
            # both observe no pending booking and create separate bookings.
            get_user_model().objects.select_for_update().get(pk=request.user.pk)
            # Keep the lock query on one Booking row per result. Joining
            # BookingSeat/ShowSeat can duplicate a booking when it owns
            # multiple held seats; PostgreSQL rejects DISTINCT together with
            # SELECT FOR UPDATE. EXISTS preserves the intended "owns at least
            # one active held seat" predicate without multiplying outer rows.
            owned_active_seat = BookingSeat.objects.filter(
                booking_id=OuterRef("pk"),
                show_seat__status=ShowSeat.HELD,
                show_seat__held_by=request.user,
            )
            owned = list(Booking.objects.select_for_update().filter(
                user=request.user, show=show, status=Booking.PENDING,
            ).filter(Exists(owned_active_seat)).order_by("pk"))
            if len(owned) > 1:
                return JsonResponse({"error": "More than one active reservation exists for this show. Use booking history to resolve them."}, status=409)
            booking = owned[0] if owned else None
            if booking:
                unresolved = _lock_unresolved_payment(booking)
                if unresolved:
                    return _payment_resolution_conflict(unresolved, "change the held seats")
            old_lines = list(booking.booking_seats.select_related("show_seat") if booking else [])
            lock_ids = set(seat_ids) | {line.show_seat.seat_id for line in old_lines}
            rows = list(ShowSeat.objects.select_for_update().filter(show=show, seat_id__in=lock_ids).select_related("seat").order_by("seat_id"))
            if len([row for row in rows if row.seat_id in seat_ids]) != len(seat_ids): return JsonResponse({"error": "One or more seats are not available for this show."}, status=400)
            row_by_seat = {row.seat_id: row for row in rows}
            # Every blocking lock (user, booking, seats) is now held. Re-read the show
            # and re-evaluate the cutoff with a fresh clock before mutating anything;
            # the check made before the transaction may be arbitrarily stale.
            now = timezone.now()
            show = Show.objects.get(pk=show.pk)
            if not show_is_bookable(show, now):
                return JsonResponse({"error": "Booking is closed: this show has already started."}, status=409)
            expires = now + timedelta(seconds=settings.SEAT_HOLD_SECONDS)
            conflicts = [r.seat.seat_number for r in rows if r.status == ShowSeat.BOOKED or (r.status == ShowSeat.HELD and (r.active_booking_id != (booking.pk if booking else None) or r.held_by_id != request.user.id) and r.hold_expires_at and r.hold_expires_at > now)]
            if conflicts: return JsonResponse({"error": "Some seats are no longer available.", "conflicts": conflicts}, status=409)
            active_old = [line for line in old_lines if line.show_seat.status == ShowSeat.HELD and line.show_seat.active_booking_id == booking.pk and line.show_seat.held_by_id == request.user.id and line.show_seat.hold_expires_at and line.show_seat.hold_expires_at > now]
            if booking and active_old:
                # Final-selection limit: validate the union with the seats already
                # held BEFORE any row is changed.
                final_seat_ids = set(seat_ids) | {line.show_seat.seat_id for line in active_old}
                if len(final_seat_ids) > MAX_SEATS_PER_BOOKING:
                    return JsonResponse({"error": f"A booking can hold at most {MAX_SEATS_PER_BOOKING} seats; this request would make it {len(final_seat_ids)}.", "status": "seat_limit_exceeded"}, status=400)
            if booking and not active_old:
                for line in old_lines:
                    row = row_by_seat.get(line.show_seat.seat_id)
                    if row and row.status == ShowSeat.HELD and row.active_booking_id == booking.pk and row.held_by_id == request.user.id:
                        row.status, row.held_by, row.hold_expires_at, row.active_booking = ShowSeat.AVAILABLE, None, None, None
                        row.save(update_fields=["status", "held_by", "hold_expires_at", "active_booking"])
                booking.booking_seats.all().delete()
                Payment.objects.filter(booking=booking, status=Payment.CREATED).update(status=Payment.FAILED)
                booking.status = Booking.FAILED
                booking.save(update_fields=["status"])
                booking = None
            active_expiry = min((line.show_seat.hold_expires_at for line in active_old), default=expires)
            if booking:
                # A reservation request adds seats to this user's active
                # hold. In particular, requests serialized behind the user
                # row lock must not discard seats added by the request that
                # committed first. Intentional removals use
                # modify_reservation(), which handles release/payment state.
                active_old_seat_ids = {line.show_seat.seat_id for line in active_old}
                wanted = set(seat_ids) | active_old_seat_ids
                old_seat_ids = {line.show_seat.seat_id for line in old_lines}
                old_amount = booking.total_amount
                for line in old_lines:
                    if line.show_seat.seat_id not in active_old_seat_ids:
                        row = row_by_seat.get(line.show_seat.seat_id)
                        if row and row.status == ShowSeat.HELD and row.active_booking_id == booking.pk and row.held_by_id == request.user.pk:
                            row.status, row.held_by, row.hold_expires_at, row.active_booking = ShowSeat.AVAILABLE, None, None, None
                            row.save(update_fields=["status", "held_by", "hold_expires_at", "active_booking"])
                        line.delete()
                current_lines = {line.show_seat.seat_id for line in booking.booking_seats.all()}
                for seat_id in wanted:
                    row = row_by_seat[seat_id]
                    if row.status != ShowSeat.HELD or row.held_by_id != request.user.id:
                        row.status, row.held_by, row.hold_expires_at, row.active_booking = ShowSeat.HELD, request.user, active_expiry, booking
                        row.save(update_fields=["status", "held_by", "hold_expires_at", "active_booking"])
                    if seat_id not in current_lines:
                        BookingSeat.objects.create(booking=booking, show_seat=row, price=show.price)
                booking.total_amount = booking.booking_seats.aggregate(total=Sum("price"))["total"] or Decimal("0.00")
                booking.save(update_fields=["total_amount"])
                if old_seat_ids != wanted or old_amount != booking.total_amount:
                    Payment.objects.filter(booking=booking, status=Payment.CREATED).update(status=Payment.FAILED)
                expires = active_expiry
            else:
                booking = Booking.objects.create(user=request.user, movie=show.movie, theater=show.theater, show=show, status=Booking.PENDING, total_amount=show.price * len(seat_ids))
                for seat_id in seat_ids:
                    row = row_by_seat[seat_id]
                    row.status, row.held_by, row.hold_expires_at, row.active_booking = ShowSeat.HELD, request.user, expires, booking
                    row.save(update_fields=["status", "held_by", "hold_expires_at", "active_booking"])
                BookingSeat.objects.bulk_create([BookingSeat(booking=booking, show_seat=row_by_seat[sid], price=show.price) for sid in seat_ids])
                booking.total_amount = booking.booking_seats.aggregate(total=Sum("price"))["total"] or Decimal("0.00")
                booking.save(update_fields=["total_amount"])
    except IntegrityError:
        return JsonResponse({"error": "Seat reservation conflicted. Refresh availability and retry."}, status=409)
    return JsonResponse({"booking": str(booking.reference), "expires_at": expires.isoformat(), "status": "held"}, status=201)


@login_required
@require_POST
def modify_reservation(request, booking_ref):
    """Replace the selected seats of this user's pending booking.

    Policy for partially expired reservations: the reservation is modifiable only
    while at least one of its seats is still actively held. Every seat in the new
    selection is then either kept (active hold) or safely RE-ACQUIRED (an expired
    hold of this booking/user, or an expired/free seat) with the reservation's
    remaining window; seats not selected are released only if this booking still
    owns them. Conflicts and an elapsed cutoff are detected before any mutation,
    so the whole operation is all-or-nothing. Saved BookingSeat price snapshots
    are never rewritten; only newly added seats take the current show price.
    Lock order: Booking -> ShowSeat (ascending seat_id).
    """
    booking = get_object_or_404(Booking, reference=booking_ref, user=request.user, status=Booking.PENDING)
    try: wanted = sorted(set(map(int, request.POST.getlist("seats"))))
    except ValueError: return JsonResponse({"error": "Invalid seat selection."}, status=400)
    if not wanted or len(wanted) > MAX_SEATS_PER_BOOKING: return JsonResponse({"error": f"Choose between 1 and {MAX_SEATS_PER_BOOKING} seats."}, status=400)
    if not show_is_bookable(booking.show, timezone.now()):  # cheap early rejection; re-checked under lock
        return JsonResponse({"error": "Booking is closed: this show has already started."}, status=409)
    with transaction.atomic():
        booking = Booking.objects.select_for_update().get(pk=booking.pk)
        unresolved = _lock_unresolved_payment(booking)
        if unresolved:
            return _payment_resolution_conflict(unresolved, "change the held seats")
        show = Show.objects.get(pk=booking.show_id)
        old_lines = list(booking.booking_seats.select_related("show_seat"))
        old_seat_ids = {line.show_seat.seat_id for line in old_lines}
        rows = list(ShowSeat.objects.select_for_update().filter(show=show, seat_id__in=set(wanted) | old_seat_ids).select_related("seat").order_by("seat_id"))
        row_by_seat = {row.seat_id: row for row in rows}
        # Blocking locks are held; take a fresh clock and re-read the status.
        now = timezone.now()
        if booking.status != Booking.PENDING:
            return JsonResponse({"error": "This booking is no longer pending."}, status=409)
        if not show_is_bookable(show, now):
            return JsonResponse({"error": "Booking is closed: this show has already started."}, status=409)
        if any(seat_id not in row_by_seat for seat_id in wanted):
            return JsonResponse({"error": "Invalid seat for this show."}, status=400)

        def owned_active(row):
            return (row.status == ShowSeat.HELD and row.active_booking_id == booking.pk and row.held_by_id == request.user.id
                    and row.hold_expires_at is not None and row.hold_expires_at > now)

        active_expiries = [row_by_seat[sid].hold_expires_at for sid in old_seat_ids if sid in row_by_seat and owned_active(row_by_seat[sid])]
        if not active_expiries:
            return JsonResponse({"error": "Reservation expired; start again."}, status=409)
        conflicts = [row_by_seat[sid].seat.seat_number for sid in wanted
                     if row_by_seat[sid].status == ShowSeat.BOOKED
                     or (row_by_seat[sid].status == ShowSeat.HELD and row_by_seat[sid].active_booking_id != booking.pk
                         and row_by_seat[sid].hold_expires_at and row_by_seat[sid].hold_expires_at > now)]
        if conflicts:
            return JsonResponse({"error": "Some seats are no longer available.", "conflicts": conflicts}, status=409)
        # ---- all checks passed; mutate ----
        window = min(active_expiries)  # keep the original reservation window; never extend it
        old_amount = booking.total_amount
        for line in old_lines:
            seat_id = line.show_seat.seat_id
            if seat_id in wanted:
                continue
            row = row_by_seat[seat_id]
            if row.status == ShowSeat.HELD and row.active_booking_id == booking.pk:
                row.status, row.held_by, row.hold_expires_at, row.active_booking = ShowSeat.AVAILABLE, None, None, None
                row.save(update_fields=["status", "held_by", "hold_expires_at", "active_booking"])
            line.delete()
        for seat_id in wanted:
            row = row_by_seat[seat_id]
            if not owned_active(row):
                row.status, row.held_by, row.hold_expires_at, row.active_booking = ShowSeat.HELD, request.user, window, booking
                row.save(update_fields=["status", "held_by", "hold_expires_at", "active_booking"])
            BookingSeat.objects.get_or_create(booking=booking, show_seat=row, defaults={"price": show.price})
        booking.total_amount = booking.booking_seats.aggregate(total=Sum("price"))["total"] or Decimal("0.00")
        booking.save(update_fields=["total_amount"])
        if old_seat_ids != set(wanted) or old_amount != booking.total_amount:
            Payment.objects.filter(booking=booking, status=Payment.CREATED).update(status=Payment.FAILED)
    return JsonResponse({"status": "held", "seats": wanted, "expires_at": window.isoformat()})


@login_required
@require_GET
def seat_availability(request, show_id):
    show = get_object_or_404(Show, id=show_id)
    now = timezone.now()
    data = []
    for row in show.show_seats.select_related("seat").order_by("seat__seat_number"):
        status = row.status
        if status == ShowSeat.HELD and (not row.hold_expires_at or row.hold_expires_at <= now): status = ShowSeat.AVAILABLE
        data.append({"seat_id": row.seat_id, "number": row.seat.seat_number, "status": status, "mine": status == ShowSeat.HELD and row.held_by_id == request.user.id})
    return JsonResponse({"show": show.id, "seats": data, "server_time": now.isoformat()})


def _holds_valid(booking, user_id, now):
    """True when every seat line is still held by this booking/user and unexpired."""
    lines = list(booking.booking_seats.select_related("show_seat"))
    return bool(lines) and all(
        line.show_seat.status == ShowSeat.HELD and line.show_seat.active_booking_id == booking.pk
        and line.show_seat.held_by_id == user_id and line.show_seat.hold_expires_at
        and line.show_seat.hold_expires_at > now for line in lines)


def _lock_unresolved_payment(booking):
    """Lock payments that make seat changes unsafe; call after locking Booking."""
    return Payment.objects.select_for_update().filter(booking=booking).filter(
        Q(status=Payment.AUTHORIZED)
        | Q(status=Payment.CREATED, order_state__in=[Payment.ORDER_CREATING, Payment.ORDER_UNCERTAIN])
    ).order_by("pk").first()


def _payment_resolution_conflict(payment, action):
    if payment.status == Payment.AUTHORIZED:
        message = (
            f"A payment ({payment.transaction_id or payment.provider_order_id}) is authorized and may still be captured. "
            f"You cannot {action} until the provider confirms capture/failure or support completes reconciliation."
        )
        status = "payment_authorization_unresolved"
    elif payment.order_state == Payment.ORDER_UNCERTAIN:
        message = f"A previous payment order has an unknown outcome. You cannot {action} until reconciliation or manual review completes."
        status = "order_uncertain_manual_review"
    else:
        message = f"A payment order is still being created. You cannot {action} until that request resolves. Please retry shortly."
        status = "order_creation_in_progress"
    response = JsonResponse({"error": message, "status": status}, status=409)
    if status == "order_creation_in_progress":
        response["Retry-After"] = "2"
    return response


_RAZORPAY_CONFIG_ID_RE = re.compile(r"^config_[A-Za-z0-9]{6,40}$")


def _order_payload(payment):
    payload = {"key": settings.RAZORPAY_KEY_ID, "order_id": payment.provider_order_id, "amount": int(payment.amount * 100),
               "currency": payment.currency, "payment_id": payment.id,
               # Display-only hint so the page can show Test Mode guidance. Derived from the public key id.
               "test_mode": str(settings.RAZORPAY_KEY_ID or "").startswith("rzp_test_")}
    # Only a validated public configuration ID is sent to the browser; secrets are never included.
    # Configurations can customize methods already enabled on the account; they cannot enable account-level methods.
    config_id = str(getattr(settings, "RAZORPAY_CONFIG_ID", "") or "").strip()
    if config_id and _RAZORPAY_CONFIG_ID_RE.fullmatch(config_id):
        payload["checkout_config_id"] = config_id
    elif config_id:
        logger.warning("RAZORPAY_CONFIG_ID is set but malformed; ignoring it and using the account default.")
    return payload


@login_required
@require_POST
def create_payment_order(request, booking_ref):
    """Create (or reuse) a provider order without holding a DB transaction open
    across the network call.

    Lock order: Booking -> Payment -> ShowSeat. Durable state lives in
    Payment.order_state (creating -> created | failed | uncertain). A partial
    unique index allows at most one in-flight ("creating") row per booking, and
    the Booking row lock makes the check-then-insert atomic, so a concurrent
    second request reuses the finished order or receives a retryable 409.
    """
    booking = get_object_or_404(Booking, reference=booking_ref, user=request.user, status=Booking.PENDING)
    in_progress = JsonResponse({"error": "A payment order is already being created for this booking. Retry shortly.", "status": "order_creation_in_progress"}, status=409)
    in_progress["Retry-After"] = "2"
    with transaction.atomic():
        booking = Booking.objects.select_for_update().get(pk=booking.pk)
        now = timezone.now()
        if booking.status != Booking.PENDING:
            return JsonResponse({"error": "This booking is no longer pending."}, status=409)
        authorized = Payment.objects.select_for_update().filter(booking=booking, status=Payment.AUTHORIZED).order_by("pk").first()
        if authorized:
            return _payment_resolution_conflict(authorized, "start another checkout")
        if not show_is_bookable(booking.show, now):
            _release_booking(booking, Booking.FAILED)
            Payment.objects.filter(booking=booking, status=Payment.CREATED).update(status=Payment.FAILED)
            return JsonResponse({"error": "Booking is closed: this show has already started."}, status=409)
        if not _holds_valid(booking, request.user.id, now):
            _release_booking(booking, Booking.FAILED)
            return JsonResponse({"error": "Reservation expired. Please select seats again."}, status=409)
        Payment.objects.filter(booking=booking, status=Payment.CREATED).exclude(amount=booking.total_amount).update(status=Payment.FAILED)
        open_payments = list(Payment.objects.select_for_update().filter(booking=booking, status=Payment.CREATED, amount=booking.total_amount).order_by("pk"))
        ready = next((p for p in open_payments if p.order_state == Payment.ORDER_CREATED and p.provider_order_id), None)
        if ready:
            return JsonResponse(_order_payload(ready))
        stale_before = now - timedelta(seconds=getattr(settings, "PAYMENT_ORDER_INFLIGHT_SECONDS", 60))
        for p in open_payments:
            if p.order_state == Payment.ORDER_CREATING and p.created_at <= stale_before:
                # The worker may have died after the provider accepted the call.
                p.order_state = Payment.ORDER_UNCERTAIN
                p.save(update_fields=["order_state", "updated_at"])
        if any(p.order_state == Payment.ORDER_UNCERTAIN for p in open_payments):
            return JsonResponse({"error": "A previous payment order has an unknown outcome and needs reconciliation. Do not retry payment yet.", "status": "order_uncertain_manual_review"}, status=409)
        if any(p.order_state == Payment.ORDER_CREATING for p in open_payments):
            return in_progress
        if not settings.RAZORPAY_KEY_ID or not settings.RAZORPAY_KEY_SECRET:
            payment = next((p for p in open_payments if p.order_state == Payment.ORDER_FAILED and not p.provider_order_id), None) or Payment.objects.create(
                booking=booking, amount=booking.total_amount, idempotency_key=str(uuid.uuid4()), order_state=Payment.ORDER_FAILED)
            return JsonResponse({"error": "Payment provider is not configured.", "payment": payment.id}, status=503)
        # A row whose order creation definitively failed and has no provider order can
        # never be paid: retire it so it is not shown as an open "Created" payment.
        Payment.objects.filter(booking=booking, status=Payment.CREATED, order_state=Payment.ORDER_FAILED, provider_order_id="").update(status=Payment.FAILED)
        try:
            with transaction.atomic():
                payment = Payment.objects.create(booking=booking, amount=booking.total_amount, idempotency_key=str(uuid.uuid4()), order_state=Payment.ORDER_CREATING)
        except IntegrityError:
            return in_progress
        amount_paise = int(payment.amount * 100)
        receipt = payment.idempotency_key
    # --- No transaction is open here. The local row (state "creating") is committed. ---
    try:
        import razorpay
        client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
        order = client.order.create({"amount": amount_paise, "currency": "INR", "receipt": receipt})
        order_id = order["id"]
    except Exception as exc:
        # Only an explicit provider rejection is a definite failure. Timeouts,
        # transport errors and 5xx responses may follow a created order.
        definite = exc.__class__.__name__ == "BadRequestError"
        logger.exception("Razorpay order creation failed for payment %s (definite=%s)", payment.pk, definite)
        with transaction.atomic():
            locked_booking = Booking.objects.select_for_update().get(pk=booking.pk)
            locked = Payment.objects.select_for_update().get(pk=payment.pk)
            if locked.order_state == Payment.ORDER_CREATING and not locked.provider_order_id:
                if definite:
                    locked.order_state, locked.status = Payment.ORDER_FAILED, Payment.FAILED
                else:
                    # Timeout/transport error: the provider may have created the order.
                    # Never guess; leave it for reconcile_uncertain_payment_orders.
                    locked.order_state = Payment.ORDER_UNCERTAIN
                locked.save(update_fields=["order_state", "status", "updated_at"])
        if definite:
            return JsonResponse({"error": "Unable to create payment order."}, status=502)
        return JsonResponse({"error": "The payment provider did not confirm the order. It will be reconciled; do not pay yet.", "status": "order_uncertain_manual_review"}, status=502)
    # Revalidate under locks (Booking -> Payment -> ShowSeat) before returning the order.
    with transaction.atomic():
        locked_booking = Booking.objects.select_for_update().get(pk=booking.pk)
        locked = Payment.objects.select_for_update().get(pk=payment.pk)
        now = timezone.now()
        usable = (locked.status == Payment.CREATED and locked.order_state == Payment.ORDER_CREATING
                  and locked_booking.status == Booking.PENDING and locked.amount == locked_booking.total_amount
                  and show_is_bookable(locked_booking.show, now) and _holds_valid(locked_booking, request.user.id, now))
        # Always persist the provider order id so a later capture for it is matched
        # and routed to refund recovery instead of being orphaned.
        locked.provider_order_id, locked.order_state = order_id, Payment.ORDER_CREATED
        if not usable and locked.status == Payment.CREATED:
            locked.status = Payment.FAILED
        locked.save(update_fields=["provider_order_id", "order_state", "status", "updated_at"])
        if not usable:
            return JsonResponse({"error": "Reservation changed while creating payment order. Please retry."}, status=409)
        return JsonResponse(_order_payload(locked))


@login_required
@require_POST
def verify_payment(request, payment_id):
    """Verify the Checkout callback with HMAC and Razorpay's provider-side payment state."""
    payment = get_object_or_404(Payment.objects.select_related("booking"), id=payment_id, booking__user=request.user)
    data = request.POST
    if not settings.RAZORPAY_KEY_ID or not settings.RAZORPAY_KEY_SECRET:
        return JsonResponse({"error": "Payment provider is not configured.", "status": "provider_not_configured"}, status=503)
    provider_payment_id = data.get("razorpay_payment_id", "")
    signature = data.get("razorpay_signature", "")
    message = f"{payment.provider_order_id}|{provider_payment_id}".encode()
    expected = hmac.new(settings.RAZORPAY_KEY_SECRET.encode(), message, hashlib.sha256).hexdigest()
    if (not provider_payment_id or not signature or not hmac.compare_digest(signature, expected)
            or data.get("razorpay_order_id") != payment.provider_order_id):
        return JsonResponse({"error": "Payment verification failed.", "status": "signature_invalid"}, status=400)
    try:
        import razorpay
        provider_payment = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET)).payment.fetch(provider_payment_id)
        matches = (provider_payment.get("order_id") == payment.provider_order_id and
                   int(provider_payment.get("amount", -1)) == int(payment.amount * 100) and
                   provider_payment.get("currency") == payment.currency)
        provider_status = provider_payment.get("status")
    except Exception:
        logger.exception("Razorpay payment lookup failed for payment %s", payment.pk)
        return JsonResponse({"error": "Payment status could not be verified.", "status": "verification_unavailable"}, status=502)
    if not matches:
        return JsonResponse({"error": "Payment does not match this booking.", "status": "payment_mismatch"}, status=409)
    if provider_status == "failed":
        return JsonResponse({"error": "Razorpay reports this payment as failed.", "status": "payment_failed"}, status=409)
    if provider_status != "captured":
        return JsonResponse({"error": "Payment is not captured yet.", "status": "payment_not_captured",
                             "provider_status": str(provider_status)}, status=409)
    return _confirm_payment(payment, provider_payment_id)


def _lock_booking_then_payment(payment_id):
    """Documented lock order: Booking -> Payment (-> ShowSeat by pk). Must be
    called inside transaction.atomic(). Payment.booking is immutable, so the
    unlocked read of booking_id is safe."""
    booking_id = Payment.objects.values_list("booking_id", flat=True).get(pk=payment_id)
    booking = Booking.objects.select_for_update().get(pk=booking_id)
    payment = Payment.objects.select_for_update().get(pk=payment_id)
    return payment, booking


def _confirm_payment(payment, txn_id):
    """Payment state machine (capture handling).

    CREATED/AUTHORIZED/FAILED/CANCELLED --capture--> CAPTURED (confirm or late-refund)
    CAPTURED  --same txn--> no-op;  --different txn--> duplicate-charge recovery
    REFUNDED  --same txn--> no-op (final state, never reopened);  --different txn--> duplicate-charge recovery
    """
    with transaction.atomic():
        payment, booking = _lock_booking_then_payment(payment.pk)
        if payment.status == Payment.REFUNDED:
            if txn_id and payment.transaction_id and payment.transaction_id != txn_id:
                return _record_duplicate_capture(payment, booking, txn_id)
            return JsonResponse({"status": "already_refunded", "booking": str(booking.reference)})
        if payment.status == Payment.CAPTURED:
            if txn_id and payment.transaction_id and payment.transaction_id != txn_id:
                return _record_duplicate_capture(payment, booking, txn_id)
            state = "confirmed" if booking.status == Booking.CONFIRMED else "late_payment_refund_pending"
            return JsonResponse({"status": state, "booking": str(booking.reference)}, status=200 if state == "confirmed" else 409)
        if txn_id and payment.transaction_id and payment.transaction_id != txn_id:
            return _record_duplicate_capture(payment, booking, txn_id)
        lines = list(booking.booking_seats.select_related("show_seat"))
        locked = {row.pk: row for row in ShowSeat.objects.select_for_update().filter(pk__in=[line.show_seat_id for line in lines]).order_by("seat_id")}
        for line in lines:
            line.show_seat = locked[line.show_seat_id]
        now = timezone.now()
        show_open = show_is_bookable(booking.show, now)
        holds_valid = lines and all(line.show_seat.status == ShowSeat.HELD and line.show_seat.active_booking_id == booking.pk and line.show_seat.held_by_id == booking.user_id and line.show_seat.hold_expires_at and line.show_seat.hold_expires_at > now for line in lines)
        payment_valid = payment.status in {Payment.CREATED, Payment.AUTHORIZED} and booking.status == Booking.PENDING and payment.amount == booking.total_amount and show_open
        payment.transaction_id = txn_id
        payment.status = Payment.CAPTURED
        payment.captured_at = timezone.now()
        payment.save(update_fields=["transaction_id", "status", "captured_at", "updated_at"])
        if not holds_valid or not payment_valid:
            if booking.status == Booking.PENDING and (not holds_valid or not payment_valid):
                _release_booking(booking, Booking.FAILED)
            payment.refund_status = "pending"
            payment.refund_reason = "show_started" if not show_open else "late_payment"
            payment.save(update_fields=["refund_status", "refund_reason", "updated_at"])
            transaction.on_commit(lambda: _enqueue_late_refund(payment.pk))
            logger.error("Late captured payment requires refund or manual recovery: payment=%s", payment.pk)
            return JsonResponse({"status": "late_payment_refund_required"}, status=409)
        for line in lines:
            line.show_seat.status, line.show_seat.held_by, line.show_seat.hold_expires_at = ShowSeat.BOOKED, None, None
            line.show_seat.save(update_fields=["status", "held_by", "hold_expires_at"])
        booking.status = Booking.CONFIRMED
        booking.save(update_fields=["status"])
        transaction.on_commit(lambda: _enqueue_ticket(booking.pk))
    return JsonResponse({"status": "confirmed", "booking": str(booking.reference)})


def _record_duplicate_capture(payment, booking, txn_id):
    key = "duplicate-" + hashlib.sha256(txn_id.encode()).hexdigest()[:60]
    duplicate, created = Payment.objects.get_or_create(
        transaction_id=txn_id,
        defaults={"booking": booking, "amount": payment.amount, "currency": payment.currency,
                  "status": Payment.CAPTURED, "refund_status": "pending", "refund_reason": "duplicate_charge",
                  "captured_at": timezone.now(), "idempotency_key": key},
    )
    if duplicate.booking_id != booking.pk or duplicate.amount != payment.amount:
        return JsonResponse({"error": "Provider payment identifier conflicts with an existing transaction."}, status=409)
    if created:
        transaction.on_commit(lambda: _enqueue_late_refund(duplicate.pk))
    return JsonResponse({"status": "duplicate_charge_refund_pending"}, status=409)


def _enqueue_ticket(booking_id):
    try:
        from .tasks import send_booking_ticket
        send_booking_ticket.delay(booking_id)
    except Exception:
        logger.exception("Ticket delivery enqueue failed for booking %s", booking_id)


def _enqueue_late_refund(payment_id):
    try:
        from .tasks import refund_late_payment
        refund_late_payment.delay(payment_id)
    except Exception:
        logger.exception("Late-payment refund enqueue failed for payment %s", payment_id)


def _enqueue_refund(payment_id):
    try:
        from .tasks import refund_late_payment
        refund_late_payment.delay(payment_id)
    except Exception:
        logger.exception("Refund enqueue failed for payment %s", payment_id)
        Payment.objects.filter(pk=payment_id, refund_status="requested").update(refund_status="manual_required")


def _release_booking(booking, status):
    for line in booking.booking_seats.select_related("show_seat"):
        row = line.show_seat
        if row.status == ShowSeat.HELD and row.active_booking_id == booking.pk:
            row.status, row.held_by, row.hold_expires_at, row.active_booking = ShowSeat.AVAILABLE, None, None, None
            row.save(update_fields=["status", "held_by", "hold_expires_at", "active_booking"])
    booking.status = status
    booking.save(update_fields=["status"])


def _mark_payment_refunded(payment_id, refund_id):
    if not refund_id: raise ValueError("Provider refund identifier is missing")
    with transaction.atomic():
        payment, booking = _lock_booking_then_payment(payment_id)
        if payment.status == Payment.REFUNDED:
            if payment.refund_id != refund_id: raise ValueError("Conflicting refund identifier")
            return
        if payment.status != Payment.CAPTURED: raise ValueError("Only captured payments can be refunded")
        payment.status, payment.refund_id, payment.refund_status = Payment.REFUNDED, refund_id, "processed"
        payment.refunded_at = timezone.now()
        payment.save(update_fields=["status", "refund_id", "refund_status", "refunded_at", "updated_at"])
        if payment.refund_reason != "duplicate_charge":
            booking.status = Booking.REFUNDED
            booking.save(update_fields=["status"])
            rows = ShowSeat.objects.select_for_update().filter(active_booking=booking).order_by("seat_id")
            for row in rows:
                row.status, row.held_by, row.hold_expires_at, row.active_booking = ShowSeat.AVAILABLE, None, None, None
                row.save(update_fields=["status", "held_by", "hold_expires_at", "active_booking"])


@csrf_exempt
@require_POST
def razorpay_webhook(request):
    secret = settings.RAZORPAY_WEBHOOK_SECRET
    signature = request.headers.get("X-Razorpay-Signature", "")
    expected = hmac.new(secret.encode(), request.body, hashlib.sha256).hexdigest() if secret else ""
    if not secret or not hmac.compare_digest(signature, expected): return HttpResponseForbidden("Invalid webhook signature")
    if len(request.body) > 1024 * 1024: return JsonResponse({"error": "Event too large"}, status=413)
    try:
        data = json.loads(request.body)
        event_id = request.headers["X-Razorpay-Event-Id"].strip()
        if not event_id or len(event_id) > 180 or not isinstance(data, dict): raise ValueError
    except (ValueError, KeyError, AttributeError): return JsonResponse({"error": "Invalid event"}, status=400)
    event, _ = PaymentWebhookEvent.objects.get_or_create(event_id=event_id, defaults={"event_type": str(data.get("event", ""))[:100], "payload": data})
    try:
        with transaction.atomic():
            event = PaymentWebhookEvent.objects.select_for_update().get(pk=event.pk)
            if event.status == "processed": return JsonResponse({"status": "duplicate"})
            event.status = "processing"
            event.event_type = str(data.get("event", ""))[:100]
            event.payload = data
            event.attempts += 1
            event.last_error = ""
            event.save(update_fields=["status", "event_type", "payload", "attempts", "last_error"])
            _process_payment_webhook(data)
            event.status = "processed"
            event.processed_at = timezone.now()
            event.save(update_fields=["status", "processed_at"])
    except Exception as exc:
        logger.exception("Razorpay webhook processing failed for event %s", event_id)
        PaymentWebhookEvent.objects.filter(event_id=event_id).update(status="failed", attempts=F("attempts") + 1, last_error=str(exc)[:1000])
        return JsonResponse({"error": "Webhook processing failed; retry this event."}, status=500)
    return JsonResponse({"status": "accepted"})


def _process_payment_webhook(data):
    event_name = data.get("event", "")
    payload = data.get("payload", {})
    entity = payload.get("payment", {}).get("entity", {})
    if event_name == "order.paid" and entity:
        # Razorpay sends the captured payment entity inside order.paid. Route it through the
        # same validated capture path (amount/currency/status checks, idempotent confirmation).
        event_name = "payment.captured"
    if event_name in {"payment.captured", "payment.authorized", "payment.failed"}:
        order_id, txn_id = entity.get("order_id"), entity.get("id")
        payment = Payment.objects.filter(provider_order_id=order_id).first()
        if not payment:
            raise ValueError("No local payment matches provider order")
        if entity.get("currency") != payment.currency or int(entity.get("amount", -1)) != int(payment.amount * 100) or not txn_id:
            raise ValueError("Provider payment amount, currency, or identifier mismatch")
        if event_name == "payment.captured":
            if entity.get("status") != "captured": raise ValueError("Capture event is not captured")
            response = _confirm_payment(payment, txn_id)
            if response.status_code >= 500: raise RuntimeError("Payment confirmation failed")
        elif event_name == "payment.authorized":
            with transaction.atomic():
                locked, booking = _lock_booking_then_payment(payment.pk)
                if locked.status in {Payment.CAPTURED, Payment.REFUNDED}:
                    # Webhook payloads describe event-time state and can arrive
                    # after capture/refund. Never move a terminal row backward.
                    return
                if locked.transaction_id and locked.transaction_id != txn_id:
                    raise ValueError("Authorization conflicts with the recorded payment transaction")
                if locked.status in {Payment.CREATED, Payment.AUTHORIZED, Payment.FAILED, Payment.CANCELLED}:
                    # A delayed authorization is provider-authoritative even if
                    # an order was locally retired after its booking changed.
                    # Keep it visible and block further edits/checkout until a
                    # definitive capture/failure event or manual review.
                    locked.status = Payment.AUTHORIZED
                    locked.transaction_id = txn_id
                    locked.save(update_fields=["status", "transaction_id", "updated_at"])
                else:
                    raise ValueError("Authorization conflicts with local payment state")
        else:
            with transaction.atomic():
                locked, booking = _lock_booking_then_payment(payment.pk)
                if (locked.status in {Payment.CREATED, Payment.AUTHORIZED, Payment.FAILED, Payment.CANCELLED}
                        and locked.transaction_id and locked.transaction_id != txn_id):
                    raise ValueError("Payment failure conflicts with the recorded transaction")
                if locked.status in {Payment.CREATED, Payment.AUTHORIZED}:
                    locked.status = Payment.FAILED
                    locked.transaction_id = txn_id
                    locked.save(update_fields=["status", "transaction_id", "updated_at"])
                    if booking.status == Booking.PENDING and not booking.payments.filter(status=Payment.AUTHORIZED).exclude(pk=locked.pk).exists():
                        _release_booking(booking, Booking.FAILED)
                elif locked.status in {Payment.FAILED, Payment.CANCELLED} and not locked.transaction_id:
                    # Retain a late definitive failure event without reopening
                    # a booking or overwriting another transaction identifier.
                    locked.transaction_id = txn_id
                    locked.save(update_fields=["transaction_id", "updated_at"])
    elif event_name in {"refund.processed", "refund.created", "refund.failed"}:
        refund = payload.get("refund", {}).get("entity", {})
        refund_id = str(refund.get("id", ""))
        payment = Payment.objects.filter(transaction_id=refund.get("payment_id", "")).first()
        if not payment: raise ValueError("No local payment matches provider refund")
        if not refund_id: raise ValueError("Provider refund identifier is missing")
        if payment.status == Payment.REFUNDED:
            if event_name == "refund.processed" or refund.get("status") == "processed":
                _mark_payment_refunded(payment.pk, refund_id)
            # Delayed created/failed notifications must not downgrade a final refund.
            return
        if event_name == "refund.failed":
            if payment.status == Payment.CAPTURED and payment.refund_status != "processed" and (not payment.refund_id or payment.refund_id == refund_id):
                Payment.objects.filter(pk=payment.pk, status=Payment.CAPTURED).update(refund_id=refund_id, refund_status="failed")
        elif int(refund.get("amount", -1)) != int(payment.amount * 100):
            if payment.status == Payment.CAPTURED and payment.refund_status != "processed":
                Payment.objects.filter(pk=payment.pk, status=Payment.CAPTURED).update(refund_id=refund_id, refund_status="partial_requires_review")
        elif event_name == "refund.processed" or refund.get("status") == "processed":
            _mark_payment_refunded(payment.pk, refund.get("id", ""))
        else:
            if payment.status == Payment.CAPTURED and payment.refund_status != "processed" and (not payment.refund_id or payment.refund_id == refund_id or payment.refund_status == "failed"):
                Payment.objects.filter(pk=payment.pk, status=Payment.CAPTURED).update(refund_id=refund_id, refund_status="pending")


@login_required
def booking_history(request):
    bookings = Booking.objects.filter(user=request.user).select_related("movie", "theater", "show").prefetch_related("payments", "booking_seats__show_seat__seat").order_by("-booked_at")
    return render(request, "users/profile.html", {"bookings": bookings})


@login_required
@require_GET
def ticket_download(request, booking_ref):
    booking = get_object_or_404(Booking, reference=booking_ref, user=request.user, status=Booking.CONFIRMED)
    if not booking.ticket_file: return HttpResponse("Ticket is not ready", status=404)
    response = HttpResponse(booking.ticket_file.open("rb"), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="ticket-{booking.reference}.pdf"'
    return response


@login_required
@require_POST
def cancel_booking(request, booking_ref):
    def response(payload, status_code):
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse(payload, status=status_code)
        if status_code >= 400:
            messages.error(request, payload.get("error", "The booking action could not be completed."))
        elif status_code == 202:
            messages.info(request, "Your refund request is being processed. The seats remain unavailable until the provider confirms the refund.")
        else:
            messages.success(request, payload.get(
                "message", "Your unpaid reservation was cancelled and its seats were released."
            ))
        return redirect("profile")

    with transaction.atomic():
        booking = get_object_or_404(Booking.objects.select_for_update(), reference=booking_ref, user=request.user)
        unresolved = _lock_unresolved_payment(booking)
        if unresolved:
            blocked = _payment_resolution_conflict(unresolved, "cancel this reservation")
            return response(json.loads(blocked.content), blocked.status_code)
        if booking.status == Booking.PENDING:
            if booking.payments.filter(status__in=[Payment.CAPTURED, Payment.REFUNDED]).exists():
                return response({"error": "Payment is already captured; request a refund instead."}, 409)
            rows = ShowSeat.objects.select_for_update().filter(booking_seats__booking=booking, active_booking=booking, status=ShowSeat.HELD, held_by=request.user).order_by("seat_id")
            for row in rows:
                row.status, row.held_by, row.hold_expires_at, row.active_booking = ShowSeat.AVAILABLE, None, None, None
                row.save(update_fields=["status", "held_by", "hold_expires_at", "active_booking"])
            Payment.objects.filter(booking=booking, status__in=[Payment.CREATED, Payment.AUTHORIZED]).update(status=Payment.CANCELLED)
            booking.status = Booking.CANCELLED
            booking.save(update_fields=["status"])
            show_started = bool(booking.show_id and booking.show.starts_at <= timezone.now())
            message = (
                "The show has started, so this unpaid reservation was closed and its seats were released. "
                "If a payment is later captured by the provider, it will be flagged for refund review."
                if show_started else
                "Your unpaid reservation was cancelled and its seats were released."
            )
            return response({"status": "cancelled", "message": message}, 200)
        retrying_failed_refund = booking.status == Booking.CANCELLATION_PENDING and booking.payments.filter(status=Payment.CAPTURED, refund_status="failed").exists()
        if booking.status != Booking.CONFIRMED and not retrying_failed_refund:
            return response({"error": "This booking cannot be cancelled in its current state."}, 409)
        if booking.show_id and booking.show.starts_at <= timezone.now():
            return response({"error": "A booking cannot be cancelled after the show starts."}, 409)
        captured_payments = booking.payments.filter(status=Payment.CAPTURED)
        if retrying_failed_refund:
            captured_payments = captured_payments.filter(refund_status="failed")
        payment = captured_payments.order_by("-captured_at", "-pk").first()
        if not payment or not payment.transaction_id:
            return response({"error": "No verified captured payment is available to refund."}, 409)
        if payment.refund_status in {"requested", "initiating", "pending", "processed"}:
            return response({"status": "refund_pending"}, 202)
        if not retrying_failed_refund:
            payment.refund_status = "requested"
            payment.refund_reason = payment.refund_reason or "customer_cancellation"
            payment.save(update_fields=["refund_status", "updated_at"])
        booking.status = Booking.CANCELLATION_PENDING
        booking.save(update_fields=["status"])
        payment_id = payment.pk
        transaction.on_commit(lambda: _enqueue_refund(payment_id))
    return response({"status": "refund_pending"}, 202)


@require_GET
def verify_ticket(request, token):
    from django.core import signing
    try: payload = signing.loads(token, salt="bookmyseat.ticket", max_age=60 * 60 * 24 * 365)
    except signing.BadSignature: return JsonResponse({"valid": False}, status=400)
    booking = Booking.objects.filter(reference=payload.get("ref"), status=Booking.CONFIRMED).first()
    return JsonResponse({"valid": bool(booking), "booking": str(booking.reference) if booking else None})


@login_required
@require_POST
def submit_review(request, movie_id):
    movie = get_object_or_404(Movie, id=movie_id)
    if not eligible_viewer(request.user.id, movie.id): return HttpResponseForbidden("A completed paid show is required to review this movie.")
    try: rating = int(request.POST.get("rating", ""))
    except ValueError: rating = 0
    body = request.POST.get("body", "").strip()
    if rating not in range(1, 6) or not body or len(body) > 3000:
        messages.error(request, "Provide a rating from 1 to 5 and review text up to 3,000 characters.")
        return redirect("movie_list")
    if Review.objects.filter(movie=movie, user=request.user).exists():
        return HttpResponse("You have already reviewed this movie. Edit your existing review instead.", status=409)
    try:
        with transaction.atomic():
            Review.objects.create(movie=movie, user=request.user, rating=rating, body=body)
    except IntegrityError:
        return HttpResponse("You have already reviewed this movie. Edit your existing review instead.", status=409)
    return redirect("movie_list")


@login_required
@require_POST
def edit_review(request, review_id):
    review = get_object_or_404(Review, id=review_id)
    if review.user_id != request.user.id: return HttpResponseForbidden()
    if not eligible_viewer(request.user.id, review.movie_id): return HttpResponseForbidden()
    try: rating = int(request.POST.get("rating", ""))
    except ValueError: rating = 0
    body = request.POST.get("body", "").strip()
    if rating not in range(1, 6): return JsonResponse({"error": "Rating must be from 1 to 5."}, status=400)
    if not body or len(body) > 3000: return JsonResponse({"error": "Review text must contain 1 to 3,000 characters."}, status=400)
    review.rating, review.body = rating, body
    review.save()
    return JsonResponse({"status": "updated"})


@login_required
@require_POST
def report_review(request, review_id):
    review = get_object_or_404(Review, id=review_id)
    if review.user_id == request.user.id: return HttpResponseForbidden("You cannot report your own review.")
    reason = request.POST.get("reason", "").strip()[:500]
    if not reason: return JsonResponse({"error": "A reason is required."}, status=400)
    _, created = ReviewReport.objects.get_or_create(review=review, reporter=request.user, defaults={"reason": reason})
    return JsonResponse({"status": "reported" if created else "already_reported"}, status=201 if created else 409)


def _analytics_range(request):
    from datetime import date
    today = timezone.localdate()
    try:
        start = date.fromisoformat(request.GET.get("start", (today - timedelta(days=29)).isoformat()))
        end = date.fromisoformat(request.GET.get("end", today.isoformat()))
    except ValueError:
        raise ValidationError("Use valid YYYY-MM-DD start and end dates.")
    if start > end or (end - start).days > 366:
        raise ValidationError("Choose a valid date range no longer than 367 days.")
    return start, end


def _date_bounds(start, end):
    tz = timezone.get_current_timezone()
    lower = timezone.make_aware(datetime.combine(start, time.min), tz)
    upper = timezone.make_aware(datetime.combine(end + timedelta(days=1), time.min), tz)
    return lower, upper


def analytics_permission_required(view_func):
    """Require an authenticated staff account with the explicit analytics permission."""
    from functools import wraps

    @login_required
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_staff or not request.user.has_perm("movies.view_booking_analytics"):
            raise PermissionDenied
        return view_func(request, *args, **kwargs)

    return wrapped


@analytics_permission_required
@require_GET
def analytics_dashboard(request):
    try:
        start, end = _analytics_range(request)
    except ValidationError as exc:
        return JsonResponse({"error": exc.messages[0]}, status=400)
    data = _analytics_report_data(start, end)
    return render(request, "movies/analytics.html", {"start": start, "end": end, **data})


def _analytics_report_data(start, end):
    from django.db.models import Sum
    from django.db.models.functions import TruncDate, TruncWeek, TruncMonth, TruncYear, ExtractHour
    start_at, end_at = _date_bounds(start, end)
    bookings = Booking.objects.filter(booked_at__gte=start_at, booked_at__lt=end_at)
    payments = Payment.objects.all()
    paid = bookings.filter(status__in=[Booking.CONFIRMED, Booking.CANCELLATION_PENDING], payments__status=Payment.CAPTURED).distinct()
    captured = payments.filter(status=Payment.CAPTURED, captured_at__gte=start_at, captured_at__lt=end_at)
    refunds = payments.filter(status=Payment.REFUNDED, refund_status="processed", refunded_at__gte=start_at, refunded_at__lt=end_at)
    summary = {
        "bookings": bookings.count(),
        "paid_bookings": paid.count(),
        "gross_revenue": captured.aggregate(total=Sum("amount"))["total"] or Decimal("0.00"),
        "refund_amount": refunds.aggregate(total=Sum("amount"))["total"] or Decimal("0.00"),
        "refund_count": refunds.count(),
        "cancelled": bookings.filter(status__in=[Booking.CANCELLED, Booking.REFUNDED]).count(),
    }
    summary["net_revenue"] = summary["gross_revenue"] - summary["refund_amount"]
    summary["cancellation_rate"] = round(summary["cancelled"] * 100 / summary["bookings"], 2) if summary["bookings"] else 0
    trends = {}
    for label, trunc in (("daily", TruncDate), ("weekly", TruncWeek), ("monthly", TruncMonth), ("yearly", TruncYear)):
        trends[label] = list(captured.annotate(period=trunc("captured_at")).values("period").annotate(amount=Sum("amount"), payments=Count("id")).order_by("period"))
    booking_trends = list(bookings.annotate(period=TruncDate("booked_at")).values("period").annotate(
        bookings=Count("id"), cancelled=Count("id", filter=Q(status__in=[Booking.CANCELLED, Booking.REFUNDED]))).order_by("period"))
    top_movies = list(paid.values("movie__name").annotate(bookings=Count("id", distinct=True), revenue=Sum("payments__amount", filter=Q(payments__status=Payment.CAPTURED))).order_by("-revenue", "movie__name")[:10])
    top_theaters = list(paid.values("theater__name").annotate(bookings=Count("id", distinct=True), revenue=Sum("payments__amount", filter=Q(payments__status=Payment.CAPTURED))).order_by("-revenue", "-bookings", "theater__name")[:10])
    peak_hours = list(bookings.annotate(hour=ExtractHour("booked_at")).values("hour").annotate(bookings=Count("id")).order_by("-bookings", "hour"))
    # Occupancy = BOOKED show-seats / screen seats, summed over the theater's shows in range.
    # Per-show scalar subqueries avoid joining screen seats x show seats (a seats-squared
    # row explosion that took ~14 s for a 365-day window on 100,000 bookings).
    seats_on_screen = Seat.objects.filter(screen_id=OuterRef("screen_id")).order_by().values("screen_id").annotate(n=Count("pk")).values("n")
    booked_on_show = ShowSeat.objects.filter(show_id=OuterRef("pk"), status=ShowSeat.BOOKED).order_by().values("show_id").annotate(n=Count("pk")).values("n")
    occupancy = [
        {"theater": row["theater__name"], "capacity": row["capacity"] or 0, "booked": row["booked"] or 0,
         "percent": round((row["booked"] or 0) * 100 / row["capacity"], 2) if row["capacity"] else 0}
        for row in Show.objects.filter(is_active=True, starts_at__gte=start_at, starts_at__lt=end_at)
        .values("theater__name")
        .annotate(capacity=Sum(Coalesce(Subquery(seats_on_screen), 0)), booked=Sum(Coalesce(Subquery(booked_on_show), 0)))
        .order_by("theater__name")
    ]
    UserModel = get_user_model()
    user_growth = list(UserModel.objects.filter(date_joined__gte=start_at, date_joined__lt=end_at).annotate(period=TruncDate("date_joined")).values("period").annotate(users=Count("id")).order_by("period"))
    cumulative_users = UserModel.objects.filter(date_joined__lt=start_at).count()
    for row in user_growth:
        cumulative_users += row["users"]
        row["cumulative_users"] = cumulative_users
    return {
        "summary": summary,
        "trends": trends,
        "booking_trends": booking_trends,
        "top_movies": top_movies,
        "top_theaters": top_theaters,
        "peak_hours": peak_hours,
        "occupancy": occupancy,
        "user_growth": user_growth,
    }


@analytics_permission_required
@require_GET
def export_bookings(request):
    try: start, end = _analytics_range(request)
    except ValidationError as exc: return JsonResponse({"error": exc.messages[0]}, status=400)
    import csv
    def safe_cell(value):
        text = str(value)
        return "'" + text if text[:1] in {"=", "+", "-", "@", "\t", "\r"} else text
    class Echo:
        def write(self, value): return value
    writer = csv.writer(Echo())
    def rows():
        yield writer.writerow(["reference", "status", "booked_at", "movie", "amount"])
        start_at, end_at = _date_bounds(start, end)
        qs = Booking.objects.filter(booked_at__gte=start_at, booked_at__lt=end_at).select_related("movie").order_by("pk")
        for row in qs.iterator(chunk_size=1000):
            yield writer.writerow([str(row.reference), row.status, row.booked_at.isoformat(), safe_cell(row.movie.name), str(row.total_amount)])
    response = StreamingHttpResponse(rows(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="bookings.csv"'
    return response


@analytics_permission_required
@require_GET
def export_analytics_csv(request):
    """Stream every dashboard report with the same validated date boundaries."""
    try:
        start, end = _analytics_range(request)
    except ValidationError as exc:
        return JsonResponse({"error": exc.messages[0]}, status=400)
    from datetime import date
    import csv

    def safe_cell(value):
        if isinstance(value, str) and value.lstrip(" \t\r\n")[:1] in {"=", "+", "-", "@"}:
            return "'" + value
        return value

    class Echo:
        def write(self, value):
            return value

    writer = csv.writer(Echo())
    start_at, end_at = _date_bounds(start, end)
    data = _analytics_report_data(start, end)

    def rows():
        yield writer.writerow(["report", "item", "period", "count", "value", "details"])
        for key, value in data["summary"].items():
            yield writer.writerow(["summary", key, "", "", value, ""])
        for granularity, records in data["trends"].items():
            yield writer.writerow(["revenue", granularity, "", "", "", ""])
            for record in records:
                yield writer.writerow(["revenue", granularity, record["period"], record["payments"], record["amount"], ""])
        yield writer.writerow(["booking_trends", "", "", "", "", ""])
        for record in data["booking_trends"]:
            yield writer.writerow(["booking_trends", "bookings", record["period"], record["bookings"], record["cancelled"], "cancelled_or_refunded"])
        yield writer.writerow(["occupancy", "", "", "", "", ""])
        for record in data["occupancy"]:
            yield writer.writerow(["occupancy", safe_cell(record["theater"]), "", record["booked"], record["percent"], f"capacity={record['capacity']}"])
        for report_name, key, count_key in (
            ("top_movies", "movie__name", "bookings"),
            ("top_theaters", "theater__name", "bookings"),
        ):
            yield writer.writerow([report_name, "", "", "", "", ""])
            for record in data[report_name]:
                yield writer.writerow([report_name, safe_cell(record[key]), "", record[count_key], record["revenue"], ""])
        yield writer.writerow(["peak_hours", "", "", "", "", ""])
        for record in data["peak_hours"]:
            yield writer.writerow(["peak_hours", f"{record['hour']}:00", "", record["bookings"], "", ""])
        yield writer.writerow(["user_growth", "", "", "", "", ""])
        for record in data["user_growth"]:
            yield writer.writerow(["user_growth", "users", record["period"], record["users"], record["cumulative_users"], "new_and_cumulative"])
        yield writer.writerow(["bookings", "", "", "", "", ""])
        queryset = Booking.objects.filter(booked_at__gte=start_at, booked_at__lt=end_at).select_related("movie", "theater").prefetch_related("payments").order_by("pk")
        for booking in queryset.iterator(chunk_size=1000):
            payment_details = "; ".join(
                f"{payment.status}:{payment.transaction_id or 'no-transaction-id'}:{payment.amount}"
                for payment in booking.payments.all()
            )
            yield writer.writerow([
                "bookings", str(booking.reference), booking.booked_at.isoformat(),
                booking.status, booking.total_amount,
                safe_cell(f"{booking.movie.name}; {booking.theater.name}; {payment_details}"),
            ])

    response = StreamingHttpResponse(
        (writerow for writerow in rows()), content_type="text/csv; charset=utf-8",
    )
    response["Content-Disposition"] = 'attachment; filename="bookmyseat-analytics.csv"'
    return response
