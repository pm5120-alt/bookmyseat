"""Create a separate, least-privilege administrator for a demo environment."""

import os
import secrets

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


DEMO_USERNAME = "demo_admin"
DEMO_EMAIL = "demo-admin@example.invalid"
DEMO_GROUP = "BookMySeat Demo Administrators"
PASSWORD_ENV = "BOOKMYSEAT_DEMO_ADMIN_PASSWORD"


class Command(BaseCommand):
    help = "Create a separate, non-superuser administrator for a demo environment."

    def handle(self, *args, **options):
        User = get_user_model()
        username_lookup = {User.USERNAME_FIELD: DEMO_USERNAME}
        if User.objects.filter(**username_lookup).exists():
            raise CommandError(
                f"Refusing to modify existing account '{DEMO_USERNAME}'. "
                "Choose a fresh demo database or have an administrator review the account."
            )

        password = os.environ.get(PASSWORD_ENV)
        generated = not password
        if generated:
            password = secrets.token_urlsafe(32)
        try:
            validate_password(password, user=User(**username_lookup))
        except Exception as exc:
            raise CommandError(f"The supplied demo password does not meet the configured password policy: {exc}") from exc

        try:
            analytics_permission = Permission.objects.get(
                content_type__app_label="movies", codename="view_booking_analytics"
            )
        except Permission.DoesNotExist as exc:
            raise CommandError(
                "The analytics permission is missing. Run migrations before creating the demo administrator."
            ) from exc

        with transaction.atomic():
            group, created = Group.objects.get_or_create(name=DEMO_GROUP)
            expected_permissions = {analytics_permission.pk}
            existing_permissions = set(group.permissions.values_list("pk", flat=True))
            if not created and existing_permissions != expected_permissions:
                raise CommandError(
                    f"Refusing to change existing group '{DEMO_GROUP}' because its permissions are not the expected least-privilege set."
                )
            group.permissions.set([analytics_permission])
            user = User.objects.create_user(
                username=DEMO_USERNAME,
                email=DEMO_EMAIL,
                password=password,
                first_name="BookMySeat Demo",
                last_name="Administrator (DEMO ONLY)",
                is_staff=True,
                is_superuser=False,
            )
            user.groups.add(group)

        self.stdout.write(self.style.SUCCESS(
            f"Created {DEMO_USERNAME} as a DEMO ONLY staff administrator (not a superuser)."
        ))
        self.stdout.write(f"Analytics access is granted through the '{DEMO_GROUP}' group.")
        if generated:
            self.stdout.write(
                f"One-time generated password for this demo environment: {password}"
            )
            self.stdout.write("Save it securely now; it will not be displayed again.")
        else:
            self.stdout.write(f"Password was read from {PASSWORD_ENV}; it was not printed.")
