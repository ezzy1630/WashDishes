# Highest House website

A standalone, phone-first static site. Vanilla ES modules and CSS; no framework, install, build, server function, or runtime CDN. The Jetson remains the source of league decisions, resident IDs, visit grouping, guesses, permissions, and signed media. The website never assigns duty locally.

## Connect the house

Fill the three marked public values in `config.js`. Use a **Supabase publishable key** (`sb_publishable_…`), never a secret or service-role key. Then run `node house-site/dev/prepare.mjs` from the repository root. This local preflight validates the config and copies its two HTTPS origins into the Vercel CSP, so configuration is entered in one place. It changes no remote resources.

When the lead is authorized to deploy, the Vercel project root is `house-site`, with no build command. `vercel.json` rewrites `/join`, `/name`, `/mine`, and `/past` to the SPA and applies restrictive CSP, no-referrer, frame denial, MIME protection, and disabled camera/microphone/location/payment access. The versioned Supabase bundle can be cached immutably; the public config and app documents are not stored. `dev/` is excluded by `.vercelignore`. No infrastructure has been created or deployed by this lane.

The API must allow the website's exact origin through CORS, including `Authorization` and `Content-Type` headers and OPTIONS preflights. Images must be signed absolute HTTPS URLs on `HOUSE_API_ORIGIN` or `SUPABASE_URL`, matching the two CSP image origins. Photos load without a bearer header; API requests carry the fresh Supabase access token. Sign-in accepts a typed name (case-insensitive ASCII slugging matching the server) and remembers the last successful name on this device. Auth maps the slug to `<slug>@highest-house.ezzyrappeport.com`; the UI never displays an email address. Join and reset both use `/join#<token>` and `POST /house/api/join`, followed by password sign-in. The fragment is cleared after successful redemption. Sign-out clears this browser's session.

## Visit contract

`GET /house/api/week` must include both `dishes` and the lead's new `visits` array. Naming, disputes, and settlements operate exclusively on `/house/api/visits/{id}/{name|dispute|settle}`. A visit contains `dish_ids`, `title`, `area`, `at`, `points`, signed media, attribution, `guess`, dispute, and permission flags. `guess.basis` is a plain string. Each visit also includes nullable `verdict: {kind, by: {id, name}, at}`, and the week includes `unreviewed_count`. A guess is visually highlighted, with an explicit tap to confirm; it has no attribution effect before that tap. Every name stays in the alphabetical chip grid, including the outlined camera suggestion. Names come from the week's full ten-resident standings and stay alphabetic. The server must include zero-point housemates there because the public login-name endpoint has been removed.

The home CTA reports **visits** from `unreviewed_count` as “N to review”; the naming flow advances one **visit** at a time. Each visit has the primary person question and four secondary answers: “Not a dish,” “It’s clean,” “Same as another,” and “Can’t tell.” They POST to `/house/api/visits/{id}/verdict` using `not_dish`, `clean`, `duplicate` (plus `of_visit_id`), or `cant_tell`. The duplicate picker shows the other visits’ photos, titles, places, times, and known names. “Can’t tell” records a skip and leaves the visit unnamed. Excluded verdicts appear in review history with their kind and reviewer; signed media remains unavailable for excluded visits. A dish only counts once someone names it. All actions respect server `can_*` flags and refetch the week afterward. Point and lead meters explain thresholds, while the API's `pick` and `on_duty` remain authoritative. Past-week selection refetches the selected week; leaving Past weeks restores the current week.

Only a last-loaded snapshot in memory is kept during an outage. No household results or photos are saved to browser storage. The site refreshes signed URLs on window focus, visibility restoration, and every 60 seconds while visible. Typing a dispute note and enlarging a photo survive background refresh. Auth session storage is managed by Supabase. Unknown and unavailable evidence remains explicit.

## Local development and verification

Use Node 22+ and installed Chrome. All tooling, screenshots, and browser scratch stay under `/Volumes/Neural`. From the repo root, for the supplied lane scratch path:

```sh
mkdir -p /Volumes/Neural/dishwatch-scratch/houseleague/laneC/tooling
cp house-site/dev/package.json house-site/dev/package-lock.json /Volumes/Neural/dishwatch-scratch/houseleague/laneC/tooling/
npm ci --prefix /Volumes/Neural/dishwatch-scratch/houseleague/laneC/tooling
node house-site/dev/mock.mjs
```

Open `http://127.0.0.1:4317/?mock=1`. Type any of the ten names; the initial password is `house-demo`. Join: `/join?mock=1#demo-invite`; reset: `/join?mock=1#demo-reset`. Links are single-use until reset. Mock sign-in is enabled only on loopback plus `?mock=1`; the mock endpoints and fixture server do not deploy. The mock creates synthetic JPEG frames and passes them through the production
`wash_dishes/filmstrip.py` renderer. No private captures are read. It includes
six-frame and four-frame strips, later photos and an arrival-not-recorded visit;
each source frame is labeled synthetic. `dev/fixtures.py` writes only to scratch.
It uses the project's Python environment (override with `PYTHON`).

With the mock running in another process:

```sh
node house-site/dev/check.mjs
node house-site/dev/verify.mjs
node house-site/dev/verify-auth.mjs
```

`DISHWATCH_SCRATCH` overrides the external tooling/output directory; `MOCK_ORIGIN` and the mock server's `PORT` override its loopback address. The verification scripts use the macOS Chrome executable. They check strict JavaScript types against official Supabase declarations; all six screens plus no-pick, offline, duplicate picker, reviewed visits, full-size filmstrip zoom, and pick/override/disabled duty states at 390×844 and 1280×800 in light/dark; axe WCAG A/AA checks, document width, descendant card bounds and text glyph bounds, 44px controls, and ten groups of end-to-end flows including all four verdicts and a real two-finger pinch gesture on an emulated phone. The separate Auth check drives the actual vendored client through password sign-in, bearer API access, persisted-session refresh, and sign-out with intercepted Auth HTTP responses.

Screenshots: `/Volumes/Neural/dishwatch-scratch/houseleague/laneC/screenshots/<screen>-<size>-<theme>.png`, where screens are `sign-in`, `join`, `this-week`, `name-visits`, `my-dishes`, `past-weeks`, `no-pick`, `offline`, `duplicate-picker`, `reviewed-visits`, `filmstrip-zoom`, `duty-pick`, `duty-override`, `duty-disabled`; sizes are `390x844`, `1280x800`; themes are `light`, `dark`. `verification.json` and `auth-verification.json` in the scratch directory record results and Chrome version. Private screenshots remain outside Git and deployment.

Mock controls: local `POST /dev/control` accepts `reset`, `offline`, `empty`, `missingPhotos`, `malformed`, `delay`, and `dutyMode` (`pick`, `carry`, `override`, `disabled`), supporting failure and state checks. Nothing from this endpoint exists in production.

## Design and remaining integration

This extends `../DESIGN.md`: local Mona Sans with its OFL license, graphite dark tokens, compact 24/27px titles, quiet borders, evidence-first composition, and review blue. Light mode uses a warm neutral counterpart. Controls are at least 44px, metadata stays readable, image frames reserve aspect ratios, all images reserve their API-provided intrinsic aspect ratio at the full evidence-column width, zoom uses a native keyboard-dismissable dialog with scrolling, pinch gestures, and labeled zoom controls, and there is no continuous animation. Zero-point housemates collapse into one alphabetic line; threshold copy states points and required lead in words. Current-duty copy uses the API reason and assignment date to distinguish a weekly pick, carry-over, house-vote override, or paused league. The phone layout places the review action before the standings; the desktop layout puts it alongside. The later photo is a zoomable thumbnail after the naming controls on phones, and stays beside the workflow on desktop. Root design files and the operator's existing app are unchanged.

Live Supabase credentials, the tunnel/CORS connection, Lane B's real responses, and Vercel-hosted routing/security headers remain integration work for the lead. Local Auth interception proves the client protocol and session behavior; it does not prove a provisioned account or live authorization. This lane creates no remote resources and performs no deployment, push, or PR.

## Frontend modules

`js/app.js` owns navigation, session lifecycle, refresh, mutation handling, and the shell. Per-screen renderers live in `js/pages/{week,name,mine,past,auth}.js`; shared standings, visit records, and duty/date copy live beside them. Screens receive a typed context from the controller and never import it, keeping module dependencies one-way. `ui.js` and `api.js` remain the shared DOM/media and HTTP boundaries.

Weekly pick headings use the API’s `pick.state` and selected name. Details display
its name-free `pick.reason` verbatim. Meters use `pick.points` and `pick.lead`;
the remaining points come from `max(pick.needed_points, pick.needed_lead)`.
The site validates the structured contract at its API boundary and never rebuilds
the decision from standings or strips a person's name from prose. Week ranges
and chips display Sunday as the inclusive last day, using the API’s exclusive end boundary.

The displayed week close time is formatted directly from the API’s `closes_at`. The mock closes Monday at noon; verification also supplies Tuesday at 3:30 PM to reject a hard-coded or browser-computed close time.
