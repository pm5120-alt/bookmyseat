"""Opt-in, idempotent seed of FICTIONAL demo catalogue records.

Never overwrites or deletes anything. A fictional movie that already exists
(matched by exact name) is left untouched, including its shows. Shows are only
created for fictional movies that currently have no shows at all, so repeated
runs do not pile up duplicate schedules.
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from movies.models import Genre, Language, Movie, Screen, Seat, Show, Theater

DEMO_MOVIES = [
    # (name, duration, certification, genres, languages, price, description)
    ("Demo: The Lantern Harbour", 118, "UA", ["Drama", "Mystery"], ["English"], "220.00",
     "Fictional demo record. A harbour town keeps a secret that only appears at dusk."),
    ("Demo: Monsoon Circuit", 132, "UA", ["Action", "Thriller"], ["Hindi", "English"], "260.00",
     "Fictional demo record. A courier races across a flooded city before midnight."),
    ("Demo: Paper Moon Cafe", 104, "U", ["Comedy", "Romance"], ["Tamil"], "180.00",
     "Fictional demo record. Two rival cafe owners are forced to share one kitchen."),
    ("Demo: Signal Garden", 126, "U", ["Sci-Fi", "Family"], ["English", "Tamil"], "240.00",
     "Fictional demo record. A school robotics club grows a garden that talks back."),
]
DEMO_THEATER = "Demo Cinema Hall"
DEMO_SCREEN = "Screen 1"
SEAT_ROWS = "ABCDEF"
SEATS_PER_ROW = 8


def _next_available_start(screen, proposed_start, duration_minutes):
    """Find a 15-minute slot that does not overlap an active show on this screen."""
    while True:
        proposed_end = proposed_start + timedelta(minutes=duration_minutes)
        overlaps = False
        for show in Show.objects.filter(
            screen=screen,
            is_active=True,
            starts_at__lt=proposed_end,
        ).select_related("movie"):
            show_end = show.starts_at + timedelta(minutes=show.movie.duration_minutes)
            if show_end > proposed_start:
                overlaps = True
                break
        if not overlaps:
            return proposed_start
        proposed_start += timedelta(minutes=15)


class Command(BaseCommand):
    help = "Seed fictional demo movies, a theatre, seats and upcoming shows (opt-in; never overwrites)."

    def add_arguments(self, parser):
        parser.add_argument("--yes", action="store_true",
                            help="Required confirmation that fictional demo data should be added.")

    @transaction.atomic
    def handle(self, *args, **options):
        if not options["yes"]:
            self.stdout.write("Nothing done. Re-run with --yes to add fictional demo records (no existing data is changed).")
            return

        theater, _ = Theater.objects.get_or_create(name=DEMO_THEATER, defaults={"address": "Fictional address, demo only"})
        screen, _ = Screen.objects.get_or_create(theater=theater, name=DEMO_SCREEN)
        seats_created = 0
        for row in SEAT_ROWS:
            for number in range(1, SEATS_PER_ROW + 1):
                _, created = Seat.objects.get_or_create(screen=screen, seat_number=f"{row}{number}")
                seats_created += int(created)

        movies_created = shows_created = 0
        base = (timezone.now() + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        demo_movies = []
        for name, duration, cert, genres, languages, price, description in DEMO_MOVIES:
            movie = Movie.objects.filter(name=name).first()
            if movie is None:
                movie = Movie.objects.create(name=name, duration_minutes=duration, certification=cert, description=description)
                movie.genres.set([Genre.objects.get_or_create(name=g)[0] for g in genres])
                movie.languages.set([Language.objects.get_or_create(name=l)[0] for l in languages])
                movies_created += 1
            demo_movies.append((movie, movie.duration_minutes, price, not movie.shows.exists()))

        for offset in range(3):
            cursor = base + timedelta(days=offset, hours=10)
            for movie, duration, price, needs_shows in demo_movies:
                if needs_shows:
                    start = _next_available_start(screen, cursor, duration)
                    Show.objects.create(theater=theater, screen=screen, movie=movie, starts_at=start, price=price)
                    shows_created += 1
                    cursor = start + timedelta(minutes=duration + 30)
                else:
                    # Existing schedules remain untouched; reserve their slot in
                    # the demo sequence so later fictional shows stay spaced out.
                    cursor += timedelta(minutes=duration + 30)

        self.stdout.write(f"Demo seed finished: {movies_created} movies, {shows_created} shows, {seats_created} seats created; existing records untouched.")
