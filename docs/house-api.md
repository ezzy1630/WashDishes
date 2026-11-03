# Highest House API

The Jetson exposes only `/house/api/*` and signed still images `/house/media/*`.
No video, buffers, faces, full activity logs, or server secrets enter this API.

## Configuration and security

Environment variables override uppercase keys in the private data root's
`house-settings.json` (0600): `SUPABASE_URL`, `SUPABASE_SECRET_KEY` (server only),
optional `SUPABASE_JWT_SECRET` (legacy HS256), `HOUSE_SITE_ORIGIN` (one HTTPS
browser origin) and `HOUSE_PUBLIC_ORIGIN` (HTTPS tunnel origin). No paths/wildcards.
The Vercel client must never receive the secret key. Cloudflare ingress must allow
only `^/house/` and refuse other paths. App middleware also refuses **all**
non-`/house/` requests carrying `Cf-Connecting-IP`, even with an operator session.

Authenticated calls use `Authorization: Bearer <Supabase access token>`; cookies
are unnecessary. ES256/RS256 use `{SUPABASE_URL}/auth/v1/.well-known/jwks.json`
(10-minute cache; unknown-key refresh at most every 30 seconds). HS256 requires
the explicit legacy secret. Require `sub`, `exp`, `aud=authenticated`, and
`iss={SUPABASE_URL}/auth/v1`; map `sub` to an active resident's `auth_user_id`.
User-editable token metadata grants no access. Only `HOUSE_SITE_ORIGIN` gets CORS,
without credentials. Authenticated writes: 20 attempts/user/minute. Join:
5 attempts/IP/15 minutes. Limits reset on restart. Demo/snapshot accounts are off.

## Read contract

- `GET /house/api/me` → `{"resident":{"id":1,"name":"Alex"},"house":"Highest House"}`.
- Public `GET /house/api/login-names` → `[{"slug":"alex","name":"Alex"}]`, active accounts only.
- `GET /house/api/week?start=YYYY-MM-DD` (Monday; default current) → shape below.

```text
Person = {id: integer, name: string}
Dispute = {state: "open"|"upheld"|"overturned", by: Person|null,
           note: string, at: UTC_ISO, settled_by: Person|null}
Dish = {
  id: integer, kind: string, title: string, location: string,
  out_since: UTC_ISO, due_at: UTC_ISO, gone_at: UTC_ISO|null, points: integer,
  resident: Person|null, named_by: Person|null, named_at: UTC_ISO|null,
  dispute: Dispute|null, filmstrip_url: URL|null, photo_url: URL|null,
  filmstrip_width: integer|null, filmstrip_height: integer|null,
  photo_width: integer|null, photo_height: integer|null,
  can_name: boolean, can_dispute: boolean, can_settle: boolean
}
Visit = {
  id: string, dish_ids: [integer], alias_ids: [integer],
  aliases: [Dish & {duplicate_of: integer}], arrival_seen: boolean, title: string, area: string, at: UTC_ISO,
  points: integer, filmstrip_url: URL|null, photo_url: URL|null,
  filmstrip_width: integer|null, filmstrip_height: integer|null,
  photo_width: integer|null, photo_height: integer|null,
  resident: Person|null, named_by: Person|null, named_at: UTC_ISO|null,
  guess: {resident_id: integer, name: string, basis: string}|null,
  dispute: Dispute|null, can_name: boolean, can_dispute: boolean, can_settle: boolean
}
WeekResponse = {
  week: {start: LOCAL_DATE, end: LOCAL_DATE, closes_at: UTC_ISO,
         closed: boolean, timezone: IANA_ZONE},
  rules: {enabled: boolean, weights: {pan: integer, pot: integer, default: integer},
          min_points: integer, min_lead: integer},
  standings: [{resident_id: integer, name: string, points: integer, dishes: integer}],
  pick: {resident_id: integer|null, name: string|null, reason: string},
  on_duty: {resident_id: integer, name: string, since: UTC_ISO, why: string}|null,
  dishes: [Dish], visits: [Visit], unnamed_count: integer, unreviewed_count: integer,
  verdicts: [{visit_id: string, title: string, verdict: "not_dish"|"clean"|"duplicate",
              by: Person|null, at: UTC_ISO, of_visit_id: string|null}],
  weeks: [{start: LOCAL_DATE, pick_name: string|null, closed: boolean}]
}
```

Image dimensions are intrinsic JPEG pixels, paired with each URL on dishes,
visits and mutation responses. Reserve `width / height` before loading, including
short filmstrips and later photos. Unavailable/expired media has null dimensions;
legacy unreadable JPEG headers also yield null. The API reads JPEG headers only
(no pixel decoding) and caches up to 2048 sizes by path, modification time and
file size for the process lifetime. Replaced files invalidate the cached size.

`arrival_seen=false` means the arrival was not recorded, including singleton
fallbacks. Show “Arrival not recorded — only name someone if you know”; a camera
suggestion never establishes responsibility.

End is exclusive. Display the last included calendar day (end minus one day),
without subtracting from `closes_at`: the naming grace ends at that actual instant.

Standings include all active residents, descending points then
name; `dishes` sums quantities. Last eight weeks include current; open weeks have
null `pick_name`. Unnamed dishes show potential points but contribute zero to
standings; open disputes show zero points. `on_duty` always describes **now**, even
on a historical page. Closed scores, roster, rules, guesses and pick stay frozen.

Weeks follow **deadlines**, Monday midnight to next Monday midnight in
`notify.timezone()`, closing Monday noon after 12 hours for Sunday naming. DST uses
calendar boundaries. `league.left_out` is the sole eligibility predicate: verified
late flag or matching late-photo observation, open/disputed or
resolved after deadline; exclude dismissed, cleared, clean and unverified records.
Startup records use their observation-based follow-up deadline even when the original
arrival is unknown. A pickup after the deadline without a verified late observation
still does not qualify. Matching late evidence must be a snapshot taken no later
than removal. No finished proof picture is required. Eligibility is a review queue, not guilt:
only a human naming/confirmation (including operator manual records) scores.
Automatic camera names and guesses score zero and remain available for review.
Unnamed dishes remain after removal.
Defaults: pan/pot 2, other 1, times quantity; pick needs 4 points and a lead of 2,
with no tie. A qualifying close assigns next week's duty; no pick retains duty.
Off clears duty; reenabling does not resurrect it. An override holds until the next
qualifying close. A minute worker and request-time catch-up close weeks. On first
run, historical weeks freeze with then-available names/rules; old votes cannot be
reconstructed.

## Naming and disputes

Persistent `arrival_visits` / `arrival_visit_members` define visits, including
unavailable or expired filmstrips. Legacy arrival evidence (`kind=filmstrip`,
`label=arrival`) supplies fallback membership through
`capture={"visit_id":...,"obligation_ids":[...],"frames":[...]}`. Missing metadata
means singleton `dish-<id>`. Titles combine quantities, e.g. `Pan and 2 bowls`.
Agreeing member suggestions supply `guess`; guesses never count as names/points.
Only eligible canonical obligations appear in `dish_ids` and `dishes`; incomplete
canonical visits have all flags false. `duplicate_of` maps aliases to canonical
obligations (including human Same dish answers); they score zero and never become
separate review items. `alias_ids` and audit-only `aliases` retain those source ids
and inherit the canonical name/dispute/verdict for display. Naming and verdicts
act on the canonical naming unit. `arrival_seen` is true only when the current
filmstrip metadata records an arrival. Otherwise show “Already there at [at] —
arrival not recorded”; `at`/`out_since` are first observation, not an invented
deposit time. Media expiration removes URLs, never visit identity or deduplication.
Regenerated membership supersedes old write routes; refresh on HTTP 409.
Mixed legacy names/audit fields serialize as null; inspect `dishes` to resolve them.

```text
POST /house/api/visits/{id}/name     {"resident_id": integer}
POST /house/api/visits/{id}/dispute  {"note": string}  # optional; max 1000
POST /house/api/visits/{id}/settle   {"decision":"them"|"not_them"}
POST /house/api/visits/{id}/verdict  {"verdict":"not_dish"|"clean"|"duplicate"|"cant_tell",
                                    "of_visit_id"?: string}
```

Each returns the updated `Visit`. All members and audit events commit atomically;
a forbidden, ineligible or closed member refuses the whole visit. Original
`/house/api/dishes/{id}/name|dispute|settle` remain with identical bodies, returning
an updated `Dish`; grouped dishes also change their whole visit. Use visit routes.
Any housemate can name an unnamed visit, including their own. Existing names change
through dispute or the operator. Only the named person disputes. Settlement needs
another housemate, excluding both disputer and namer, or the operator. “Them”
restores points; “not them” returns it to unnamed. Closed weeks refuse names and
disputes, including operator reassignment. Events retain earlier actions.

Verdicts need no dispute process: Not a dish, It's clean, and Same dish as another
exclude every member for everyone, with who answered and when retained in
`verdicts` and the events table. Duplicate requires a different eligible original
visit, or `of_visit_id="dish-<id>"` to choose one exact dish within a visit.
A person's exact-dish choice can override incorrect detector kind labels;
ambiguous multi-dish targets ask for a specific original. `cant_tell` writes **no label or
verdict event**, just hides that visit/dishes for the current user; standings for
named dishes remain unchanged. `unreviewed_count` counts visible visits with no
human name or global verdict; `unnamed_count` remains a dish count for compatibility.
Verdict returns the previous Visit with `verdict` (verdict/by/at/of_visit_id),
`hidden` (true for cant_tell), and false can_* flags. Closed weeks refuse verdicts.
The operator can undo global verdicts with
`POST /api/league/visits/{id}/undo-verdict {}`; scores and eligibility restore.
Skipped visits are scoped to the resident, or the shared operator identity.

Integration note for Lane G: `Store.mark_not_dish(obligation_ids, by)`,
`mark_clean(obligation_ids, by)`, and `mark_duplicate(obligation_id, of_id, by)`
are called inside the visit transaction (`by` is resident ID or null for operator).
Clearly marked temporary adapters currently persist league exclusions only.
Replace them with Lane G's lifecycle/training implementations without committing
inside them; keep the API-owned exclusion/audit rows. Operator undo restores the
pre-verdict obligation fields; Lane G must reconcile training-label undo at merge.

## Join, media and errors

The operator creates `{HOUSE_SITE_ORIGIN}/join#<token>` on Housemates: 7 days,
single use, hash stored, new link revokes the old. Public
`POST /house/api/join {"token":"...","password":"..."}` (8–256 characters)
returns `{"login":"resident-slug"}`. Admin `POST /auth/v1/admin/users` creates
`<slug>@highest-house.ezzyrappeport.com` with `email_confirm=true`; an existing
`auth_user_id` uses `PUT /auth/v1/admin/users/{id}` to reset the password. No email.
Slugs are persistent, unique ASCII; the site must use the returned slug. Failed
upstream calls leave tokens usable. Passwords/tokens never enter events or logs.

Media: absolute `{HOUSE_PUBLIC_ORIGIN}/house/media/{id}?exp=<unix>&sig=<hmac>`,
HMAC-SHA256 over `id:exp` with root `house-media.key` (0600), at most 30 minutes
and no later than evidence expiry. ID/expiry tampering fails. A visible live or
frozen week's still image, or a shared filmstrip with a visible member, is required.
Expired/deleted media never resurrect. `Cache-Control: private, no-store`.
Errors: `{"detail":"plain sentence"}`; 400 invalid input, 401 sign-in, 403 forbidden,
404 absent/ineligible, 409 closed/conflicting, 429 rate limit, 503 auth unavailable.

Private operator routes retain cookie + CSRF under `/api/league`: `GET week`
(same response, local authenticated media URLs), `GET config` (`rules,on_duty`),
`PATCH rules` (full rules), `POST duty` (`resident_id` or null), `GET accounts`
(`[{resident_id,has_account}]`), `POST residents/{id}/join-link` (`url,expires_at`),
and dish/visit `name` and `settle`, visit `verdict` and `undo-verdict`. No money features.
