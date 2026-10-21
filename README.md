# Sentinel

Sentinel is a fresh-start Roblox group moderation workspace. Its monochrome design and animated background are retained, with a new public landing page and account-based dashboard. No old profile database, PIN account, migration flow, or extension download is included.

## Routes

`/sentinel` is the public landing page. `/sentinel/login` provides EXE account sign-in, registration, email verification guidance, two-step verification, and password recovery. `/sentinel/Dashboard` is protected by a validated server-side account session. The origin root also opens the landing page.

## Run

Install the pinned Python requirements with `pip install -r requirements.txt`, then run `uvicorn app:app --host 0.0.0.0 --port 3000`. A Dockerfile is included for the managed server deployment. The container honors `PORT` and provides `/api/health` for readiness.

Account-linked moderation data uses the project's managed MySQL `DATABASE_URL`. The database starts empty; the uploaded `sentinel.db` is deliberately not imported. EXE Accounts remains the authentication provider at `https://exe-accounts-api.onrender.com`.

## Account and moderation safety

Login is real and validated server-side; no demo identity or preview bypass is used. EXE account passwords and tokens are not placed in browser local storage. State-changing browser API requests require the Sentinel request header. User-owned API resources are scoped to the authenticated account.

Connect a dedicated Roblox moderation account that you own, with permission to manage the groups you add. Roblox session credentials remain in server memory rather than the database. Monitoring continues while the server runs and the Roblox session is valid; reconnect after a server restart. Polling, asset filters, trusted-uploader allowlists, archival delays, and moderation history are managed from the dashboard.

The landing page's example dashboard is explicitly marked as a preview. Its illustrative figures are not customer or live usage statistics. Authenticated dashboards display actual account data and clean empty states.

## Background

The 18-second silent background loop is optimized from the original Sentinel video. Subtle gray blocks create the glitched composition. Reduced-motion preferences disable the animated background and retain a static fallback.

Provider access and refresh tokens are encrypted server-side. An optional `SENTINEL_SESSION_SECRET` can supply a dedicated encryption key; otherwise the key is derived with an application-specific namespace from the private managed database credential. Rotating this key or database credential requires users to sign in again for provider-dependent refresh operations. Neither key material nor provider tokens are exposed to browser scripts or committed to Git.

## Validation

The isolated backend tests pass with mocked EXE calls and a temporary test-only database. Live route checks verify database readiness, the public landing/login pages, anonymous dashboard redirection, protected APIs, retired endpoints, and the page manifest. EXE service health is checked live. Account creation and real Roblox asset changes are not performed using customer accounts during validation.
