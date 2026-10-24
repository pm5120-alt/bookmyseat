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
        for index, (name, duration, cert, genres, languages, price, description) in enumerate(DEMO_MOVIES):
            movie = Movie.objects.filter(name=name).first()
            if movie is None:
                movie = Movie.objects.create(name=name, duration_minutes=duration, certification=cert, description=description)
                movie.genres.set([Genre.objects.get_or_create(name=g)[0] for g in genres])
                movie.languages.set([Language.objects.get_or_create(name=l)[0] for l in languages])
                movies_created += 1
            if not movie.shows.exists():
                for offset, hour in enumerate((13, 18, 21)):
                    # Distinct start minutes per movie keep (screen, starts_at) unique on the one demo screen; demo data only, not a realistic schedule.
                    start = base + timedelta(days=offset, hours=hour, minutes=index * 5)
                    if Show.objects.filter(screen=screen, starts_at=start).exists():
                        continue
                    Show.objects.create(theater=theater, screen=screen, movie=movie, starts_at=start, price=price)
                    shows_created += 1

        self.stdout.write(f"Demo seed finished: {movies_created} movies, {shows_created} shows, {seats_created} seats created; existing records untouched.")
