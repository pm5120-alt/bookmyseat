from django.db import migrations


def retire_failed_order_payments(apps, schema_editor):
    """A payment whose provider order could never be created (order_state="failed",
    no provider order id) cannot receive money, so its status must be "failed", not
    "created". 0013 only set order_state; this aligns the status."""
    Payment = apps.get_model("movies", "Payment")
    Payment.objects.filter(order_state="failed", provider_order_id="", status="created").update(status="failed")


class Migration(migrations.Migration):

    dependencies = [
        ("movies", "0013_payment_order_state"),
    ]

    operations = [
        migrations.RunPython(retire_failed_order_payments, migrations.RunPython.noop),
    ]
