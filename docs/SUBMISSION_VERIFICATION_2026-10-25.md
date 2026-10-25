# BookMySeat — Current Submission Status and Verification

**Status updated:** 25 October 2026 (India Standard Time)  
**Project:** BookMySeat — Django Online Movie Ticket Booking Platform  
**Public repository:** https://github.com/pm5120-alt/bookmyseat  
**Live demo:** https://bookmyseat-demo-drmf.onrender.com/  
**Render service:** `bookmyseat-demo`  
**Current GitHub main commit:** `15d82f4521b5d2b830233a5db3702895aa5313a6`  
**Last successful deployment observed before the latest key update:** `dep-dbeg5t67bikc73b80m20` (live on 24 October 2026 at 19:13 UTC)  
**New Test Mode key-pair deployment:** `dep-dbeoac8ae00c73dq24h0` (started 25 October 2026 at 04:27:29 UTC; build was in progress at last check)  
**Poster fallback code release:** `3f5288712b87a573e859da324138250d5c1345a7`

## 1. Current overall status

**The application is deployed and available for reviewer inspection. A fresh Razorpay Test Mode key pair has now been saved to the Render environment, and the resulting deployment is still in progress at the latest check. End-to-end checkout remains unverified until it is retested after deployment.**

Render's previously successful deployment is live, and a new deployment (`dep-dbeoac8ae00c73dq24h0`) was triggered when the new Test Mode API key pair was saved. The build was still in progress at the last check. The source repository and public demo link remain available to the reviewer.

The codebase includes movie discovery, registration and authentication, show-specific seat inventory, seat reservation, booking management, Razorpay order/payment verification and webhook handling, admin analytics, and digital ticket generation/delivery. Those implemented code paths are not equivalent to proof that every external integration currently succeeds on the hosted demo.

## 2. Live demo and deployment evidence

- **Live URL:** https://bookmyseat-demo-drmf.onrender.com/
- **Repository:** https://github.com/pm5120-alt/bookmyseat
- **Hosting/runtime:** Render, Python / Django.
- **Last confirmed successful deployment before the latest key update:** Render reported deployment `dep-dbeg5t67bikc73b80m20` as live. A newer deployment was triggered after updating Razorpay Test Mode credentials and had not completed at the last check.
- **Application assets:** a previous successful build collected 133 static files, including four demo-specific SVG posters and a generic fallback.
- **Earlier smoke-check evidence:** Render application logs recorded HTTP 200 for the homepage after launch.
- **Scope of this check:** the deployment state is verified from Render's deployment listing; the latest transaction flow is not passing. A new independent HTTP probe was not completed in this update.

## 3. Latest payment checkout issue

During the latest recorded attempt, Razorpay Checkout displayed:

> No appropriate payment method found

A fresh CSV containing a Test Mode API key ID (with the `rzp_test_` prefix) and matching API secret was received on 25 October. The values were entered directly into the existing Render service as `RAZORPAY_KEY_ID` and `RAZORPAY_KEY_SECRET`; the raw credentials are deliberately omitted from this report. Render confirmed that the environment variables were updated and triggered deployment `dep-dbeoac8ae00c73dq24h0`. Its build was still in progress at the last status check.

The earlier checkout attempt displayed **“No appropriate payment method found.”** That attempt occurred before the new pair was deployed. Therefore the latest key update is **not yet evidence that checkout works**. The existing `RAZORPAY_WEBHOOK_SECRET` was not changed in that environment update; it must match the secret configured for the Razorpay webhook. Do not assume the webhook or payment flow is verified until a new hosted test transaction completes.

### Required follow-up before calling payments verified

1. Wait for the new Render deployment to finish and show **Live**. Confirm the new pair is saved as `RAZORPAY_KEY_ID` and `RAZORPAY_KEY_SECRET`; never publish those values.
2. Reopen BookMySeat and retry the exact seat reservation and checkout steps that previously failed. If no payment methods appear, confirm that payment methods are available/enabled in the Razorpay Test Mode dashboard and ask the mentor or Razorpay support to diagnose the exact message.
3. Confirm the Razorpay webhook URL is configured as `https://bookmyseat-demo-drmf.onrender.com/movies/payments/webhook/razorpay/`, and that the configured webhook secret matches Render's `RAZORPAY_WEBHOOK_SECRET`.
4. Retry a full test transaction and verify order creation, provider checkout, server-side payment verification, and webhook processing. Do not call the flow successful until the payment is confirmed by the application.
5. Use only test credentials and simulated payments for the demo; do not process real customer payments.

## 4. Other feature areas

| Area | Current evidence / status |
|---|---|
| Movie catalogue and poster fallback | Prior poster fallback release deployed; Render build collected 133 static files. |
| Discovery and recommendation fallback | Code/test changes are present in the repository; earlier project-developer functional checks were reported. |
| Seat selection and reservation | Implemented in source; earlier project-developer testing was reported. Live concurrent-booking acceptance is not independently demonstrated by this report. |
| Razorpay payment, capture and webhook | **Not yet re-tested after a fresh Test Mode key pair was saved; previous attempt returned “No appropriate payment method found.”** |
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

No API-key, API-secret, webhook-secret, password, or other credential value is intentionally included in this report. Keep all credentials in the Render environment and Razorpay dashboard, never in GitHub source files or public screenshots. An earlier pair was exposed in a troubleshooting screenshot, and the latest replacement pair was uploaded as a CSV in this conversation. Treat these test secrets as shared: after confirming the integration, rotate to another final Test Mode API key pair and update Render without sending the replacement secret in chat. Likewise, ensure the webhook secret is generated and synchronized in the Razorpay dashboard and Render.

## 8. Final assessment

**Current result: deployment available; fresh Test Mode API keys saved in Render; deployment in progress at last check; hosted checkout not yet re-tested; external acceptance remains partial.**

- **Public website:** https://bookmyseat-demo-drmf.onrender.com/
- **Source repository:** https://github.com/pm5120-alt/bookmyseat
- **Current report:** https://github.com/pm5120-alt/bookmyseat/blob/main/docs/SUBMISSION_VERIFICATION_2026-10-25.md

The next acceptance gate is a successful, fully verified Razorpay test transaction on the deployed demo. Do not represent that gate as passed until there is fresh evidence of a completed payment and confirmation by the application.
