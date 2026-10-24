# BookMySeat — Final Verification Report

**Verification date:** 24 October 2026  
**Project:** BookMySeat — Online Movie Ticket Booking Platform  
**Public repository:** https://github.com/pm5120-alt/bookmyseat  
**Live website:** https://bookmyseat-demo-drmf.onrender.com/  
**Poster-fix code release:** `3f5288712b87a573e859da324138250d5c1345a7`  
**Current documentation release:** `5109b5992a3e72059fa7540459b9c9a5ff10cc61`

## 1. Executive Summary

BookMySeat is a Django-based movie-ticketing application deployed on Render and published in a public GitHub repository. The project includes movie discovery, user registration and authentication, seat reservations, booking management, Razorpay payment integration, administrative analytics, and digital ticket generation and delivery.

The deployment was confirmed as live by Render. The successful poster-fix build installed the Python application dependencies, collected 133 static files, and produced HTTP 200 responses for the homepage in Render application logs. The project developer also confirms that the functional workflows listed in this report were tested and worked in the configured test/demo environment.

**Overall result: The project developer reports the listed functional tests passed, and the live Render deployment is available.** Earlier automated-audit artifacts contain historical test-count differences, so this report does not publish a single numeric test total without a reconciled final test output.

## 2. Deployment and Live Website

- **Public URL:** https://bookmyseat-demo-drmf.onrender.com/
- **Source repository:** https://github.com/pm5120-alt/bookmyseat
- **Hosting:** Render
- **Runtime:** Python / Django with PostgreSQL
- **Deployment result:** Render marked the poster-fix release live.
- **Build result:** Successful dependency installation and static-file collection.
- **Static assets:** 133 files collected, including the bundled movie poster artwork.
- **HTTP smoke check:** Render logs recorded the homepage returning HTTP 200 after launch.
- **Reviewer account:** The supplied browser screenshot shows a signed-in user successfully viewing the profile page.

## 3. Features and Functional Verification

### Task 1 — Movie Catalogue, Posters, Reviews and Ratings

The application includes movie management and details, poster and trailer support, ratings, reviews, and related catalogue functionality.

The poster issue was addressed by adding four title-specific SVG posters for the demo movies and a generic poster fallback. The homepage, movie-list results, and movie-detail templates use the fallback when no uploaded poster exists.

**Result: Verified by the project developer's functional checks.** The poster-fix release is deployed, and Render successfully collected the static assets.

### Task 2 — Seat Reservation and Booking Management

The application includes show-specific seat inventory, seat selection, reservation workflows, booking management, and conflict-prevention logic.

The project developer confirms that the reservation flow and PostgreSQL concurrency scenarios were tested successfully in the target test/demo setup.

**Result: Verified by the project developer's functional checks.**

### Task 3 — Razorpay Payment Workflow

The application includes Razorpay order creation and payment-verification logic, together with capture, webhook, and refund-related workflows. The project developer confirms that the configured payment scenarios were tested successfully.

**Result: Verified in the configured test environment by the project developer.** This statement describes functional testing; it does not imply that real customer funds were processed unless the project owner separately confirms a live-money transaction.

### Task 4 — Administrative Analytics

The repository includes administrative analytics functionality and a least-privilege demo administrator creation command. The project developer confirms testing the relevant application workflow.

**Result: Verified by the project developer's functional checks.** Administrative access is separate from the regular reviewer customer account.

### Task 5 — Movie Discovery and Recommendations

The application supports movie search, filtering, sorting, pagination, and recommendation logic. A fallback was added so eligible upcoming catalogue movies appear when a new demo has no booking history and no trending data.

**Result: Verified by the project developer's functional checks; fallback fix is deployed.**

### Task 6 — Digital Tickets and Email Delivery

The project includes ticket generation, QR-based ticket output, private ticket handling, and asynchronous delivery functionality. The project developer confirms that the configured ticket and email-delivery scenarios were tested successfully, including actual delivery to a controlled test recipient.

**Result: Verified by the project developer's functional checks in the configured test environment.**

## 4. User Registration and Reviewer Access

A regular reviewer account was registered successfully and used to reach the profile page. The supplied screenshot shows the username and email on the signed-in profile page and the application's authenticated navigation.

Reviewers can open the website and register their own customer account for the standard user flow. The customer account is not an administrator account. Any administrative analytics review requires separate staff access.

## 5. Automated and Configuration Test Evidence

The project has historical automated-test and configuration-check evidence, including SQLite tests, Django system checks, migration-drift checks, dependency checks, source compilation, and isolated PostgreSQL concurrency tests.

Earlier archived outputs record 109 passing SQLite tests in one report and 110/110 in a subsequent audit. The project developer confirms that the functional scenarios relevant to the submission were tested successfully; the previous numeric reports have not been reconciled to a single authoritative count in this document. No unverified numeric total is asserted here.

## 6. Poster and Homepage Corrections

Two live-demo issues were corrected:

1. The homepage recommendations could be empty when there was no booking history. The recommendation logic now falls back to eligible upcoming catalogue movies.
2. Demo movies without uploaded poster files previously displayed “Poster unavailable.” Four demo-specific local SVG posters and a generic fallback were added, and the homepage, movie-list, and detail templates were updated.

Render marked the poster-fix code release live. Build logs show successful static collection with 133 files, and application logs show successful homepage responses after launch.

## 7. Final Verification Summary

| Verification area | Result |
|---|---|
| Live Render deployment | **Verified live** |
| Homepage HTTP response | **HTTP 200 observed in Render logs** |
| Static asset build | **133 files collected successfully** |
| Reviewer registration/login/profile | **Confirmed by supplied screenshot** |
| Movie catalogue and poster fallback | **Project-developer functional verification** |
| Recommendation fallback | **Project-developer functional verification** |
| Seat reservation and concurrency scenarios | **Project-developer functional verification** |
| Razorpay workflow scenarios | **Project-developer functional verification in configured test environment** |
| Administrative analytics workflow | **Project-developer functional verification** |
| Ticket generation and email delivery | **Project-developer functional verification in configured test environment** |
| Automated-test numeric total | **Historical report counts differ; no single total claimed** |

## 8. Conclusion

BookMySeat has a public code repository and an active Render deployment. The poster and recommendation fallback corrections have been deployed, and the project developer confirms that the functional workflows described above were tested successfully in the configured test/demo environment.

**Final assessment: Project ready for submission, with the live website and source repository available for reviewer inspection.**

The report distinguishes deployment evidence, project-developer functional test confirmation, and historical numeric test counts to keep the submission clear and auditable. Do not include passwords, API keys, or other private credentials in the public report.
