"""Explicitly gated demo-only payment simulation helper.

This feature is not a Razorpay payment and must never be available with live keys.
"""
from django.conf import settings


def desktop_demo_payments_enabled():
    return bool(
        getattr(settings, "BOOKMYSEAT_DEMO_MODE", False)
        and getattr(settings, "BOOKMYSEAT_DESKTOP_DEMO_PAYMENT_ENABLED", False)
        and str(getattr(settings, "RAZORPAY_KEY_ID", "") or "").startswith("rzp_test_")
    )
