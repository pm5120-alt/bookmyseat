# Razorpay Checkout diagnosis — 25 October 2026

## Code correction

`buildCheckoutOptions(order)` no longer sends a runtime `config.display` method list in Test Mode. When the backend supplies a validated public `checkout_config_id`, the browser now passes it to Standard Checkout in both Test and Live Mode. Without one, Checkout uses the account default. The backend's existing validation, order creation, signature verification, provider order/payment status checks, captured-payment requirement, idempotency, seat holds, booking confirmation, and ticket generation were not changed.

Regression coverage asserts the Test Mode and Live Mode configuration ID behavior, no runtime method override, and account-default behavior when no ID is supplied.

## Razorpay rules and this merchant's status

- Razorpay's Test Mode integration documentation lists `success@razorpay` and `failure@razorpay` for the UPI test flow, when UPI ID entry is offered by Checkout.
- Razorpay's UPI documentation says UPI Collect is deprecated effective 28 February 2026. The listed exceptions are MCC 6012/6211, iOS mobile app/mobile web, specified mandate operations, eRupi, and PACB businesses. Ordinary desktop web is not listed. The desktop UPI ID-entry flow therefore cannot be assumed permitted for this merchant in October 2026; Razorpay Support must confirm any merchant-specific eligibility or sandbox exception.
- Razorpay's UPI Intent documentation says Desktop Web does not support Intent and displays a QR. The UPI ID field is a Collect flow and is distinct from that QR flow.
- Razorpay's Standard Checkout documentation supports using a Dashboard configuration ID in Checkout options. Passing the ID selects the configured payment experience; it does not create eligibility for a restricted method.
- The supplied repository notes from the earlier Dashboard investigation report Account Access as Limited, site/app review Under Review, and the saved test configuration showing Netbanking only, with no usable banks. A prior Razorpay-hosted Test Mode Payment Link reportedly showed the same “No appropriate payment method found” error. These observations point to account/configuration availability, but they do not reveal the exact current Dashboard configuration ID or prove the present eligibility state.

The application cannot inspect the merchant's authenticated Dashboard from this source checkout. The exact current configuration ID, enabled methods, and account eligibility remain **unverified**. The corrected code now honors the ID configured by the merchant; the account must be reviewed by Razorpay.

## Dashboard and Support actions

In Test Mode, open the active Payment Configuration and confirm which configuration ID is attached to the payment gateway and which methods are enabled. If UPI ID/Collect is offered, ask Razorpay to confirm in writing that this merchant is eligible to use it on desktop web after the 28 February 2026 deprecation, and whether Test Mode follows the same restriction. If it is not offered, do not try to force-enable it with runtime JavaScript. Use the account's supported UPI QR/Intent or other enabled test methods, or wait for Razorpay to activate an eligible method.

Suggested support request:

> Our Razorpay account currently reports Account Access: Limited and our website/app review is Under Review. In Test Mode, Standard Checkout displays only a UPI QR (or “No appropriate payment method found”); we need to validate the documented `success@razorpay` / `failure@razorpay` UPI test flow on Windows desktop. Please confirm (1) the active Test Mode payment configuration ID and methods attached to it, (2) whether UPI Collect/UPI ID entry is enabled for this merchant, and (3) whether this merchant is eligible for desktop UPI Collect after the NPCI deprecation effective 28 February 2026, including any Test Mode exception. If not eligible, please identify the supported desktop sandbox methods and the account activation steps required. We will use Test Mode keys only.

## Acceptance status

The source correction and automated regression tests are local code work. No real Razorpay transaction, hosted UPI ID field, provider payment ID/signature, server verification, booking confirmation, or downloaded ticket PDF was produced during this task. The account-side preconditions and live browser transaction are **NOT VERIFIED**. Do not represent the final acceptance criterion as passed until a genuine hosted sandbox transaction and its Django verification/ticket are evidenced.

Local checks actually run for this patch: Python source compilation passed; a Node check exercised the three checkout-option cases (Test with config ID, Live with config ID, and Test without config ID) and passed; `git diff --check` passed. The Django test suite, `manage.py check`, and `makemigrations --check` could not run because Django is not installed in the available Python environments. Installing project dependencies was attempted, but downloading Django stalled. These Django checks are **NOT RUN**, not passed.
