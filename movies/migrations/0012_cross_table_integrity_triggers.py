from django.db import migrations

from movies.migrations._integrity import (
    install_booking_confirmation_trigger,
    install_screen_integrity_triggers,
    remove_booking_confirmation_trigger,
    remove_screen_integrity_triggers,
)


def install_all(apps, schema_editor):
    install_screen_integrity_triggers(apps, schema_editor)
    install_booking_confirmation_trigger(apps, schema_editor)


def remove_all(apps, schema_editor):
    remove_booking_confirmation_trigger(apps, schema_editor)
    remove_screen_integrity_triggers(apps, schema_editor)


class Migration(migrations.Migration):
    dependencies = [("movies", "0011_analytics_indexes")]
    operations = [migrations.RunPython(install_all, remove_all)]
