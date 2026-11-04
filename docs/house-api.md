# Highest House API

The Jetson exposes only `/house/api/*` and signed still images `/house/media/*`.
No video, buffers, faces, full activity logs, or server secrets enter this API.

## Configuration and security

Environment variables override uppercase keys in the private data root's
`house-settings.json` (0600): `SUPABASE_URL`, `SUPABASE_SECRET_KEY` (server only),
optional `SUPABASE_JWT_SECRET` (legacy HS256), `HOUSE_SITE_ORIGIN` (one HTTPS
browser origin) and `HOUSE_PUBLIC_ORIGIN` (HTTPS tunnel origin). No paths/wildcards.
The Vercel client must never receive the secret key. Cloudflare ingress must allow
only `^/house/` and refuse other paths. Its ingress upstream must be:

```yaml
service: http://127.0.0.1:8788
```

`serve` runs both listeners in one process: the existing private/Tailscale upstream
at `127.0.0.1:8787` (`--host`/`--port`) and the tunnel-only loopback listener at
`127.0.0.1:8788` (`--tunnel-port`). Ports must differ. The ASGI `server` port
identifies ingress; Uvicorn proxy-header rewriting is disabled. The tunnel socket
returns 404 for every non-`/house/` path, with or without edge headers or an operator
session. Only this socket may use `Cf-Connecting-IP` as limiter identity, after IP
parsing (invalid values return 400). Private requests use the actual peer address,
ignoring CF/X-Forwarded-For identity claims. As defense in depth the private listener
also refuses non-`/house/` requests carrying `Cf-Connecting-IP` (403).

Authenticated calls use `Authorization: Bearer <Supabase access token>`; cookies
are unnecessary. ES256/RS256 use `{SUPABASE_URL}/auth/v1/.well-known/jwks.json`
(10-minute cache; refresh attempts, including failures/empty results, at most every 30 seconds; single-flight refresh with fast 503 for concurrent waiters). HS256 requires
the explicit legacy secret. Require `sub`, `exp`, `aud=authenticated`, and
`iss={SUPABASE_URL}/auth/v1`; map `sub` to an active resident's `auth_user_id`.
User-editable token metadata grants no access. Only `HOUSE_SITE_ORIGIN` gets CORS,
without credentials. Authenticated writes: 20 attempts/user/minute. Join:
5 attempts/IP/15 minutes, in a separate capacity pool from resident writes.
Week reads: 60 attempts/resident/minute, 240 globally/minute, at most two computations in flight
and one per resident. Signed media replay has its own budget: 120 attempts/IP/minute,
600 globally/minute, at most eight visibility checks in flight. Excess work fails promptly with 429.
Week-scoped data is copied under the store lock; formatting, signing and JPEG-header
I/O run outside it. Media eligibility is cached (at most 2048 predicates), invalidated
by database writes and week closure; evidence expiry/deletion is always live.
Limits reset on restart. Demo/snapshot accounts are off.

## Read contract

- `GET /house/api/me` → `{"resident":{"id":1,"name":"Alex"},"house":"Highest House"}`.
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
  aliases: [Dish & {duplicate_of: integer}], arrival_seen: boolean,
  evidence: "strong"|"fair"|"weak", reason: string|null, title: string, area: string, at: UTC_ISO,
  points: integer, filmstrip_url: URL|null, photo_url: URL|null,
  filmstrip_width: integer|null, filmstrip_height: integer|null,
  photo_width: integer|null, photo_height: integer|null,
  resident: Person|null, named_by: Person|null, named_at: UTC_ISO|null,
  guess: {resident_id: integer, name: string, basis: string}|null,
  dispute: Dispute|null, can_name: boolean, can_dispute: boolean, can_settle: boolean
}
WeekResponse = {
  start_week: LOCAL_DATE,
  week: {start: LOCAL_DATE, end: LOCAL_DATE, closes_at: UTC_ISO,
         closed: boolean, timezone: IANA_ZONE},
  rules: {enabled: boolean, weights: {pan: integer, pot: integer, default: integer},
          min_points: integer, min_lead: integer},
  standings: [{resident_id: integer, name: string, points: integer, dishes: integer}],
  pick: {resident_id: integer|null, name: string|null,
         state: "pick"|"too_close"|"below_minimum"|"disabled"|"tie",
         points: integer, lead: integer, needed_points: integer, needed_lead: integer,
         reason: string},
  on_duty: {resident_id: integer, name: string, since: UTC_ISO, why: string}|null,
  dishes: [Dish], visits: [Visit], unnamed_count: integer, unreviewed_count: integer,
  clear_count: integer, weak_count: integer,
  verdicts: [{visit_id: string, title: string, kind: "not_dish"|"clean"|"duplicate",
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
name; `dishes` sums quantities. Up to eight weeks include current, bounded by
`start_week`; open weeks have
null `pick_name`. Unnamed dishes show potential points but contribute zero to
standings; open disputes show zero points. `on_duty` always describes **now**, even
on a historical page. Closed scores, roster, rules, guesses and pick stay frozen.

`pick` is the authoritative decision for the operator home, weekly image and house
site. `points` is the leading score and `lead` its margin over second place (zero
for a tie or empty standings). Both remain available when duty is disabled.
`needed_points` and `needed_lead` are **additional** score/lead still required,
never negative; both are zero when disabled. The additional points for a clear
pick, if nobody else scores, are `max(needed_points, needed_lead)`. A tied leader
needs at least one point of lead even when `min_lead=0`.

State priority is disabled, below minimum (including no named points), tie,
too close, then pick. Only `state="pick"` has a non-null resident ID and name.
`reason` never includes a person's name: e.g. `6 points · 4 ahead`, or
`Too close — 1 point ahead, needs 2`. Render the name once in the heading and the
reason underneath; do not strip sentences or recalculate eligibility in clients.
Older closed snapshots receive this display shape on read from their **frozen**
standings and rules; stored snapshots and their selected person are not rewritten.
New weekly duty assignments keep `why` prefixed with `Weekly pick:` so it remains
distinct from a house-vote override.

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
qualifying close. A minute worker and request-time catch-up close weeks.

The first call to league rules or week closing durably records `start_week`: the
Monday of that call's week in the house timezone. It lives in the singleton
`league_start` row, separately from the replaceable rules JSON. Rules PATCH cannot
change it. Only weeks starting on or after it can close, freeze names or pick duty.
The first run does not close any historical weeks, even when old obligations exist;
the start week first closes the following Monday at noon. Later downtime catches
up only from that boundary. No heuristic migration infers a start from old results.

Both house and operator week responses expose `start_week`. Their shared `weeks`
list omits earlier weeks, so both past-week selectors use the same boundary.
Explicit pre-start requests to week/result/share endpoints return 400 with
“That week is before the league started (YYYY-MM-DD).” Pre-start records remain
ordinary records, editable via the operator dashboard's record actions; legacy
frozen rows from before the start cannot lock them. They do not appear as league
weeks or provide signed house media.

### Repair an already backfilled database

Run the explicit local operator CLI against the intended existing data root
(`WASHDISHES_DATA_DIR` contains the `live/history.db` database):

```sh
WASHDISHES_DATA_DIR=/path/to/data python -m wash_dishes league-reset-start --dry-run
WASHDISHES_DATA_DIR=/path/to/data python -m wash_dishes league-reset-start
```

Both print JSON with `dry_run`, `previous_start_week`, `start_week`,
`deleted_weeks`, `cleared_duty` and `changed` (whether changes are needed).
Dry-run opens the existing database read-only, without preparing directories or
running schema migrations, and changes nothing. The real command atomically sets
the start to the current house week, deletes only earlier `league_weeks`, and
clears duty when results were deleted, `why` begins with `Weekly pick`, and
`since` falls on or before the start Monday in house time (including a preceding
week's Monday-noon pick). House-vote overrides and newer assignments stay intact.
Obligations, evidence, rules and old audit events stay intact. A changed reset
records `league_start_reset` with the JSON change report. Repeating it in the same
week is a no-op, including no additional event. No camera or remote service runs.

## Naming and disputes

Persistent `arrival_visits` / `arrival_visit_members` define visits, including
unavailable or expired filmstrips. Legacy arrival evidence (`kind=filmstrip`,
`label=arrival`) supplies fallback membership through
`capture={"visit_id":...,"obligation_ids":[...],"frames":[...]}`. Missing metadata
means singleton `dish-<id>`. Titles combine quantities, e.g. `Pan and 2 bowls`.
`guess` is null until the current identity calibration has at least 30 independent
human-checked day-excluded predictions in a fixed confidence band with >=90% precision
(the existing automatic-naming gate constants). Once earned, the API scores only the
visit's unambiguous arrival anchor against the current human gallery. Scores are cue
ranking values, not probabilities. Legacy strips without a safe anchor show no guess.
A human tap to confirm uses the ordinary name endpoint; guesses never score points.
Any label/gallery/model change invalidates calibration until the next successful nightly check.
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

Residents may apply Not a dish, It's clean, and Same dish as another only when
every canonical member is unnamed, undisputed and nameable by that resident.
The permission is checked atomically under the visit lock/transaction; otherwise
409: "Already named — use That wasn't me to dispute it." Operator verdicts retain
full power and undo. These global verdicts exclude every member for everyone, with who answered and when retained in
`verdicts` and the events table. Duplicate requires a different eligible original
visit, or `of_visit_id="dish-<id>"` to choose one exact dish within a visit.
A person's exact-dish choice can override incorrect detector kind labels;
ambiguous multi-dish targets ask for a specific original. `cant_tell` writes **no label or
verdict event**, hides that visit/dishes for the current user, and retracts its identity
training labels because the visitor is uncertain; standings for
named dishes remain unchanged. `unreviewed_count` counts visible visits with no
human name or global verdict; `unnamed_count` remains a dish count for compatibility.
Verdict returns the previous Visit with `verdict: {kind, by, at, of_visit_id}` (`by` is null for operator),
`hidden` (true for cant_tell), and false can_* flags. Closed weeks refuse verdicts.
The operator can undo global verdicts with
`POST /api/league/visits/{id}/undo-verdict {}`; scores and eligibility restore.
Skipped visits are scoped to the resident, or the shared operator identity.

Identity teaching uses one continuously tracked, in-reach arrival visitor, even for
multi-dish visits. All journal observations must be unambiguous; the saved anchor
must match a depicted person inside the arrival window. Naming and operator naming
restore that anchor's outfit/head/face/stature cues and label only those cues.
Later proof/mover captures cannot erase a human-labelled arrival. Disputes retract
labels immediately; `not_them` leaves them unlabelled; `them` restores the same anchor.
`not_dish`, `clean` and `duplicate` retract labels through Store's correction transaction.
Operator verdict undo restores the pre-verdict human labels; it never labels a guess.

The installed identity timer already runs `fit-identity` nightly at 04:30, like the
04:45 probe timer. Refit requires 40 independent human-labelled visits across at
least three local days. It excludes the entire test day from both the gallery and
weight training, also excluding each training query's day and duplicate captures.
Only a strict improvement in correct held-out predictions (abstentions included)
can replace the current weights. Every successful evaluation publishes a revision-bound
display gate, including rejected refits. No inference models are loaded by the fitter.

## Join, media and errors

The operator creates `{HOUSE_SITE_ORIGIN}/join#<token>` on Housemates: 7 days,
single use, hash stored, new link revokes the old. Public
`POST /house/api/join {"token":"...","password":"..."}` (8–256 characters)
returns `{"login":"resident-slug"}`. Admin `POST /auth/v1/admin/users` creates
`<slug>@highest-house.ezzyrappeport.com` with `email_confirm=true`; an existing
`auth_user_id` uses `PUT /auth/v1/admin/users/{id}` to reset the password. No email.
Slugs are persistent, unique ASCII; join uses the returned slug directly.
There is no public roster or login-name endpoint. Sign-in accepts "Your name" text,
normalizes case/NFKD to ASCII, replaces non-alphanumeric runs with `-`, trims outer
hyphens and limits the base to 40 characters (empty becomes `housemate`), matching
`slug_for`. Collision-suffixed logins use their assigned slug. Authentication failures
show the same generic name/password error. Only the last successful login name is
remembered in localStorage (best-effort; no password). Failed
upstream calls leave tokens usable. Passwords/tokens never enter events or logs.

Media: absolute `{HOUSE_PUBLIC_ORIGIN}/house/media/{id}?exp=<unix>&sig=<hmac>`,
HMAC-SHA256 over `id:exp` with root `house-media.key` (0600), at most 30 minutes
and no later than evidence expiry. ID/expiry tampering fails. A visible live or
frozen week's still image, or a shared filmstrip with a visible member, is required.
Expired/deleted media never resurrect. `Cache-Control: private, no-store`.

An optional `w=640` requests a snapshot preview with the same signed authority.
No other width is accepted. It preserves aspect ratio, never enlarges a small
source, and leaves filmstrips and the original URL unchanged. Previews cache only
in RAM (128 entries, at most 8 MiB); every request still checks signature, current
visibility and retention before reading that cache. No derivative files extend
photo retention. Clients reserve the original aspect ratio and zoom the original.

House requests admit at most two handlers and twenty asynchronous waiters, with
a two-second admission timeout, before authentication can occupy sync workers or
wait on the camera lock. A response releases its slot before streaming to a slow
phone. House sync handlers run at nice 10 on Linux; camera threads retain their
own priorities. Existing per-account read/media budgets also apply. Week and
teaching-feed GETs negotiate gzip at level 1. Small account reads and JPEG
responses bypass compression. No household JSON or signed-media response is
publicly cacheable.
Errors: `{"detail":"plain sentence"}`; 400 invalid input, 401 sign-in, 403 forbidden,
404 absent/ineligible, 409 closed/conflicting, 429 rate limit, 503 auth unavailable.

Private operator routes retain cookie + CSRF under `/api/league`: `GET week`
(same response, local authenticated media URLs), `GET config` (`rules,on_duty,start_week`),
`PATCH rules` (full rules), `POST duty` (`resident_id` or null), `GET accounts`
(`[{resident_id,has_account}]`), `POST residents/{id}/join-link` (`url,expires_at`),
and dish/visit `name` and `settle`, visit `verdict` and `undo-verdict`. No money features.

Operator home additions (never exposed under `/house/`):

- `GET /api/league/share?start=YYYY-MM-DD` returns an authenticated, no-store,
  1080px-wide JPEG attachment from the league result. Open weeks are provisional;
  closed weeks use their frozen duty assignment, including no-pick carryovers.
  Demo and snapshot images are marked. No household photos are included.
- `GET /api/operator-status` returns only mode, server time, camera status,
  detection enabled, last analysed time (nullable) and approximate saved footage
  hours (nullable on read failure). Recording gaps do not count as footage.
- `GET /api/proofs?ready_only=true` returns `{items,people}` without building
  incomplete sequences or loading search progress. The default contract remains
  available to existing consumers. House review exclusions also suppress proofs
  and named-proof totals until the exclusion is undone.

## Review evidence and priority

Each visit includes `evidence: "strong"|"fair"|"weak"` and `reason: string|null`.
`visits` is ordered strong → fair → weak, then newest `at` first within a tier
(visit ID breaks exact time ties). Both review clients walk this server order.
The rating is a review aid, never a responsibility, dirty-condition or scoring
verdict. It changes no `can_*` permissions or resident verdict restrictions.

- **Strong:** a retained arrival filmstrip records the arrival; at least one of
  its frames has a person in reach; every canonical member passes the existing
  kitchen probe's `proof_threshold` (or its adopted threshold for older probes);
  and no member near-matches an active remembered object in the same area/role.
- **Fair:** a retained strip has either arrival or person context, or retained
  photo evidence has a confident kitchen-probe dish verdict. Missing arrivals
  can therefore be fair; clients still warn to name only someone you know.
- **Weak:** no useful retained signal, including no retained photos/filmstrip.
  Reasons use plain words, e.g. `Arrival not recorded`, `No one in the photos`,
  or `Arrival photos unavailable` when a recorded strip has expired.
  Missing probe/features never invent a confident verdict.

The fair fallback intentionally keeps confident dishes with a saved later photo
visible when arrival footage is missing. Requiring a strip for fair hid too many
confirmed dishes in the isolated historical evaluation. This does not relax the
four-part strong definition. All canonical members must pass the dish-confidence
check; duplicate aliases do not add votes. Person boxes are the filmstrip worker's
existing reach-filtered detections, never a fresh identity inference.

The server copies only the week's latest linked dish features, active remembered
objects (at most 64), and configured areas under the existing store lock. It
scores those saved features outside the lock using the existing local kitchen
probe and remembered-object cosine cutoff. No pixels are decoded, no image inference runs and no model is trained. Missing/malformed probes fail conservatively. The rating
reflects currently retained evidence and the current adopted probe/memory; it is
not part of the frozen score snapshot. Retention may reduce it on later reads.
Read budgets, signed-media visibility and the tunnel listener remain unchanged.

Week responses also include `clear_count` (strong + fair unnamed, undisputed
visits) and `weak_count` (weak unnamed, undisputed visits), after the viewer's
private skips. `unreviewed_count` is their sum. A closed week's weak unnamed
visits contribute zero to pending counts, but remain in `visits`/history. Closed
names, points, rules and duty picks remain immutable. Unnamed visits always score
zero, regardless of rating; nobody needs to empty the queue.

The headline is “N clear ones to name”. A quiet “+M with weak evidence” control
reveals weak visits for the current review session. They remain optional and come
after strong/fair visits. Closed pages show history without a naming prompt.

### Phone evidence images and re-rendering

Derived proof collages and arrival filmstrips now use a 1080px-wide adaptive,
stacked layout. Do not assume six frames or a fixed aspect ratio. The existing
`filmstrip_width`/`filmstrip_height` and `photo_width`/`photo_height` are the actual
JPEG dimensions and remain the layout contract; URLs and response shapes are
unchanged. Text is at least 40 image pixels (14.4 CSS pixels at 390px full width).
Exports use the house site's graphite palette and static system sans fallback,
and are capped at 350,000 JPEG bytes. Weekly exports use the same palette/type.

Only retained stages are drawn. The dish has its own contextual crop; recorded
arrival windows also show up to two visually distinct people views, ranked by
visible frontal face, head size and detail. This visual ranking supplies no name,
responsibility, dish admission or score. Missing-arrival strips never select a
nearby visitor. Original full scenes remain available on the record. Stage
checks, timestamps and source hashes still refer to the saved original stills;
`render_sources` documents display-only buffer replacements and their estimated
times. The renderer only uses buffer frames within 0.75 seconds of a saved capture
and with more source pixels; legacy record times keep their saved stills.

Filmstrip metadata `frames` contains the retained source selections in time order;
the image presents dish comparison first and then people context, with actual
decoder times labeled approximate. Re-rendering preserves visit membership,
identity bindings, evidence ids and expiry. No models for dish detection or
identity are loaded by the re-render command.

Run on an explicitly selected data root after deployment (first command is
read-only; this task has not run either command on the live Jetson):

```sh
python -m wash_dishes.rerender --root /path/to/live --dry-run
python -m wash_dishes.rerender --root /path/to/live --apply --limit 100
```

The default is dry-run. Scope is non-archived open records plus records with a
this-week deadline, excluding dismissed/disputed records. `--limit` bounds image
actions per invocation; `next_offset` and `--offset` allow bounded continuation. Expired media is not revived. Missing arrival endpoints
leave the old strip intact and appear in `skipped`; sequences keep retained saved
stills when video is gone. Apply forces a new render of selected derived images,
without changing scoring, identities, admission or visit membership. Review
checks are preserved when the original stage signature is unchanged.

## Help the camera and nightly learning

Teaching is separate from league scoring. `learning_questions` freeze the source capture,
box, task and interested residents. `learning_labels` keep actor, source, timestamp,
answer and undo tombstone; nothing is deleted to undo an answer. `learning_asks`
reserves at most 12 cards per resident per UTC day. New owners are checked on reads
and writes as well as the original interested parties. Questions are ranked by stored
probe uncertainty and recurrence, without running image models in an API request.

- `GET /house/api/learning`: authenticated card batch, personal label count, house
  truth count, real run history and last adoption date. Issuing a card consumes a daily
  reservation, including skips. Reloading the same card does not consume another.
- `POST /house/api/learning/{question_id}/answer`: `{answer,value?}`. Tasks: `present`
  (`yes/no`), `kind` (`plate/bowl/cup/pan/pot/utensil/not_dish`), `same`
  (`same/different`), `identity` (`me/not_me`) and `missed` (`box`, with
  `value:{kind,box:[x,y,width,height]}`). Coordinates are normalized; missed-box
  agreement uses a 2.5% grid. `unsure` records a skip, never truth. Returns label ID
  and `pending/trusted/conflict` state. A question must have been issued to the actor.
- `POST /house/api/learning/labels/{label_id}/undo`: `{}`; only the author can undo.
- `POST /house/api/visits/{visit_id}/feedback`: `{task:"present"|"kind"|"missed"}`;
  flags a currently visible visit for independent correction. An interested person
  can flag an error, but cannot confirm it. Returns `independent_review`.
- `GET /house/learning-media/{question_id}?actor=&exp=&sig=`: signed, expiring media
  capability, restricted to an issued question, active independent resident and
  retained source evidence. `other=1` reads the second picture of a pair.
- Operator session + CSRF: `GET /api/learning` returns runs and label conflicts;
  `POST /api/learning/{question_id}/resolve` records an explicit operator answer;
  `POST /api/learning/labels/{label_id}/undo` withdraws it;
  `POST /api/learning/rollback/{probe|identity|detector}` restores the previous model.
  The compact Learning panel is in Settings.

Two independent residents must agree on training truth. A resident with an ownership,
original-owner, suggested-owner, naming or dispute stake cannot confirm the label.
Conflicting answers are excluded regardless of majority, until an operator resolves
them. Self-identification identifies only a person crop, never ownership or points.
Names, disputes and dish verdicts append through `learning.capture_action` within the
existing transaction. `learning.add_question` is the server-only seam for other lanes
to enqueue paired dishes, model disagreements and anchored person crops.

`learn-nightly` runs at 04:45–04:59 local time under a persisted daily claim and
nonblocking filesystem lock, at low priority. `scripts/install-learning-timer.sh`
is an explicit replacement for the three legacy timers; installing it disables those
timers. It is not installed by starting the app. **The worker never stops the camera**:
when the current camera heartbeat shows people, less than a minute of quiet, or stale/unknown observations, it records `skipped_busy`. Feature-only probe and identity jobs can run while an active camera is quiet.
On the current memory-limited Jetson, detector training therefore needs an independently
scheduled camera-off window. This installer does not create that window or restart
anything. Reloading the detector in a running camera remains an operator action.

Only trusted labels export. Whole capture days (one in five, fixed by UTC ordinal)
are permanently reserved, grouped by visit obligation. Each run trains on earlier
nonreserved days with a full day gap and checks the latest reserved day. Raw answers,
conflicts, skips and undone labels never reach trainers. The candidate root contains
only training rows; existing probe, identity and detector functions are called there.
Partial dish corrections train on bounded crops so unreviewed surroundings are not
negative examples. Same/different answers are exported for the tracking lane, not
silently applied to inventory. Evaluation must contain at least 20 paired examples;
the gain must exceed both two paired standard errors and 2 percentage points. The
previous artifact is retained, the promoted artifact is evaluated again, and a readback
regression immediately restores it. Run receipts include training/test IDs, counts,
metrics, candidate digest and adoption decision. Undo during training rejects that run.
No UI accuracy claim is shown without an adopted `learning_runs` measurement.
