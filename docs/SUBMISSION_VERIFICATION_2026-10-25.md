# BookMySeat — Current Submission Status and Verification

**Status updated:** 25 October 2026 (India Standard Time)  
**Project:** BookMySeat — Django Online Movie Ticket Booking Platform  
**Public repository:** https://github.com/pm5120-alt/bookmyseat  
**Live demo:** https://bookmyseat-demo-drmf.onrender.com/  
**Render service:** `bookmyseat-demo`  
**Current source commit reported by Render:** `1ed17cda2e51137b95fdd05c206a5881d22d637a`  
**Latest successful Render deployment observed:** `dep-dbefqmvavr4c73aprqr0` (finished 24 October 2026 at 18:49:00 UTC)  
**Poster fallback code release:** `3f5288712b87a573e859da324138250d5c1345a7`

## 1. Current overall status

**The application is deployed and available for reviewer inspection, but end-to-end Razorpay checkout remains blocked and the project should be described as partially verified—not fully accepted.**

Render reports the latest deployment as successful/live. That deployment uses the latest source commit shown above; it includes the prior application changes and documentation updates. The source repository and public demo link are available to the reviewer.

The codebase includes movie discovery, registration and authentication, show-specific seat inventory, seat reservation, booking management, Razorpay order/payment verification and webhook handling, admin analytics, and digital ticket generation/delivery. Those implemented code paths are not equivalent to proof that every external integration currently succeeds on the hosted demo.

## 2. Live demo and deployment evidence

- **Live URL:** https://bookmyseat-demo-drmf.onrender.com/
- **Repository:** https://github.com/pm5120-alt/bookmyseat
- **Hosting/runtime:** Render, Python / Django.
- **Latest deployment observed:** Render reported deployment `dep-dbefqmvavr4c73aprqr0` as live after a successful build/deploy.
- **Application assets:** a previous successful build collected 133 static files, including four demo-specific SVG posters and a generic fallback.
- **Earlier smoke-check evidence:** Render application logs recorded HTTP 200 for the homepage after launch.
- **Scope of this check:** the deployment state is verified from Render's deployment listing; the latest transaction flow is not passing. A new independent HTTP probe was not completed in this update.

## 3. Latest payment checkout issue

During the latest recorded attempt, Razorpay Checkout displayed:

> No appropriate payment method found

The project owner reports entering the following environment-variable names through the Render service environment editor and initiating a deployment:

- `RAZORPAY_KEY_ID`
- `RAZORPAY_KEY_SECRET`
- `RAZORPAY_WEBHOOK_SECRET`

Render subsequently reported a successful deployment. The checkout error means the payment flow is **not verified end to end**. This report does not assert that Razorpay alone is at fault or that the entered credentials are valid. Possible causes still requiring diagnosis include payment methods unavailable or not enabled for the Razorpay test account, a mismatched/invalid key pair, account configuration, or an integration/configuration issue.

### Required follow-up before calling payments verified

1. Confirm in Render that all three variables are saved in the active service and that the key ID and secret come from the same Razorpay **Test Mode** key pair. Do not publish or send their values.
2. Confirm which payment methods are available/enabled in the Razorpay test dashboard and ask the mentor or Razorpay support to diagnose the exact checkout message.
3. Confirm the Razorpay webhook URL is configured as `https://bookmyseat-demo-drmf.onrender.com/movies/payments/webhook/razorpay/`, and that its configured webhook secret matches Render's `RAZORPAY_WEBHOOK_SECRET`.
4. Retry a full test transaction and verify order creation, provider checkout, server-side payment verification, and webhook processing. Do not call the flow successful until the payment is confirmed by the application.
5. Use only test credentials and simulated payments for the demo; do not process real customer payments.

## 4. Other feature areas

| Area | Current evidence / status |
|---|---|
| Movie catalogue and poster fallback | Prior poster fallback release deployed; Render build collected 133 static files. |
| Discovery and recommendation fallback | Code/test changes are present in the repository; earlier project-developer functional checks were reported. |
| Seat selection and reservation | Implemented in source; earlier project-developer testing was reported. Live concurrent-booking acceptance is not independently demonstrated by this report. |
| Razorpay payment, capture and webhook | **Blocked / not verified on latest hosted checkout** due to “No appropriate payment method found.” |
| Admin analytics | Implemented; prior project-developer functional checks were reported. |
| Digital tickets and email | Implemented; earlier test-environment checks were reported. Real SMTP delivery from the current hosted demo is not established by this report. |
| Automated-test total | Historical report files show 109 versus 110 SQLite tests; count discrepancy remains unreconciled. |
| PostgreSQL concurrency / 100,000-booking benchmark | Historical isolated test evidence exists, but full production acceptance is not established here. |
| Docker full build and launch | Not independently verified in the execution environment used for this report. |

## 5. Reviewer walkthrough recommendation

The live demo can be used to show registration/login, movie catalogue and details, show selection, seat availability/reservation, and the booking interface. The payment provider error should be described accurately if encountered. A source-code walkthrough can show the Razorpay order-creation, verification, and webhook handlers without exposing environment-variable values.

Do not claim successful payment/capture/refund, real-world email delivery, production concurrency, or full acceptance unless those checks are completed and supported by fresh evidence.

## 6. Test and evidence history

The prior report records developer-confirmed functional checks in the configured test/demo environment. This current report supersedes any earlier broad statement that the deployed Razorpay payment flow is verified, because the latest hosted checkout attempt failed before a successful payment could be demonstrated.

Earlier evidence includes a successful poster-fix release, Django checks and source compilation, and historical local/isolated database tests. The previous SQLite test totals differ (109 versus 110), so no single reconciled final test total is claimed. A test double or a local provider stub is not evidence of a successful hosted Razorpay transaction.

## 7. Security note

No API-key, API-secret, webhook-secret, password, or other credential value is intentionally included in this report. Keep all credentials in the Render environment and Razorpay dashboard, never in GitHub source files or public screenshots. Since test credentials were visible in a troubleshooting screenshot, rotate the exposed Razorpay test API secret and webhook secret, update both systems with the replacement values, and then run the test checkout again.

## 8. Final assessment

**Current result: deployment available; payment checkout incomplete; external acceptance remains partial.**

- **Public website:** https://bookmyseat-demo-drmf.onrender.com/
- **Source repository:** https://github.com/pm5120-alt/bookmyseat
- **Current report:** https://github.com/pm5120-alt/bookmyseat/blob/main/docs/SUBMISSION_VERIFICATION_2026-10-25.md

The next acceptance gate is a successful, fully verified Razorpay test transaction on the deployed demo. Do not represent that gate as passed until there is fresh evidence of a completed payment and confirmation by the application.
