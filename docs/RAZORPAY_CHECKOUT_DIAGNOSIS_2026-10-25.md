# Razorpay Checkout diagnosis (Test Mode) - 2026-10-25

Status labels: VERIFIED = checked by code inspection or local static checks; DOCUMENTED = stated in Razorpay's official docs; UNVERIFIED = requires the Dashboard or a live browser test.

## Root-cause assessment

1. The payment-methods body is rendered inside Razorpay's hosted Checkout UI. BookMySeat cannot hide or populate that pane with page CSS. It supplies an optional Payment Configuration ID, but the set of visible methods is controlled by the account's enabled methods and the selected configuration.
2. A configuration or default that hides UPI ID/Number while showing UPI QR can produce a QR-only presentation. This is a plausible explanation for the screenshot, but must be confirmed in the Razorpay Dashboard in Test Mode.
3. A Test Mode QR should not be treated as a real UPI payment request. Razorpay documents UPI test IDs such as success@razorpay and failure@razorpay, while UPI Intent/QR is intended for Live Mode.
4. A green animation in the Dashboard configuration preview is not a transaction. Only a provider-side Test Mode payment and successful BookMySeat backend verification can confirm a booking.
5. Whether a configuration created in one mode is accepted in the other is not guaranteed by our code. Use the dashboard's default in Test Mode if the configured ID does not produce the expected methods.
6. The browser handler has been hardened to handle verification/network failures, distinguish uncaptured payments from failures, and confirm only when the server explicitly returns status=confirmed. Checkout can be reopened while the existing seat hold remains valid.

## Changes in this fix

- Normalize surrounding quotes from the optional public RAZORPAY_CONFIG_ID environment value.
- Expose a display-only test_mode hint based on the public key prefix; no secret is sent to the browser.
- Add machine-readable verification outcomes for missing credentials, invalid signatures, provider outage, mismatched payments, failed payments, and payments not yet captured.
- Treat the browser callback as a hint only; HMAC and provider-side order/amount/currency/captured checks remain mandatory.
- Add Test Mode guidance, Checkout load/open error handling, clearer failure messages and Retry payment while the seat hold is active.
- Add regression tests and a manual Dashboard runbook.

## Safety preserved

No fake success path, no Live Mode switch, no payment signature bypass, and no migration/model changes. This patch does not make a Test Mode QR payable by a real UPI app; verify using a documented sandbox method.
