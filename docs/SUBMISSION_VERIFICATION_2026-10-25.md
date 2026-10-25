# BookMySeat — Current Submission Status and Verification

**Status updated:** 25 October 2026 (India Standard Time)  
**Project:** BookMySeat — Django Online Movie Ticket Booking Platform  
**Public repository:** https://github.com/pm5120-alt/bookmyseat  
**Live demo:** https://bookmyseat-demo-drmf.onrender.com/  
**Render service:** `bookmyseat-demo`  
**Application-code commit:** `5993839732c9d437094ae3daf64e80567d748430`  
**Application-code deployment:** `dep-dbeomedg1s2s739k2f90` (Render reported Live on 25 October 2026 at 04:54:29 UTC)  
**Test Mode key-pair deployment:** `dep-dbeoac8ae00c73dq24h0` (Render reported Live on 25 October 2026 at 04:28:43 UTC)  
**Poster fallback code release:** `3f5288712b87a573e859da324138250d5c1345a7`

## 1. Current overall status

**The config-ID patch is now deployed and the live site is healthy. A checkout attempt before this patch still displayed “No appropriate payment method found”; payment remains unverified until a fresh post-deployment transaction is tested.**

The key-pair environment update was deployed by Render in `dep-dbeoac8ae00c73dq24h0`. The owner then retried checkout and observed the same payment-method error. The optional `RAZORPAY_CONFIG_ID` patch is now committed and live in Render deployment `dep-dbeomedg1s2s739k2f90`. It reads a public `config_...` ID from the environment, validates its format, includes `checkout_config_id` in the order response only when valid, and passes that value into Standard Checkout. Focused mocked-provider tests were added; they were not run in this environment because Django is not installed and package downloads are blocked. The hosted payment flow now requires a fresh post-deployment test. The source repository and live demo remain available for inspection.

The codebase includes movie discovery, registration and authentication, show-specific seat inventory, seat reservation, booking management, Razorpay order/payment verification and webhook handling, admin analytics, and digital ticket generation/delivery. Those implemented code paths are not equivalent to proof that every external integration currently succeeds on the hosted demo.

## 2. Live demo and deployment evidence

- **Live URL:** https://bookmyseat-demo-drmf.onrender.com/
- **Repository:** https://github.com/pm5120-alt/bookmyseat
- **Hosting/runtime:** Render, Python / Django.
- **Test Mode key deployment:** Render reported deployment `dep-dbeoac8ae00c73dq24h0` as Live after the environment update. The owner then retried checkout and again observed the same payment-method error.
- **Application assets:** a previous successful build collected 133 static files, including four demo-specific SVG posters and a generic fallback.
- **Earlier smoke-check evidence:** Render application logs recorded HTTP 200 for the homepage after launch.
- **Scope of this check:** the deployment state is verified from Render's deployment listing; the latest transaction flow is not passing. A new independent HTTP probe was not completed in this update.

## 3. Latest payment checkout issue

During the latest recorded attempt, Razorpay Checkout displayed:

> No appropriate payment method found

A fresh CSV containing a Test Mode API key ID (with the `rzp_test_` prefix) and matching API secret was received on 25 October. The values were entered directly into the existing Render service as `RAZORPAY_KEY_ID` and `RAZORPAY_KEY_SECRET`; the raw credentials are deliberately omitted from this report. Render confirmed that the environment variables were updated and triggered deployment `dep-dbeoac8ae00c73dq24h0`. Render later reported that the key-pair deployment was Live, but the payment-method error persisted after that key update. The config-ID patch deployed later; the checkout has not yet been retested after that patch.

The owner retried checkout after the new key-pair deployment was Live and again saw **“No appropriate payment method found.”** The new API keys alone did not resolve the visible checkout failure. The existing `RAZORPAY_WEBHOOK_SECRET` was not changed in that environment update; it must match the secret configured for the Razorpay webhook. Do not assume the webhook or payment flow is verified until a new hosted test transaction completes.

### Required follow-up before calling payments verified

The Razorpay Dashboard screenshot shows the saved configuration as `config_Trhb8JAuyT6pgZ`. That public configuration ID is stored in Render's `RAZORPAY_CONFIG_ID` environment variable. The new application-code deployment is Live; this variable should now be available to the app.

1. Retry seat reservation and checkout on the live site. Check the order-create response in the browser Network tab; it should contain `checkout_config_id` when configured.
3. Confirm usable payment methods are enabled/available in the Razorpay Test Mode account. A display configuration controls what is shown but does not activate payment methods unavailable to the account.
4. Confirm the key in the order response matches the API Key ID for the same Razorpay Test Mode account.
5. Verify the webhook URL (`https://bookmyseat-demo-drmf.onrender.com/movies/payments/webhook/razorpay/`) and ensure its secret matches Render's `RAZORPAY_WEBHOOK_SECRET`. Complete a successful simulated payment and confirm server-side verification, webhook processing and the final booking status before declaring payment fixed.

## 4. Other feature areas

| Area | Current evidence / status |
|---|---|
| Movie catalogue and poster fallback | Prior poster fallback release deployed; Render build collected 133 static files. |
| Discovery and recommendation fallback | Code/test changes are present in the repository; earlier project-developer functional checks were reported. |
| Seat selection and reservation | Implemented in source; earlier project-developer testing was reported. Live concurrent-booking acceptance is not independently demonstrated by this report. |
| Razorpay payment, capture and webhook | **Config-ID patch is deployed; hosted payment still needs a fresh transaction test. Django tests were not run in this environment.** |
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

**Current result: config-ID patch deployed and site healthy; post-patch hosted checkout and payment remain unverified pending a fresh Test Mode transaction.**

- **Public website:** https://bookmyseat-demo-drmf.onrender.com/
- **Source repository:** https://github.com/pm5120-alt/bookmyseat
- **Current report:** https://github.com/pm5120-alt/bookmyseat/blob/main/docs/SUBMISSION_VERIFICATION_2026-10-25.md

The next acceptance gate is a successful, fully verified Razorpay test transaction on the deployed demo. Do not represent that gate as passed until there is fresh evidence of a completed payment and confirmation by the application.
