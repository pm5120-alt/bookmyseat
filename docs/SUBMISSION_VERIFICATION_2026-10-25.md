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

**Current state: the config-ID patch is deployed, but both BookMySeat Standard Checkout and a Razorpay-hosted Test Mode Payment Link show “No appropriate payment method found.” Evidence now strongly points to Razorpay account/payment-method availability or activation configuration; root cause is not yet confirmed.**

The fresh Test Mode API key pair was saved to Render, then deployment `dep-dbeoac8ae00c73dq24h0` went Live. The optional `RAZORPAY_CONFIG_ID` patch is deployed in `dep-dbeomedg1s2s739k2f90`. The saved public configuration ID is stored in Render's environment, not hard-coded in source.

Render logs from the user's post-patch attempt show seat reservation returning HTTP 201 and payment-order creation returning HTTP 200. The payment-order response was 171 bytes after the patch, versus 124 bytes before it; this is consistent with the optional `checkout_config_id` field being included. The user then reproduced the same error on a Razorpay-hosted Test Mode Payment Link outside BookMySeat, which makes a defect unique to the BookMySeat checkout code less likely. This is persuasive account/configuration-side evidence, but not definitive proof of the underlying cause. The user also reports that Razorpay Activation Details shows **Account Access: Limited**, the submitted website/app is **Under Review** with an expected 24–48-hour update, and the saved “BookMySeat Test” configuration lists only **Netbanking**. The user could not complete payment through Netbanking either, so bank availability/enabled status or another account/payment-method restriction still needs Razorpay support to confirm.


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

Both the hosted BookMySeat Standard Checkout and a Razorpay-hosted Test Mode Payment Link reproduce:

> No appropriate payment method found

This matters because the failure also occurs outside the Django application. BookMySeat's order endpoint returns HTTP 200, and the deployed config-ID patch sends the saved configuration ID through Checkout. These facts make an application-specific order-creation defect less likely, but the root cause remains unconfirmed.

### Account observations reported by the project owner

- Razorpay Dashboard is in **Test Mode**.
- Saved Payment Configuration: **BookMySeat Test**, configuration ID `config_Trhb8JAuyT6pgZ`, marked Default.
- The configuration shows only **Netbanking**; the custom-block selector also offers only Netbanking.
- A Razorpay-hosted Payment Link with ID `plink_Ts1Evlzq2cWx8V` shows the same error when opened separately and in a fresh view.
- Account Activation details show **Account Access: Limited**. The Dashboard's Websites & API Keys section shows the website/app URL **Under Review**, with an estimated 24–48-hour update reported by the owner.
- The browser's Razorpay Key ID prefix matches the Test Mode key shown in the Dashboard. The actual secret values are deliberately not included here.

Taken together, this evidence suggests an account-side availability or activation/configuration problem more strongly than a bug unique to BookMySeat. It is not yet confirmed whether limited access affects Test Mode checkout, whether Netbanking lacks usable enabled banks, or whether another restriction applies. Razorpay's integration guidance says some payment methods are available by default while others require an enablement request; use a method actually configured for the account. See the official [Razorpay Python test-integration guide](https://razorpay.com/docs/server-integration/python/test-app/) and [Payment Configuration documentation](https://razorpay.com/docs/payments/dashboard/account-settings/payment-configuration/).

The optional `RAZORPAY_CONFIG_ID` patch is deployed in Render deployment `dep-dbeomedg1s2s739k2f90`. Render logs from the user's post-patch retry show seat reservation returning HTTP 201 and payment-order creation returning HTTP 200. The response size changed from 124 bytes before the patch to 171 bytes after it, consistent with the config ID being included. The same checkout error nevertheless persists, so this patch was not sufficient to fix the issue.

### Recommended next action

Contact Razorpay Support through the authenticated Dashboard's Help & Support / support-ticket flow. Ask them to check Test Mode payment-method availability for the account and the named configuration, whether any banks are enabled under Netbanking, whether the configuration belongs to the same account as the current Test Mode API key, and whether the account's Limited access / pending website review affects this hosted Test Mode checkout. Include the payment link ID and config ID in the secure support ticket. Never include the API secret or webhook secret.

Do not keep rotating API keys or changing application code while the provider-side issue is being investigated. The webhook secret is used after a payment; its mismatch would not explain this pre-payment-method error.

### Status and acceptance gates

- Application deployment and order creation: working based on Render status and HTTP 200 logs.
- Standard Checkout payment methods: **blocked** — no appropriate method found.
- Razorpay-hosted Payment Link: **same failure**.
- Payment verification, capture, webhook handling, and final booking confirmation: **not demonstrated**.
- Unit tests for the config-ID patch: added but not run in the available environment; no real hosted Test Mode payment has succeeded.


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

**Current result: both BookMySeat checkout and a Razorpay-hosted Test Mode Payment Link fail with “No appropriate payment method found.” Account-side/payment-method configuration is the leading hypothesis; awaiting Razorpay support/website review. Payment remains unverified.**

- **Public website:** https://bookmyseat-demo-drmf.onrender.com/
- **Source repository:** https://github.com/pm5120-alt/bookmyseat
- **Current report:** https://github.com/pm5120-alt/bookmyseat/blob/main/docs/SUBMISSION_VERIFICATION_2026-10-25.md

The next acceptance gate is a successful, fully verified Razorpay test transaction on the deployed demo. Do not represent that gate as passed until there is fresh evidence of a completed payment and confirmation by the application.
