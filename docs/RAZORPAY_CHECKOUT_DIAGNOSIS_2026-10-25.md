# Razorpay Checkout diagnosis (Test Mode) — 2026-10-25

Status labels: VERIFIED = code inspection/local static checks; DOCUMENTED = official Razorpay documentation; UNVERIFIED = requires the Dashboard or an actual browser transaction.

## Root-cause assessment

1. The Dashboard's green UPI app preview is a UI simulation, not proof that BookMySeat can complete a real provider-side test transaction.
2. The deployed website had a runtime `config.display.blocks.all_methods` override. This took over method/flow selection instead of allowing Razorpay's account configuration and device capabilities to drive Checkout. The override has been removed.
3. BookMySeat now uses Razorpay Standard Checkout's native options and passes `checkout_config_id` only when the backend returns a valid configured ID. The backend order creation and signature/provider-status verification remain in place.
4. Razorpay documents that UPI Intent app selection is supported on mobile web (when enabled) but is not supported on desktop web. Desktop Checkout automatically displays a QR code instead. Therefore, seeing QR rather than Google Pay/PhonePe buttons on a Windows desktop is expected behavior, not a frontend bug.
5. A Test Mode QR is not a normal live UPI payment request. Do not scan it with a real UPI app to simulate a successful sandbox payment.
6. The current screenshots show UPI and Netbanking configuration sections, but no Card option and an empty bank list. Whether Cards/Netbanking are available in this account's Test Mode cannot be confirmed by code inspection and requires Razorpay Support/dashboard activation.

## Changes in this patch

- Restored the pre-Claude Test Mode runtime Checkout configuration and stopped passing the Dashboard `checkout_config_id` in Test Mode, because the failure appeared after that path was changed. Live Mode continues to pass the configured ID. This isolates the suspected regression; it does not make desktop Test Mode QR payable by a real UPI app.
- Replaced the stale `success@razorpay` advice with accurate Test Mode and desktop/mobile guidance.
- Added regression tests asserting that no runtime `config` override is passed, and that an optional `checkout_config_id` is passed consistently in Test and Live modes.
- Updated the runbook to distinguish desktop UPI QR, mobile-web UPI Intent, the Dashboard preview, and real server-side payment verification.
- No backend views, models, migrations, idempotency, or seat reservation rules are changed by this checkout-options patch.

## Safety preserved

No fake success path, no switch to Live Mode, and no payment signature bypass. The Test Mode runtime configuration is restored as a diagnostic rollback; it cannot activate account-disabled payment methods or make desktop web launch mobile UPI apps.

## Validation status

The uploaded source report states that 128 Django tests passed in the creator's environment. In the current environment, Python compilation, JavaScript syntax and ZIP integrity were checked; Django is not installed here, so the Django suite has not been independently rerun. A real browser/Android test and a Razorpay Test Mode transaction remain unverified.
