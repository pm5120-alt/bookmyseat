# Razorpay Test Mode runbook (the one check not runnable without your keys)

All Razorpay behavior in the automated suite uses test doubles. This runbook produces the real-provider evidence. **Use Test Mode keys only. Never enter a real card or live keys.**

## Setup
1. In the Razorpay Dashboard switch to **Test Mode**; generate Test API keys.
2. Set environment variables (never commit them): `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET` (choose any long random value; reuse it in step 4).
3. Start the stack with PostgreSQL and Redis, then `python manage.py migrate`, a Celery worker and Celery beat (see `DEPLOYMENT.md`), and the web process. Create the demo admin with `python manage.py create_demo_admin`.
4. Expose the web process with an HTTPS tunnel (for example `ngrok http 8000`). In Dashboard -> Webhooks add `https://<tunnel>/movies/payments/webhook/razorpay/` with the same secret and these events: `payment.authorized`, `payment.captured`, `payment.failed`, `order.paid`, `refund.created`, `refund.processed`, `refund.failed`. Add the tunnel host to `DJANGO_ALLOWED_HOSTS` and `https://<tunnel>` to `DJANGO_CSRF_TRUSTED_ORIGINS`.

## Scenarios to run and record
| # | Action | Expected |
|---|---|---|
| 1 | Hold 2 seats, pay with Razorpay's documented Test Mode success card | Payment `captured`, booking `confirmed`, seats `booked`, one ticket email, ticket downloadable from profile |
| 2 | Watch the webhook delivery log in the Dashboard | 200 responses; replaying a delivery returns `duplicate` and changes nothing |
| 3 | Hold seats, pay with Razorpay's documented Test Mode failure card (or `failure@razorpay`) | Page says NOT confirmed; after the `payment.failed` webhook, payment/booking fail and seats release |
| 4 | Close Checkout without paying | Page says NOT confirmed and shows Retry payment; seats stay held until expiry (120 s), then release |
| 5 | Retry after a failure while the hold is valid | New payment attempt row; history shows both |
| 6 | Cancel a confirmed booking | Provider refund created; booking/payment end as `refunded` after the `refund.processed` webhook |
| 7 | Tamper: edit the amount in the browser request | Server ignores it; order amount equals server-computed total |

## Desktop demo simulation (explicitly not a Razorpay transaction)

The optional Windows desktop fallback is gated by all three conditions: `BOOKMYSEAT_DEMO_MODE=true`, `BOOKMYSEAT_DESKTOP_DEMO_PAYMENT_ENABLED=true`, and a Test Mode key (`RAZORPAY_KEY_ID` starts with `rzp_test_`). It presents two choices after reservation and order creation: continue with Razorpay Checkout, or choose **Simulate desktop demo payment (no money moved)**. The simulation uses a distinct `simulated` payment status, never `captured`, writes a `demo_...` reference, confirms only a valid held reservation, and generates a PDF clearly marked `DEMO SIMULATED - NO MONEY MOVED`. Simulated payments are not counted as Razorpay captures/revenue and must never be presented as real payments. The environment flag is off by default and this path is unavailable when Live Mode keys are configured.

## Checkout configuration and Test Mode payment methods

In Test Mode, BookMySeat restores the prior runtime `config.display` request for Card, UPI, Wallet and Netbanking and does not pass `checkout_config_id`, to avoid binding sandbox attempts to the Dashboard config ID while the regression is investigated. In Live Mode, the configured public `RAZORPAY_CONFIG_ID` is passed as `checkout_config_id`; otherwise the account default is used. The runtime list cannot enable a method the Razorpay account has not enabled.

Payment methods that appear are limited by the Razorpay account's enabled methods and the selected Dashboard configuration. A code-level configuration cannot enable Cards, Wallets or bank issuers that Razorpay has not enabled. The current Dashboard screenshots show UPI and Netbanking settings, with no visible Cards option and no available bank list, so account-level Test Mode activation still requires Razorpay Support.

Platform expectation:
- Desktop web: Razorpay UPI Intent app selection is not supported; Checkout displays a QR code instead. A Test Mode QR must not be treated as a real payment request scanned with a production UPI app.
- Mobile web (Android/iOS): Razorpay documents UPI Intent app selection for supported mobile-web flows when UPI Intent is enabled for the account. Test Mode simulation and Live Mode transactions are different; validate only through the documented sandbox methods.
- Dashboard configuration preview: a success animation is only a preview simulation. It does not prove that the website's order creation, payment callback, server verification, and booking confirmation work end to end.

Never switch to Live Mode or use real payment details merely to validate the internship demo. For an end-to-end sandbox test, use a documented method offered by the actual Checkout, then confirm the payment appears in the Razorpay Test Mode transaction list and the BookMySeat backend confirms the booking only after signature/provider-status verification.

References:
- https://razorpay.com/docs/payments/payment-gateway/web-integration/standard/configure-payment-methods/
- https://razorpay.com/docs/payments/payment-methods/upi/upi-intent/
- https://razorpay.com/docs/server-integration/python/test-app/
## Evidence to keep
Screenshots of each Dashboard payment/webhook/refund entry, the web, worker and beat logs for the run, and the final `profile` page. Redact keys and signatures before sharing. Record the outcome of each row as PASS/FAIL in `docs/TEST_REPORT.md`; anything not run stays **NOT VERIFIED**.

## Known unverified assumption
`reconcile_uncertain_payment_orders` looks up an order by `receipt` through the provider's order-list API. That filter has not been confirmed against live Razorpay; check scenario 1 logs and the Dashboard if an order-creation timeout is ever simulated.
