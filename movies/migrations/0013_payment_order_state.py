from django.db import migrations, models
from django.db.models import Q

from movies.migrations._integrity import (
    install_booking_confirmation_trigger,
    remove_booking_confirmation_trigger,
)


def mark_stranded_legacy_orders(apps, schema_editor):
    # Rows created before order_state existed that never received a provider
    # order id can never be reused or paid; record them as definite failures.
    Payment = apps.get_model("movies", "Payment")
    Payment.objects.filter(provider_order_id="", status="created").update(order_state="failed")


class Migration(migrations.Migration):

    dependencies = [
        ("movies", "0012_cross_table_integrity_triggers"),
    ]

    operations = [
        # SQLite rebuilds movies_payment to add a column. Its booking trigger
        # references that table, so temporarily remove and restore the trigger
        # around the schema edit. The reverse hooks keep it absent until the
        # reverse AddField has completed.
        migrations.RunPython(remove_booking_confirmation_trigger, install_booking_confirmation_trigger),
        migrations.AddField(
            model_name="payment",
            name="order_state",
            field=models.CharField(default="created", max_length=12),
        ),
        migrations.RunPython(mark_stranded_legacy_orders, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="payment",
            constraint=models.UniqueConstraint(
                condition=Q(("order_state", "creating"), ("status", "created")),
                fields=("booking",),
                name="uniq_payment_creating_per_booking",
            ),
        ),
        migrations.RunPython(install_booking_confirmation_trigger, remove_booking_confirmation_trigger),
    ]
