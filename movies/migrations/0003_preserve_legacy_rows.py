import uuid
from django.db import migrations, models
import movies.models


def map_legacy_records(apps, schema_editor):
    db = schema_editor.connection.alias
    Theater = apps.get_model("movies", "Theater")
    Screen = apps.get_model("movies", "Screen")
    Show = apps.get_model("movies", "Show")
    Seat = apps.get_model("movies", "Seat")
    ShowSeat = apps.get_model("movies", "ShowSeat")
    Booking = apps.get_model("movies", "Booking")
    BookingSeat = apps.get_model("movies", "BookingSeat")

    shows_by_theater = {}
    for theater in Theater.objects.using(db).all().iterator():
        screen, _ = Screen.objects.using(db).get_or_create(theater_id=theater.pk, name="Screen 1")
        show, _ = Show.objects.using(db).get_or_create(
            theater_id=theater.pk, screen_id=screen.pk, movie_id=theater.movie_id,
            starts_at=theater.time, defaults={"price": 0, "is_active": True})
        shows_by_theater[theater.pk] = show
        seat_map = {}
        for seat in Seat.objects.using(db).filter(theater_id=theater.pk).iterator():
            status = "booked" if seat.is_booked else "available"
            row, _ = ShowSeat.objects.using(db).get_or_create(
                show_id=show.pk, seat_id=seat.pk,
                defaults={"status": status, "held_by_id": None, "hold_expires_at": None})
            seat_map[seat.pk] = row
        for booking in Booking.objects.using(db).filter(theater_id=theater.pk).iterator():
            booking.show_id = show.pk
            # The temporary field default used while adding the nullable UUID
            # may be shared by all preexisting rows; always assign a fresh value.
            booking.reference = uuid.uuid4()
            booking.save(using=db, update_fields=["show", "reference"])
            if booking.seat_id in seat_map:
                BookingSeat.objects.using(db).get_or_create(
                    booking_id=booking.pk, show_seat_id=seat_map[booking.seat_id].pk,
                    defaults={"price": 0})


class Migration(migrations.Migration):
    dependencies = [("movies", "0002_city_genre_language_paymentwebhookevent_and_more")]
    operations = [
        migrations.RunPython(map_legacy_records, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="booking", name="reference",
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
        ),
        migrations.AlterField(
            model_name="movie", name="created_at",
            field=models.DateTimeField(auto_now_add=True, null=True),
        ),
    ]
