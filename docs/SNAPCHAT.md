# Snapchat Public Profile API integration

Publishing to **Snapchat Spotlight** and **Public Stories** from The Reel Ledger,
over Snap's official Public Profile API. No browser automation, no cookies, no
unofficial endpoints.

Every path, field name and constraint below comes from Snap's published
reference:

- [Public Profile API — Introduction](https://developers.snap.com/marketing-api/Public-Profile-API/Introduction)
- [Getting Started](https://developers.snap.com/marketing-api/Public-Profile-API/GetStarted)
- [Profiles](https://developers.snap.com/marketing-api/Public-Profile-API/Profiles)
- [Profile Asset Management](https://developers.snap.com/api/marketing-api/Public-Profile-API/ProfileAssetManagement)
- [Metrics](https://developers.snap.com/api/marketing-api/Public-Profile-API/Metrics)

---

## 1. The two things that decide whether this works at all

**The OAuth app must come from Ads Manager → Business Dashboard.** Snap's Getting
Started page states that a client ID created in the Snap Developer Portal *will
not work* with the Public Profile API. It authenticates perfectly and then every
profile call returns 403, which reads like a code bug and is not one.

**The app must be allowlisted by Snap.** The Public Profile API is allowlist-only.
Until Snap adds your client ID, OAuth completes and the first profile call fails.
The integration surfaces this specifically: connecting reports *"Signed in, but
Snapchat would not return a Public Profile"* rather than a generic error.

---

## 2. Exact production configuration

The production backend is the Vercel deployment of this repository. `vercel.json`
rewrites `/api/(.*)` to `api/index.py`, which mounts `backend/app.py`, so the API
and the dashboard share one origin. The production alias is:

```
https://reels-dashboard-omega.vercel.app
```

Verified live: `GET https://reels-dashboard-omega.vercel.app/api/health` → `200`.

### Redirect URI to register in Snapchat Business

```
https://reels-dashboard-omega.vercel.app/api/snapchat/oauth/callback
```

Register this value exactly — scheme, host and path, no trailing slash. Snap
requires the token exchange to present a byte-identical URI, which is why the
backend stores the URI alongside the CSRF state and replays the stored value
rather than recomputing it.

> The value previously in `.env`, `https://reels-dashboard.vercel.app/api/…`, is a
> **different Vercel project**: its `/api/*` routes return 404. It has been
> corrected. The other production aliases
> (`reels-dashboard-ainan17s-projects.vercel.app`,
> `reels-dashboard-git-main-…`) sit behind Vercel deployment protection and
> answer 302 to an SSO page, so they cannot serve an OAuth callback.

### OAuth scope

```
snapchat-profile-api
```

Single scope. `snapchat-marketing-api` is not requested — nothing here touches ads.

### Environment variables

Set these in **Vercel → Project → Settings → Environment Variables** (Production),
and as GitHub Actions secrets for the cron runner. All are server-side; none may
be prefixed `VITE_`.

| Variable | Required | Purpose |
|---|---|---|
| `SNAPCHAT_CLIENT_ID` | yes | OAuth app client ID from Ads Manager → Business Dashboard |
| `SNAPCHAT_CLIENT_SECRET` | yes | OAuth app client secret. Server-side only, never returned by any endpoint |
| `SNAPCHAT_REDIRECT_URI` | yes | The exact URI above |
| `SNAPCHAT_ENCRYPTION_KEY` | strongly recommended | Fernet key encrypting stored tokens at rest |

Generate the encryption key with:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Without it, a key is derived from `SNAPCHAT_CLIENT_SECRET + DATABASE_URL`. That
works, but rotating the Snapchat client secret would then make every stored token
undecryptable. `MultiFernet` keeps the derived key as a decrypt-only fallback, so
setting `SNAPCHAT_ENCRYPTION_KEY` later is a no-downtime change: existing rows
still open and re-encrypt under the new key as their tokens refresh.

Optional tuning is listed in `.env.example`.

---

## 3. Snapchat Business configuration required

1. **A Snapchat Public Profile.** Business/creator, with Spotlight and Public
   Story posting enabled for the account. A personal Snapchat account with no
   Public Profile authenticates but has nothing to publish to.
2. **A Business account in Snapchat Ads Manager** that owns (or is a member of the
   organization that owns) that Public Profile.
3. **An OAuth app created in Ads Manager → Business Dashboard**, with the redirect
   URI above registered on it.
4. **Allowlisting.** Send the **client ID only** — never the secret — to your Snap
   representative and ask for the app to be allowlisted for the Public Profile API.
5. **A member role on the profile** for the Snapchat login that will authorize: the
   token can only reach profiles that login administers.

---

## 4. How a publish actually works

Six steps. Any of them can fail, and each failure class is handled differently.

```
1. ensure a live access token          refresh if within 5 min of expiry, persist first
2. confirm the token owns the profile  GET /v1/public_profiles/my_profile
3. validate the .mp4                   duration + resolution, before any bytes move
4. create the media container          POST …/media  {type, name, key, iv}
5. upload + finalize                   POST {add_path} action=ADD ×N, then action=FINALIZE
6. publish                             POST …/spotlights  or  POST …/stories
```

Steps 2 and 3 run **before** step 4 on purpose. A container that is created and
then abandoned holds a `media_id` for 24 hours, and a video Snap will reject at
step 6 is far cheaper to catch before its bytes cross the wire.

### Media encryption

Snap will not accept a plain file. `snapchat_media.py` reproduces the documented
`openssl` flow in Python:

| Snap's shell | This implementation |
|---|---|
| `key=$(openssl rand -hex 32)` | `secrets.token_bytes(32)` |
| `iv=$(openssl rand -hex 16)` | `secrets.token_bytes(16)` |
| `openssl enc -aes-256-cbc -nosalt -e -K $key -iv $iv` | AES-256-CBC + PKCS#7 padding |
| `key64=$(echo $key \| xxd -r -p \| base64)` | `base64.b64encode(key_bytes)` |
| `split -b 32m` | 32 MiB parts, `part_number` from 1 |

`xxd -r -p` turns the hex back into bytes before base64, so what Snap receives is
base64 of the **raw 32 bytes**, not of the 64-character hex string. A test
reassembles the parts the pipeline uploaded and decrypts them with the key it
declared, which proves the two agree.

`add_path` and `finalize_path` come back from Create Media already carrying a
region prefix (`/us/v1/…`) and are joined to the host verbatim. Rebuilding them by
hand sends the upload to the wrong region.

### Media requirements, enforced before upload

| | Spotlight | Public Story |
|---|---|---|
| Format | `.mp4` | `.mp4` |
| Duration | 6–60 s | 5–60 s |
| Resolution | ≥ 540×960 | ≥ 540×960 |
| Media object lifetime | 24 h after creation | 24 h after creation |

Duration and resolution are read from the MP4 box tree in pure Python — neither
Vercel's runtime nor the GitHub Actions job has ffmpeg. The reader applies the
track rotation matrix, so a phone-shot portrait clip stored as a rotated
1920×1080 track is correctly measured as 1080×1920 rather than rejected.

Failures name the actual value: *"Video is 3.40s. Snapchat spotlight requires at
least 6s."*

### The Story ID that does not exist

Snap's Post Story response is `{request_id, request_status}` — **there is no story
id in it**. After a successful story publish the code reads the profile's story
list back and takes the newest entry. When that succeeds the record stores
`post_id_kind = "story_id"`; when it does not, it stores the `request_id` and
`post_id_kind = "request_id"`. The dashboard labels the difference, so a request
id is never mistaken for a permanent post id. A Spotlight always returns a real
`spotlight_id`.

---

## 5. How a post cannot go out twice

Three independent guards, because each covers a different failure:

1. **A unique index.** `posted_ledger (snapchat_account_id, source_video_id,
   target_placement) WHERE snapchat_account_id IS NOT NULL`. The base schema's
   `unique (page_id, source_video_id)` never bound Snapchat rows — their
   `page_id` is NULL, and NULLs do not collide — so a re-run could previously
   have posted twice. Now the database refuses the second row.
2. **An idempotency key** on `snapchat_publishing_records`, derived from the
   schedule row (`sched:<id>:<placement>`) or the content item
   (`acct:<id>:item:<id>:<placement>`). A retry of a job that already succeeded
   finds the completed record and stops before uploading.
3. **An atomic claim.** `claim_due_snapchat_posts` flips `pending → posting`
   inside one statement behind `FOR UPDATE SKIP LOCKED`, so the Vercel cron and
   the GitHub Actions runner cannot both take the same job.

Dedup is per **placement**: the same clip may go to Spotlight *and* to the Public
Story, but not twice to either.

---

## 6. Failure handling

| Condition | Class | What the scheduler does |
|---|---|---|
| Access token expired | — | Refreshed automatically 5 min before expiry, persisted before use |
| Refresh fails / `invalid_grant` | `SnapAuthError` | Post returns to **pending**, no attempt spent; account flagged for reconnection; a circuit breaker skips that account's remaining jobs this run |
| Authorization revoked | `SnapAuthError` | As above |
| Token belongs to a different profile | `SnapAuthError` | Publish refused before upload |
| Job points at another account's content | `SnapApiError` | Job **abandoned**; the account keeps publishing |
| Invalid media (format/duration/resolution) | `SnapMediaError` | Abandoned on the first attempt — a retry uploads the same rejected bytes |
| `MEDIA_EXPIRED` (container older than 24 h) | `SnapTransientError` | Retried; the retry re-uploads from scratch |
| Upload failure, 5xx, 429 | `SnapTransientError` | 2 in-call retries, then requeued with exponential backoff (30 m → 60 m → …, capped at 6 h) |
| Network timeout | `SnapTransientError` | Same |
| Google Drive download failure | transient | Same |
| Other Snap API rejection | `SnapApiError` | Abandoned with Snap's `error_code` and message |
| Duplicate publish | — | Refused by all three guards above |
| Worker crash / restart | — | Claimed rows sit in `posting`; `heal_stuck_posting_posts` and `heal_stuck_snapchat_records` reclaim them into the retry path |
| Serverless timeout approaching | — | Unstarted jobs are released back to `pending` for the next run |

Retries stop at `SNAP_MAX_ATTEMPTS` (default 3), after which the post is
`abandoned` with the reason visible in the dashboard and a Retry button that
resets the counter.

A **`request_status: ERROR` inside an HTTP 200** is treated as a failure. Snap
answers 200 with an error envelope, and trusting the HTTP code alone would report
a rejected publish as a success.

---

## 7. Security

| Requirement | How |
|---|---|
| Tokens encrypted at rest | Fernet (AES-128-CBC + HMAC-SHA256) via `MultiFernet`; ciphertext is non-deterministic |
| Tokens never returned by the API | `_clean_snap_account` strips `access_token_encrypted`, `refresh_token_encrypted` and `session_data` from every account response; asserted by tests |
| Tokens never logged | `backend/logger.py` installs a redaction filter on the **root logger**, so third-party libraries are covered too. It masks `access_token`/`refresh_token`/`client_secret`/`Authorization: Bearer …`, DSN passwords, and the literal values of `SNAPCHAT_CLIENT_SECRET`, `RUN_TOKEN`, `DATABASE_URL` and friends wherever they appear |
| Client secret server-side only | Only ever read from the environment inside `snapchat_oauth.py`. `GET /api/snapchat/config` reports booleans |
| OAuth state validated | Stored server-side with a 15-minute TTL; consumed by a single `DELETE … RETURNING`, so a replay finds nothing. Validated **before** the code is exchanged |
| Account ownership before publishing | `GET /v1/public_profiles/my_profile` is compared against the account's stored profile ID on every publish (`SNAP_VERIFY_PROFILE=1`) |
| A job cannot use another account's credentials | Credentials are read only from the job's own joined account row; content owned by a different Snapchat account is refused at queue time (403) and again at publish time |
| Disconnect is scoped | `disconnect_snapchat_account(id)` updates one row by primary key; other accounts are untouched |

---

## 7b. Where the credentials live

The Snapchat client ID and secret are stored **encrypted in Postgres**, in
`app_credentials`, not in environment variables. Rotating the secret is one
API call instead of a redeploy, and Vercel, the GitHub Actions runner and a
local shell all read the same value.

Resolution is **vault first, environment second**. The env fallback is what
bootstraps a fresh database and what keeps a database blip from becoming a
configuration outage.

One secret cannot move: `SNAPCHAT_ENCRYPTION_KEY`, which decrypts the vault.
That and `DATABASE_URL` are the entire bootstrap surface.

```bash
# what is configured, and where each value is read from — never a value
curl -s "$BASE/api/admin/credentials" -H "X-Run-Token: $RUN_TOKEN"

# rotate the client secret; takes effect on the next request, no redeploy
curl -X PUT "$BASE/api/admin/credentials/snapchat_client_secret"      -H "X-Run-Token: $RUN_TOKEN" -H 'Content-Type: application/json'      -d '{"value":"<new secret from Ads Manager>"}'
```

The status endpoint reports the client secret as a length plus an 8-character
SHA-256 prefix. That is enough to confirm *which* secret is loaded and that a
rotation took effect, without the value ever being readable back.

### Why this is only safe with the lockdown

This dashboard talks to Supabase directly with the anon key, and that key
ships inside the built JavaScript bundle — it is public by construction. Every
table in this schema was created with RLS disabled and grants to `anon`.
Before migration 011, this returned rows:

```
GET /rest/v1/snapchat_accounts?select=*   (anon key)  ->  200
GET /rest/v1/pages?select=*               (anon key)  ->  200, fb_access_token in plaintext
```

So writing a client secret into this database **without locking the table
first would have been strictly worse than an environment variable.** Migration
011 enables RLS with no policies and revokes `anon`/`authenticated` on
`app_credentials`, `snapchat_accounts`, `snapchat_oauth_states`,
`snapchat_publishing_records` and `snapchat_account_stats`. Verified after
applying:

```
GET /rest/v1/app_credentials    (anon key)  ->  401  permission denied
GET /rest/v1/snapchat_accounts  (anon key)  ->  401  permission denied
```

The backend connects as the owning role over `DATABASE_URL` and is unaffected.

### Closed: `pages.fb_access_token`

Migration 012 closed this. It was the worst exposure in the schema and it was
verified open before the fix:

```
GET /rest/v1/pages?select=*  (public anon key)  ->  200
[{"id":2,"name":"petflix","fb_access_token":"EAAIBd2ki1YkBS…"}]
```

Two changes, on different axes:

1. **Column-level grants.** PostgREST honours Postgres column privileges, so
   `anon` keeps SELECT on every column of `pages` *except* the token columns.
   The dashboard's reads keep working; the token simply stops being
   selectable. Enumerated dynamically, so a future column is granted
   automatically.
2. **No writes from the browser.** INSERT/UPDATE/DELETE on `pages` are
   revoked. Page writes go through `POST/PUT/DELETE /api/pages`, which
   already existed and already stripped the token from its responses. Without
   this, `anon` could not read the token but could still overwrite it.

The token is now also **encrypted at rest**, in `fb_access_token_encrypted`,
with the same key as the Snapchat tokens. All 10 existing tokens were
migrated and the plaintext column nulled. Reads tolerate both columns during
a rollout or a partial restore, preferring the encrypted one.

Verified after applying:

```
GET  /rest/v1/pages?select=*                        (anon) -> 401
GET  /rest/v1/pages?select=fb_access_token          (anon) -> 401
GET  /rest/v1/pages?select=id,name,is_active        (anon) -> 200
PATCH /rest/v1/pages?id=eq.2                        (anon) -> 401
```

And on the backend, every token-consuming path still resolves a usable token:
`list_pages`, `get_page`, `due_posts`, `retryable_failed_posts` and
`posted_items_for_analytics` (3,234 rows) all decrypt transparently.

`suggest()` in the frontend was changed from `select("*")` to named columns —
a wildcard select over a denied column is rejected outright by PostgREST.

### Still open: business metadata

`content_items`, `scheduled_posts`, `posted_ledger`, `activity_log`,
`content_source_folders` and `page_stats` remain readable with the public
anon key, because the dashboard reads them directly. That is captions,
schedules and Drive file IDs — metadata, not credentials, and the Drive IDs
are useless without access to folders shared only with the service account.

Closing it means moving roughly twenty read functions in
`frontend/src/api.js` onto the backend API (most of the endpoints already
exist) and then applying the same RLS-plus-revoke treatment. It is a
mechanical change, but it touches every screen, so it is called out rather
than done blind.

---

## 8. Database

Migration **`db/migrations/010_snapchat_official_api.sql`** (idempotent, applied to
the live Supabase database).

`snapchat_accounts` — added `organization_id`, `profile_type`, `default_locale`,
`skip_save_to_profile`, `connection_status`, `connected_at`, `disconnected_at`,
`last_publish_at`. Already present from 008/009: profile ID, username, profile
metadata, encrypted access/refresh tokens, token expiry, token status/error.

`snapchat_publishing_records` — added `content_item_id`, `description`, `locale`,
`skip_save_to_profile`, `post_id`, `post_id_kind`, `request_id`, `stage`,
`max_attempts`, `next_retry_at`, `error_code`, `error_kind`, `last_attempt_at`,
`media_duration_seconds`, `media_width`, `media_height`, `media_size_bytes`,
`idempotency_key` (unique).

`scheduled_posts` — added `snap_locale`, `snap_skip_save_to_profile`,
`snap_post_id`.

`posted_ledger` — added `snap_post_id`, plus the partial unique index that makes
Snapchat dedup a constraint rather than a race-prone query.

`snapchat_oauth_states` — expiry index (table from 009).

`snapchat_account_stats` — rebuilt with connection health and per-placement counts.

---

## 9. API endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/snapchat/config` | Readiness booleans, the exact redirect URI, scope, media rules |
| GET | `/api/snapchat/oauth/authorize` | Returns `{authorization_url, state}`; stores the state |
| GET | `/api/snapchat/oauth/callback` | Snap's redirect target. Validates state, exchanges code, stores encrypted tokens, redirects to the dashboard |
| GET | `/api/snapchat/accounts` | Connected accounts, credentials stripped |
| GET | `/api/snapchat/accounts/{id}` | One account |
| PATCH | `/api/snapchat/accounts/{id}` | Settings (placement, locale, hashtags, schedule defaults) |
| GET | `/api/snapchat/accounts/{id}/health` | Live check against Snapchat; re-syncs profile metadata |
| POST | `/api/snapchat/accounts/{id}/refresh-token` | Force a refresh. Returns status only |
| POST | `/api/snapchat/accounts/{id}/disconnect` | Wipe this account's credentials, keep its history |
| DELETE | `/api/snapchat/accounts/{id}` | Delete the account and everything under it |
| GET | `/api/snapchat/accounts/{id}/content` | Content still publishable for a placement |
| POST | `/api/snapchat/accounts/{id}/publish` | Publish now |
| GET | `/api/snapchat/jobs` | The Snapchat queue |
| POST | `/api/snapchat/jobs` | Create a publishing job at a chosen time |
| DELETE | `/api/snapchat/jobs/{id}` | Cancel a queued job |
| POST | `/api/snapchat/jobs/{id}/retry` | Retry a failed/abandoned job |
| GET | `/api/snapchat/publishing/history` | Publishing records |
| GET | `/api/snapchat/publishing/{id}/status` | One record; `?live=true` re-reads the post from Snapchat |
| GET | `/api/snapchat/stats` | Per-account rollup |
| GET | `/api/admin/credentials` | Vault status (admin token). Never a value |
| PUT | `/api/admin/credentials/{key}` | Store or rotate a credential (admin token) |
| DELETE | `/api/admin/credentials/{key}` | Drop a credential; falls back to the environment |

HTTP status mapping: `401` needs reconnection · `422` bad media · `503` Snapchat
unavailable, retry later · `409` duplicate · `403` ownership · `400` rejected.

---

## 10. Connecting the first account

1. Set `SNAPCHAT_CLIENT_ID`, `SNAPCHAT_CLIENT_SECRET`, `SNAPCHAT_REDIRECT_URI` and
   `SNAPCHAT_ENCRYPTION_KEY` in Vercel (Production) and redeploy.
2. Confirm readiness:
   ```bash
   curl -s https://reels-dashboard-omega.vercel.app/api/snapchat/config
   ```
   Expect `"ready": true` and the redirect URI matching what you registered.
3. Open the dashboard → **Snapchat** → **Connect Snapchat**.
4. Sign in with the Snapchat account that administers the Public Profile and
   approve the `snapchat-profile-api` scope.
5. Snapchat redirects to `/snapchat` — the section's own address — with the
   profile listed, `connection_status` `connected`, and a token expiry. The access token and refresh token are stored
   encrypted; neither reaches the browser.
6. Press **Check** on the row. A healthy account reports its Public Profile ID,
   display name and `snap_user_name` straight from
   `GET /v1/public_profiles/my_profile`.

**To connect a second profile**, repeat from step 3 signing in as that profile's
administrator. Each connection stores its own token pair keyed on the Public
Profile ID. Disconnecting one clears only that row.

---

## 11. Real API verification procedure

Mocked tests prove the wire format. They do not prove Snap accepts your account.
Run this against one real allowlisted profile and one real video.

**Prepare:** a `.mp4`, portrait, ≥ 540×960, **8 seconds** long — inside both the
Spotlight (6–60 s) and Story (5–60 s) windows. Put it in the Drive folder that
syncs into `content_items`, and run a sync so it appears in the content list.

Record `ACCOUNT_ID` and `ITEM_ID` from the dashboard, and set
`BASE=https://reels-dashboard-omega.vercel.app`.

### Step 1 — configuration
```bash
curl -s "$BASE/api/snapchat/config"
```
`ready: true`, correct `redirect_uri`, `scope: snapchat-profile-api`.

### Step 2 — authorized profile
```bash
curl -s "$BASE/api/snapchat/accounts/$ACCOUNT_ID/health"
```
`healthy: true`, a real `profile.id`, `profile_matches_stored: true`.

### Step 3 — token refresh
```bash
curl -s -X POST "$BASE/api/snapchat/accounts/$ACCOUNT_ID/refresh-token"
```
`success: true` and a `token_expires_at` later than before. No token in the body.

### Step 4 — real Spotlight publish (upload + publish in one call)
```bash
curl -s -X POST "$BASE/api/snapchat/accounts/$ACCOUNT_ID/publish" \
  -H 'Content-Type: application/json' \
  -d "{\"content_item_id\": $ITEM_ID, \"placement\": \"spotlight\"}"
```
Expect `status: published`, a real `media_id`, `post_id_kind: "spotlight_id"`, and
`media_info` showing the probed duration and resolution.

### Step 5 — confirm it is live on the profile
```bash
curl -s "$BASE/api/snapchat/publishing/$RECORD_ID/status?live=true"
```
`live_confirmed: true` and Snapchat's own record of the Spotlight. Then open the
profile in the Snapchat app and see the video.

### Step 6 — real Public Story publish
Use a **second** video (dedup correctly refuses the same clip to the same
placement; a different placement of the same clip is allowed, so the same file may
be used for the Story if you prefer):
```bash
curl -s -X POST "$BASE/api/snapchat/accounts/$ACCOUNT_ID/publish" \
  -H 'Content-Type: application/json' \
  -d "{\"content_item_id\": $ITEM_ID_2, \"placement\": \"story\"}"
```
Expect `status: published`. `post_id_kind` will be `story_id` if the story list
read-back succeeded, otherwise `request_id` — see §4. Confirm in the app.

### Step 7 — the scheduler end to end
Queue one post two minutes out, then let the cron fire (or trigger `/api/run`):
```bash
curl -s -X POST "$BASE/api/snapchat/jobs" -H 'Content-Type: application/json' \
  -d "{\"snapchat_account_id\": $ACCOUNT_ID, \"content_item_id\": $ITEM_ID_3,
       \"scheduled_time_utc\": \"$(date -u -d '+2 minutes' +%Y-%m-%dT%H:%M:%SZ)\",
       \"placement\": \"spotlight\"}"
curl -s "$BASE/api/snapchat/jobs?account_id=$ACCOUNT_ID"
```
It must stay `pending` until its time, then become `posted` with a `snap_post_id`.

### Step 8 — duplicate refusal
Repeat step 4 with the same item and placement. Expect `409`, or
`already_published: true` with the original `post_id`. Nothing new appears on the
profile.

### Classifying what you see

| Result | Meaning |
|---|---|
| **PASS** | `status: published`, a real Snapchat ID, and the post visible in the app |
| **BLOCKED BY SNAP ALLOWLIST** | OAuth succeeds; the first profile call returns 401/403 or "would not return a Public Profile". Your client ID is not allowlisted — nothing in this code will change that |
| **BLOCKED BY SNAP ACCOUNT/PERMISSION** | Authorized fine, but `my_profile` returns no profile, or publishing returns a permission error. The Snapchat login does not administer a Public Profile, or that profile is not enabled for Spotlight/Story posting |
| **FAIL** | Snapchat rejected the content itself — `INVALID_MEDIA`, `MEDIA_EXPIRED`, a rejected description. Shown with Snap's `error_code` |
| **CODE ISSUE** | A `500`, an unhandled exception, or a `published` record with no `post_id`. This is ours to fix; the `request_id` in the record is what Snap support needs |

The acceptance criterion is Steps 4–6 returning PASS with the posts visible on the
real profile.

---

## 11b. Monetization: what this automation can and cannot do

Publishing through the API and *earning* from what you publish are separate
questions, and the API only answers the first.

**The API has no monetization surface at all.** There is no endpoint to enrol
in the Monetization Program, check eligibility, or read revenue. The Metrics
endpoints return views, shares and subscriber counts — never earnings.
Monetization is an account-level state managed by Snap, not something an
integration can turn on.

**The account gates, none of which code can satisfy** ([Snapchat's Monetization
Program](https://help.snapchat.com/hc/en-us/articles/14669003687444-About-Snapchat-s-Monetization-Program)):

- at least 50,000 followers
- 15,000 hours of view time over the trailing 28 days, with at least 3,000
  from Spotlight
- Snap Star status
- 18+, in an eligible country
- from 7 May 2026, at least 100 hours of Spotlight view time over 28 days to
  keep maximum rewards

**The content gate is the one this pipeline runs into.** Snap's
[Creator Monetization Policy](https://values.snap.com/policy/creator-monetization-policy)
excludes, in its own words:

- "Re-uploading other people's social media posts"
- "Re-posting the same or similar content over and over"
- "repetitive, formulaic or unoriginal template-based content"
- content assembled "via simple automation", absent "substantive, unique
  elements of creative selection, editorial judgement, or personal
  contribution"

This repository's own schema comments describe `source_video_id` as the
"TikTok id parsed from filename". A queue of third-party clips reposted on a
schedule is the shape that policy names. Third-party watermarks compound it:
they also cost recommendation eligibility, which is what feeds Spotlight view
time in the first place.

**One thing the code can help with, and now does.** Snap pays on Spotlight
videos "at least 30 seconds long", while the API accepts 6 seconds. A short
Spotlight publishes perfectly and can never earn. `snapchat_media.validate`
returns `monetizable_length` and a note, `/api/snapchat/config` exposes the
threshold, and the dashboard states it next to the placement selector. It is
advisory — it never blocks a publish.

**What would actually change the answer:** original content made for
Snapchat, no competitor watermarks, Spotlight cuts of 30s+, and the follower
and view-time thresholds met on the account. This automation can then
schedule and publish that content reliably. It cannot substitute for it.

---

## 12. What Snapchat does not permit

These are platform limits, not gaps in the implementation.

- **The API is allowlist-only.** No amount of correct code publishes until Snap
  allowlists the client ID.
- **The Developer Portal cannot issue usable credentials.** Ads Manager →
  Business Dashboard only.
- **Post Story returns no story id.** Recovered by reading the story list back,
  best-effort. Snap provides no better identifier at publish time.
- **No publish-status polling.** There is no "processing → live" state machine.
  A `spotlight_id` means accepted; `GET …/spotlights/{id}` is the confirmation.
- **No edit or delete.** The API has no endpoint to change or remove a published
  Spotlight or Story. Removal is manual, in the app.
- **No platform-side scheduling.** Snap publishes on receipt. All scheduling here
  is ours.
- **Video only for these placements.** Create Media accepts `IMAGE`, but Spotlight
  and Story both require `.mp4`.
- **Stats lag.** Snap documents up to 24 hours of skew between the UI and the API,
  so engagement numbers are not an immediate publish confirmation.
- **No creator enumeration.** No endpoint lists all discoverable creators.
- **Saved Stories are not implemented here.** The endpoint exists
  (`POST …/saved_stories`, taking `snap_sources` of `media_id`/`spotlight_id`/
  `snap_id`) and was out of the requested scope; the API client can be extended
  to it without new plumbing.
- **Upload ceiling:** 35 parts × 32 MiB ≈ 1 GB, and a media container expires 24
  hours after creation.
