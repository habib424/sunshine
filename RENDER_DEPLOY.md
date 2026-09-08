# Deploying Sunshine on Render

One Render web service runs everything: FastAPI serves the API **and** the
built React frontend on a single origin. Access is restricted to
`light.inc` Google accounts once `GOOGLE_CLIENT_ID` is set.

## 1. Create the Google OAuth client (one-time, ~2 minutes)

1. Go to <https://console.cloud.google.com/apis/credentials> (signed in with
   your light.inc account, in the Light Google Cloud project — or create a
   project named "Sunshine").
2. If prompted, configure the consent screen: **Internal** (this alone limits
   sign-in to the light.inc workspace at Google's side; the backend enforces
   it again regardless).
3. **Create credentials → OAuth client ID → Web application**, name it
   `Sunshine`.
4. Under **Authorized JavaScript origins** add:
   - `https://sunshine.onrender.com` (adjust to the actual URL Render gives
     you after the first deploy — come back and add it if it differs)
   - `http://localhost:5173` (optional, for testing sign-in locally)
5. Copy the **Client ID** (ends in `.apps.googleusercontent.com`). No client
   secret is needed — Sunshine uses the ID-token flow.

## 2. Create the Render service

1. Push the repo to GitHub (`master`).
2. In Render: **New → Blueprint**, connect `habib424/sunshine`. Render reads
   `render.yaml` and proposes the `sunshine` web service.
3. When asked for environment values, set:
   - `ANTHROPIC_API_KEY` — your Anthropic key
   - `GOOGLE_CLIENT_ID` — from step 1
   (`AUTH_SECRET_KEY` is generated automatically.)
4. Deploy. First build takes a few minutes (pip + npm + vite build).
5. Open the service URL — you should see the Sunshine sign-in screen, and
   only `@light.inc` Google accounts get in.

## 3. Storage: ephemeral by default, disk optional

Without a disk, every deploy/restart wipes uploads, outputs, the SQLite DB,
and confirmed layouts. **Transforms still work end-to-end** — the mechanism
is stateless per run; history is only used for the dashboard list,
re-downloading old outputs, and auto-applying previously confirmed layouts.

To keep history: upgrade nothing in the code — just uncomment the `disk`
block in `render.yaml` (or add a disk in the dashboard) mounted at
`/opt/render/project/src/backend/storage`. Everything (DB, uploads,
outputs, layouts.json) already lives under that path. Note: disks require a
paid instance and pin the service to one instance (fine — chat sessions are
in-memory and single-instance anyway).

## Local development — nothing changes

Without `GOOGLE_CLIENT_ID` in the environment, auth is disabled and the app
behaves exactly as before (Vite on :5173 proxying to uvicorn on :8000).
To try the sign-in screen locally: put `GOOGLE_CLIENT_ID=<the client id>`
in `.env`, restart the backend, and use the app through
`http://localhost:5173` (that origin must be in the OAuth client's
authorized origins).

## Environment variables reference

| Variable | Required | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | yes | AI file detection / chat / rule generation |
| `GOOGLE_CLIENT_ID` | yes (prod) | Enables + configures Google sign-in; unset = auth off |
| `AUTH_SECRET_KEY` | yes (prod) | Signs the session cookie (Render generates it) |
| `SESSION_HTTPS_ONLY` | prod only | `1` marks the session cookie Secure |
| `AUTH_ALLOWED_DOMAIN` | no | Defaults to `light.inc` |
| `DATABASE_URL` / `STORAGE_PATH` | no | Only needed if storage moves elsewhere |
