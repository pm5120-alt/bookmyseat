from django.db import transaction
from django.db.models import Avg, Count
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from datetime import timedelta

from django.utils import timezone

from .models import Booking, Movie, Payment, Review


def refresh_movie_ratings(movie_id, using):
    # Serialize aggregate refreshes for a movie so concurrent edits don't leave
    # a stale denormalized count or average behind on PostgreSQL.
    with transaction.atomic(using=using):
        Movie.objects.using(using).select_for_update().filter(pk=movie_id).first()
        reviewers = set(Review.objects.using(using).filter(movie_id=movie_id, is_hidden=False).values_list("user_id", flat=True))
        eligible_users = set()
        bookings = Booking.objects.using(using).filter(
            movie_id=movie_id, user_id__in=reviewers, status=Booking.CONFIRMED,
            payments__status=Payment.CAPTURED, show__starts_at__isnull=False,
        ).select_related("show", "movie").distinct()
        now = timezone.now()
        for booking in bookings:
            if booking.show.starts_at + timedelta(minutes=booking.movie.duration_minutes) <= now:
                eligible_users.add(booking.user_id)
        agg = Review.objects.using(using).filter(movie_id=movie_id, is_hidden=False, user_id__in=eligible_users).aggregate(avg=Avg("rating"), count=Count("id"))
        Movie.objects.using(using).filter(pk=movie_id).update(rating=agg["avg"] or 0, review_count=agg["count"])


@receiver(post_save, sender=Review)
def review_saved(sender, instance, using, **kwargs):
    refresh_movie_ratings(instance.movie_id, using)


@receiver(post_delete, sender=Review)
def review_deleted(sender, instance, using, **kwargs):
    refresh_movie_ratings(instance.movie_id, using)


@receiver(post_save, sender=Booking)
def booking_saved(sender, instance, using, **kwargs):
    refresh_movie_ratings(instance.movie_id, using)


@receiver(post_save, sender=Payment)
def payment_saved(sender, instance, using, **kwargs):
    booking = Booking.objects.using(using).filter(pk=instance.booking_id).only("movie_id").first()
    if booking:
        refresh_movie_ratings(booking.movie_id, using)
