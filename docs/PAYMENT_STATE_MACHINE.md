# Payment, refund, order-creation and lock-order rules

Historical final-source baseline (2026-10-24): SQLite 107 passed/0 failed/0 skipped; PostgreSQL 16.15 full suite 107 run/0 failed/1 skipped (SQLite-only refusal test); PostgreSQL concurrency module 8/8 passed; see `TEST_REPORT.md`.

## Ticket query and refund worker lock order (2026-10-24)

`send_booking_ticket` uses `select_for_update(of=("self",))` while retaining `select_related()` for display data. The generated PostgreSQL SQL keeps the nullable Show/Screen outer joins but locks only `movies_booking`. The task's SQLite DB path was exercised for a Show booking and a legacy booking whose `show_id` is null. The PostgreSQL ticket-lock test passed on PostgreSQL 16.15 (concurrency module 8/8).

`refund_late_payment` reads the immutable `booking_id`, locks Booking, then locks Payment and rechecks refund state before setting `initiating`. The provider call occurs after that transaction ends. On a pending or failed response, the task relocks Booking then Payment and preserves any concurrent processed refund. A prior refund ID can be replaced only after a definite failed outcome; conflicting or uncertain outcomes remain for manual review. A PostgreSQL test races this response with confirmation and a processed refund webhook using separate connections and timeouts, but it has not been run on PostgreSQL.

## Capture / refund state machine (`_confirm_payment`, `_mark_payment_refunded`, `_process_payment_webhook`)

| Current payment status | Event | Result |
|---|---|---|
| CREATED / AUTHORIZED / FAILED / CANCELLED | capture (valid booking, holds, amount, show not started) | CAPTURED, booking CONFIRMED, seats BOOKED, ticket queued |
| same, but hold expired / amount changed / booking not pending / **show started** | capture | CAPTURED (evidence kept), booking released to FAILED, `refund_status=pending`, `refund_reason=late_payment` or `show_started`, refund queued once |
| CAPTURED | same transaction id | no-op |
| CAPTURED | different transaction id | separate duplicate-charge Payment row (`get_or_create` on transaction id), refund queued once |
| **REFUNDED** | same transaction id | no-op (`already_refunded`); status, refund id/status, timestamps, seats untouched; nothing queued |
| **REFUNDED** | different transaction id | duplicate-charge recovery as above |
| CAPTURED | refund.processed (amount matches) | REFUNDED, booking REFUNDED (unless duplicate-charge row), seats released |
| REFUNDED | refund.created / failed / repeated processed | ignored; a *different* refund id raises and is never applied |
| any non-captured | refund event for an unknown transaction | raises, webhook answers 500, provider retries later |
| REFUNDED | payment.authorized / payment.failed (late) | ignored |

## Provider-order creation (`create_payment_order`) — `Payment.order_state`

`creating -> created | failed | uncertain`

1. Under the Booking row lock: validate booking, show cutoff, holds, amount; reuse a finished (`created`, order id present, amount matches) order; otherwise insert one `creating` row. A partial unique index (`uniq_payment_creating_per_booking`) allows only one in-flight row per booking.
2. Commit, then call Razorpay **with no transaction open**.
3. Re-lock Booking then Payment and revalidate (status, amount, holds, cutoff). The provider order id is always stored; if the selection changed the payment is marked FAILED and the order is **not** returned (a later capture for it goes to refund recovery).
4. A concurrent second request gets `409 order_creation_in_progress` (`Retry-After: 2`) and makes **no** provider call.
5. Only an explicit `BadRequestError` is a definite failure. Timeouts, transport errors and 5xx become `uncertain`: further order creation for that booking returns `409 order_uncertain_manual_review` and the Celery task `reconcile_uncertain_payment_orders` looks the order up by receipt. An in-flight row older than `PAYMENT_ORDER_INFLIGHT_SECONDS` (default 60) is also treated as uncertain.

The locally generated UUID (`idempotency_key`, sent as the Razorpay `receipt`) is **not** a provider-enforced idempotency key. It is only used for reconciliation lookups. The provider-side `receipt` list filter used by reconciliation has not been tested against live Razorpay.

## Unresolved authorization and changed selections

`Payment.AUTHORIZED` means the provider has authorized a charge that may still be captured. It is not treated as a failed or safely cancellable order. Under the booking lock, checkout detects an authorized payment before creating a provider order and returns a conflict without calling the provider. `reserve_seats`, `modify_reservation`, and booking cancellation use the same unresolved-payment gate. They return a useful conflict and leave the booking, saved `BookingSeat.price` snapshots, amount, payment history, and `ShowSeat` ownership unchanged.

An authorization webhook is recorded durably with its transaction ID even if a prior local selection change had retired the payment row. A later capture for an amount/selection that no longer matches the booking is retained as captured evidence and follows late-payment refund recovery; duplicate capture events do not enqueue duplicate refunds. A definitive failure webhook resolves the authorization. Until a definitive provider outcome or manual reconciliation, checkout and booking changes remain blocked. The current implementation does not issue a provider cancellation request because no verified, supported cancellation operation is available in the configured provider adapter.

Regression tests verify call counts and persisted booking, seat, payment, order and amount states for authorized checkout retry, blocked selection changes and cancellation, delayed authorization/capture after amount changes, and retry behavior for definite failure versus uncertain order creation. The SQLite suite passed. PostgreSQL concurrency execution is not verified in this environment.

## Booking cutoff

`show_is_bookable(show, now)` is true only when `show.is_active and show.starts_at > now` (aware datetimes). `starts_at == now` is **not** bookable. Enforced in `seat_selection` (redirect with message), `reserve_seats` and `modify_reservation` (409), `create_payment_order` (409, booking released) and `_confirm_payment` (capture kept, refund queued).

## Lock order

Documented order for every workflow that touches these tables: **User -> Booking -> Payment -> ShowSeat (ascending `seat_id`)**. Changes made: `_confirm_payment`, `_mark_payment_refunded` and the `payment.failed` webhook branch previously locked Payment before Booking; they now use `_lock_booking_then_payment()`. ShowSeat locks in confirm, refund, cancellation and expiry cleanup now use `seat_id` ordering, matching `reserve_seats`/`modify_reservation`. `refund_late_payment` takes a single Payment lock. Because every multi-table path takes the Booking row first, Booking serializes the paths that share rows. This is a code-review result, not a measured one; no deadlock has been tested on PostgreSQL by this change.

## Defect-closure pass 2 (also UNRUN)

* **Ten-seat limit:** `MAX_SEATS_PER_BOOKING = 10` is applied to the *final* selection. `reserve_seats` computes the union of the request and the seats the booking already actively holds before any row changes and answers `400 seat_limit_exceeded`; `modify_reservation` limits the submitted final set. Existing seats, snapshot prices, total and payment state are untouched on rejection.
* **Cutoff time-of-check/use:** both `reserve_seats` and `modify_reservation` do a cheap early check, then — after the user/booking/ShowSeat locks are held — re-read the `Show` row and re-evaluate the cutoff with a fresh `timezone.now()` before changing anything. The hold expiry in `reserve_seats` is now also computed from that fresh clock. No lock-order change (User -> Booking -> ShowSeat by `seat_id`).
* **Partially expired reservations (policy: re-acquire):** `modify_reservation` requires at least one seat of the booking to be actively held (otherwise 409 "Reservation expired"). Every seat in the new selection is then kept (active hold) or re-acquired for this booking/user with the reservation's existing expiry window (never extended). Conflicts are detected before any mutation. Unselected seats are released only if this booking still owns them; a seat that was meanwhile taken by another booking is left alone (only our `BookingSeat` line is removed). Saved `BookingSeat.price` snapshots are never rewritten; only newly added seats use the current show price. `reserve_seats` already released/dropped non-active lines of an existing booking before re-holding requested seats.
* **Migration 0013/0014 semantics:** `Payment.status="created"` means "open attempt that can still receive money". A row with `order_state="failed"` and no provider order id can never receive money, so it is retired to `status="failed"`. Migration `0014_retire_failed_order_payments` does this for existing rows (0013 is unchanged, so it is safe whether or not 0013 was already applied). One intentional exception: when the provider is *not configured*, `create_payment_order` still creates/reuses a `created` + `failed` row and returns 503 (existing test contract); the next request made with a configured provider retires that row to `failed` before creating the real order. The profile page shows the status label, so a dead row would otherwise read as an open "Created" payment.
