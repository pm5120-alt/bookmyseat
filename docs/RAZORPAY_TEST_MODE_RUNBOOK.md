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

## Checkout configuration and Test Mode UPI

Environment variable (public value, optional): RAZORPAY_CONFIG_ID=config_XXXXXXXXXXXXXX. Leave it empty to use the account's default Checkout configuration. Surrounding quote characters are stripped; malformed IDs are ignored with a server warning. The setting is passed to Checkout as checkout_config_id.

In the Razorpay Dashboard with Test Mode ON:
1. Open Account & Settings -> Payment Configuration and identify the configuration marked Default.
2. Ensure Cards and any intended methods are shown. Under UPI, show UPI ID/Number if you want the test VPA path. A configuration can show/hide methods, but cannot enable methods disabled for the account.
3. Open Settings -> Payment Methods and verify Cards/UPI are enabled in Test Mode.
4. Check Payment Capture settings. BookMySeat only confirms a booking after Razorpay reports the payment as captured.
5. If the configured ID behaves differently from the account default, open the seat page with ?rzp_default=1 (Test Mode only) to compare. The browser console logs the mode and config ID; DevTools -> Network shows checkout_config_id in the create-order response.

For Test Mode, use a supported test card, or use success@razorpay / failure@razorpay only if Checkout exposes UPI ID entry. Razorpay documents UPI Intent/QR as Live-Mode-only; do not scan the test QR with Google Pay or PhonePe and expect a real payment.

The Dashboard configuration preview is only a UI preview. A green success animation there is not a provider transaction. A booking is confirmed only after Razorpay Test Mode records the payment and BookMySeat's backend verifies its signature and captured status.

Reference: https://razorpay.com/docs/payments/payment-gateway/web-integration/standard/integration-steps/

## Evidence to keep
Screenshots of each Dashboard payment/webhook/refund entry, the web, worker and beat logs for the run, and the final `profile` page. Redact keys and signatures before sharing. Record the outcome of each row as PASS/FAIL in `docs/TEST_REPORT.md`; anything not run stays **NOT VERIFIED**.

## Known unverified assumption
`reconcile_uncertain_payment_orders` looks up an order by `receipt` through the provider's order-list API. That filter has not been confirmed against live Razorpay; check scenario 1 logs and the Dashboard if an order-creation timeout is ever simulated.
