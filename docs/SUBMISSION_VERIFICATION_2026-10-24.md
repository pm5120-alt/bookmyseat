# BookMySeat Submission Verification Report

**Date:** 2026-10-24  
**Source repository:** https://github.com/pm5120-alt/bookmyseat  
**Demo URL recorded in the deployment notes:** https://bookmyseat-demo-drmf.onrender.com/  
**Source revision:** `a20a6b26291fcf94fc8ade4cc08bd08e592b92dd` (`Avoid overlapping demo catalogue shows`)

## Overall verdict

**Ready to submit as a source-code package; full external acceptance is still partial.** The updated poster-fix revision is live on Render. The successful Render build collected 133 static files and recorded HTTP 200 for the homepage after launch. This report deliberately separates automated/local results from external service checks that have not been proved.

## Evidence and limits

| Area | Evidence | Status |
|---|---|---|
| Archive integrity | The submitted ZIP opens successfully; 111 entries; CRC check passed. | **Verified** |
| Automated tests | The ZIP's embedded `docs/TEST_REPORT.md` records 109 SQLite tests passed with no failures/skips for its extracted snapshot. A separate acceptance note records 110/110 for a subsequent audit. That count discrepancy is not reconciled in this report. Django is not installed in the current execution environment, so I could not re-run the suite here. | **Prior local evidence; exact count needs reconciliation** |
| Django/configuration checks | Prior snapshot notes record `manage.py check`, migration-drift check, `pip check`, and Python source compilation as passing. They were not re-run in this execution environment. | **Previously reported; not re-run here** |
| PostgreSQL concurrency | Prior local evidence records 8/8 concurrency tests passing against a disposable, isolated PostgreSQL database. It was not the Render production database. | **Local-only evidence** |
| Public demo | On 2026-10-24, Render reported poster-fix commit `3f5288712b87a573e859da324138250d5c1345a7` as `live` (deployment `dep-dbef0fo473hc73927n2g`) at https://bookmyseat-demo-drmf.onrender.com/. Build logs show dependency installation, successful static-file collection (133 files), and a successful build; Render app logs recorded `GET /` with HTTP 200 after launch. | **Deployment live; homepage 200; static assets built; direct visual confirmation after poster release not captured** |
| Payments | A Razorpay Test Mode order-creation request was previously accepted. No successful payment, capture, signature callback, live webhook, or refund was verified; no money was moved. | **Partial; payment flow not fully verified** |
| Email/tickets | Local test coverage and a local SMTP-sink run were documented. Real SMTP-provider delivery was not established and no real email was sent in the recorded check. | **Partial; real email unverified** |
| Production database | No concurrency test was run against the Render production database. | **Unverified** |
| Docker | Compose configuration was reported valid, but a full image build and running stack were not established. | **Partial** |

## Recommended submission wording

BookMySeat is a Django movie-ticketing application with catalogue/discovery, seat holds and bookings, a Razorpay-integrated payment workflow, analytics, and private ticket-delivery code. Local automated and isolated-database checks provide meaningful evidence, but they are not substitutes for a successful live payment, production-database concurrency test, or real email-provider delivery.

Do not state that every task or every external integration is fully verified. The defensible verdict is **source submission prepared; external acceptance checks remain partial**.

## Before a production claim

1. Re-run `python manage.py test` in a clean environment and update the conflicting reported test count.
2. Inspect the selected Render workspace/service and verify current deployment state and routes.
3. Use a disposable PostgreSQL instance for concurrency/performance checks, never the live bookings database.
4. Run an explicitly approved Razorpay test payment and webhook/refund test with test credentials only.
5. Configure a controlled SMTP test recipient and verify delivery before claiming real email works.

Never commit production secrets or place credentials in this report.
