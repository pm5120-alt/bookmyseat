from datetime import timedelta

from django.db.models import Count, Exists, OuterRef, Q, F
from django.utils import timezone

from .models import Booking, Movie, Payment, RecentlyViewedMovie, Show


def recommendations_for_user(user=None, *, exclude_movie_id=None, limit=8, now=None):
    """Rank upcoming movies using history, trending, then catalogue fallback.

    Each distinct overlapping genre contributes 2 points and each overlapping
    language contributes 1 point. Already-booked movies and movies without an
    active upcoming show are never recommended. If there is no history or booking
    data yet (common on a fresh demo), show eligible catalogue movies instead of
    rendering an empty recommendations section.
    """
    now = now or timezone.now()
    booked = Booking.objects.filter(
        status=Booking.CONFIRMED,
        payments__status=Payment.CAPTURED,
    )
    if user is not None and user.is_authenticated:
        booked = booked.filter(user_id=user.pk)
    else:
        booked = booked.none()
    booked_ids = set(booked.values_list("movie_id", flat=True))
    history_ids = set(booked_ids)
    if user is not None and user.is_authenticated:
        history_ids.update(
            RecentlyViewedMovie.objects.filter(user_id=user.pk).values_list("movie_id", flat=True)
        )

    upcoming = Show.objects.filter(
        movie_id=OuterRef("pk"), is_active=True, starts_at__gt=now,
    )
    eligible = Movie.objects.annotate(has_upcoming_show=Exists(upcoming)).filter(has_upcoming_show=True)
    if booked_ids:
        eligible = eligible.exclude(pk__in=booked_ids)
    if exclude_movie_id:
        eligible = eligible.exclude(pk=exclude_movie_id)

    if history_ids:
        personalized = eligible.annotate(
            genre_overlap=Count(
                "genres", filter=Q(genres__movies__id__in=history_ids), distinct=True,
            ),
            language_overlap=Count(
                "languages", filter=Q(languages__movies__id__in=history_ids), distinct=True,
            ),
        ).annotate(
            recommendation_score=2 * F("genre_overlap") + F("language_overlap"),
        ).filter(recommendation_score__gt=0).order_by(
            "-recommendation_score", "-rating", "name", "pk",
        )
        results = list(personalized[:limit])
        if results:
            return results

    recent = now - timedelta(days=30)
    trending = eligible.filter(
        shows__bookings__status=Booking.CONFIRMED,
        shows__bookings__payments__status=Payment.CAPTURED,
        shows__bookings__booked_at__gte=recent,
    ).annotate(
        recent_bookings=Count(
            "shows__bookings",
            filter=Q(
                shows__bookings__status=Booking.CONFIRMED,
                shows__bookings__payments__status=Payment.CAPTURED,
                shows__bookings__booked_at__gte=recent,
            ),
            distinct=True,
        ),
    ).order_by("-recent_bookings", "-rating", "name", "pk")
    trending_results = list(trending[:limit])
    if trending_results:
        return trending_results

    # A new demo often has no paid bookings yet. Keep the homepage useful by
    # falling back to all eligible upcoming catalogue movies, still excluding
    # movies already booked by this user and never recommending past/inactive shows.
    return list(eligible.order_by("-rating", "name", "pk")[:limit])
