# SYNBYTE — Powered by Synthexia Group

## What this edition does

A **Flask multi-page, multi-vendor campus food pickup system**. Public visitors see only the brand/quote and separate Student / Vendor sign-in buttons, not food listings.

- New vendor signs up and **goes live immediately** on the student stall directory. Administrators can manage the platform separately.
- **Each vendor receives an independent copy of all 32 starter food items**. A change by one vendor never changes another vendor's menu.
- Vendors can add dishes (including a new category), upload their own photos, edit price/name, remove/hide and restore dishes. These updates are reflected on the student-facing menu at the next request. Removed dishes are also removed from carts.
- Students choose a vendor on `/vendors` or browse `/menu`, then add one vendor's dishes to their cart and complete the checkout/payment/token sequence across separate pages.
- Staff see only their vendor's verified paid orders, and can set Accepted → Preparing → Ready; students see notifications and collect using the order token.
- Payments default to **LOCAL DEMO SIMULATOR**; Razorpay *test* credentials can be configured in `.env` via environment variables. No real payments are enabled in this package.

## Start on Windows

1. Extract ZIP. Open `SYNBYTE_PROJECT`.
2. Double-click `START_SYNBYTE.bat` (installs Python packages if needed).
3. Open `http://127.0.0.1:5000`. Don't use VS Code Live Server port 5500/5501.
4. For local tests run `RUN_TESTS.bat`.

| Role | Email | Password |
|---|---|---|
| Student | `student@synbyte.test` | `SynbyteDemo!2026` |
| Vendor (Pizza Point) | `pizzapoint@synbyte.test` | `SynbyteDemo!2026` |
| Admin | `admin@synbyte.test` | `SynbyteDemo!2026` |

## Important photo honesty

**This ZIP DOES NOT contain 32 downloaded, locally audited genuine photographs.** Instead, its starter menu uses 32 different real-photograph source URLs (mostly Wikimedia Commons entries with dish-related descriptions). The images load online, **only when the user's internet connection and photo hosts permit access**. All photo subjects remain **unverified by visual audit**, although individual source descriptions were researched. See `PHOTO_MANIFEST.json` and `PHOTO_DOWNLOAD_RESULTS.json` (after downloader runs).

`DOWNLOAD_REAL_FOOD_PHOTOS.bat` attempts to download each source, validates image files, rejects exact/perceptual duplicates, creates a 32-image visual contact sheet, and (only if all 32 downloads succeed) maps the starter menu to local WebP assets. It cannot replace a human's visual exact-dish check or legal license review. **Do not publish this system until those image checks have been performed**, or replace the sources using your own vendor food photos. No generated illustrations are used for seed food images.

## Vendor photo upload

Vendors can upload their own **JPG/PNG/WebP**, minimum 320×220px, with appropriate rights. Every added item requires an uploaded image.

## Data/storage

- New SQLite database: `instance/synbyte.sqlite3`. Account/order history from older project ZIPs is not automatically imported.
- Passwords are hashed and users are separated by role.
- One stall per cart/checkout.
- CSRF protection and SQL parameterization applied.
- No production guarantees. Payment gateway must be configured and independently tested before deployment.

## Known limitations and tests

This environment could check Python compilation, template syntax, files and SQL schema, but **could not run a full Flask browser/payment integration test** because Flask was not installed here. Run `RUN_TESTS.bat` locally. Real-photo URLs have not all been browser-verified, and exact dish licensing/credits require final review.
