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
| 3 | Hold seats, pay with Razorpay's documented Test Mode failure card | Payment `failed`, booking `failed`, seats released immediately |
| 4 | Close Checkout without paying | Seats stay held until expiry (120 s), then are released by Beat; booking not confirmed |
| 5 | Retry after a failure while the hold is valid | New payment attempt row; history shows both |
| 6 | Cancel a confirmed booking | Provider refund created; booking/payment end as `refunded` after the `refund.processed` webhook |
| 7 | Tamper: edit the amount in the browser request | Server ignores it; order amount equals server-computed total |

## Test Mode UPI QR limitation

Razorpay's Standard Checkout testing guide states that Test Mode UPI testing uses the test VPAs `success@razorpay` and `failure@razorpay`; UPI Intent and QR payments are for Live Mode. Do **not** scan a Test Mode QR using Google Pay, PhonePe or another real UPI app and treat its failure as a BookMySeat code defect. For an end-to-end sandbox check, use a supported test card, or use the test VPA if the Checkout configuration offers UPI-ID entry. Verify the resulting payment in the Razorpay **Test Mode** dashboard and confirm BookMySeat only changes the booking to confirmed after backend verification.

Reference: https://razorpay.com/docs/payments/payment-gateway/web-integration/standard/integration-steps/

## Evidence to keep
Screenshots of each Dashboard payment/webhook/refund entry, the web, worker and beat logs for the run, and the final `profile` page. Redact keys and signatures before sharing. Record the outcome of each row as PASS/FAIL in `docs/TEST_REPORT.md`; anything not run stays **NOT VERIFIED**.

## Known unverified assumption
`reconcile_uncertain_payment_orders` looks up an order by `receipt` through the provider's order-list API. That filter has not been confirmed against live Razorpay; check scenario 1 logs and the Dashboard if an order-creation timeout is ever simulated.
