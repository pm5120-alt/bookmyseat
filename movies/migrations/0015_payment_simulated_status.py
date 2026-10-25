# Generated manually for the additional explicitly-labelled demo payment status.
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("movies", "0014_retire_failed_order_payments"),
    ]

    operations = [
        migrations.AlterField(
            model_name="payment",
            name="status",
            field=models.CharField(
                choices=[
                    ("created", "Created"),
                    ("authorized", "Authorized"),
                    ("captured", "Captured"),
                    ("failed", "Failed"),
                    ("cancelled", "Cancelled"),
                    ("refunded", "Refunded"),
                    ("simulated", "Simulated"),
                ],
                db_index=True,
                default="created",
                max_length=12,
            ),
        ),
    ]
