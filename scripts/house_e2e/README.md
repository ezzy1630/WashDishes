# House league browser proof

From this checkout:

```sh
/Volumes/Neural/WashDishes/.venv/bin/python scripts/house_e2e/run.py
```

Exit **0** means the full journey passed; **1** means a failure; **2** means the
foundation passed but product lanes are missing. `--foundation` explicitly runs
only infrastructure checks. A missing product journey never reports as green.

## Isolation and dependencies

Every run has a new private directory under
`/Volumes/Neural/dishwatch-scratch/houseleague/laneE/`. It contains generated
JPEGs, databases, credentials, CA/certificate, profiles, screenshots and logs.
Nothing is copied from live storage or cameras. All owned servers and Chrome
stop on success or failure; a driver timeout terminates its process group.

All three servers use **HTTPS** with a throwaway CA and a loopback-only leaf
certificate. Python clients and the app child trust the CA through a per-run
SSL context / `SSL_CERT_FILE`; Node uses `NODE_EXTRA_CA_CERTS`. The dedicated
Chrome process accepts only the generated certificate's SPKI pin, with a fresh
scratch profile. No system/browser trust store or production origin validation
is changed. A client without the CA must reject the certificate.

App workers/inference/recording stay disabled. The app child blocks external DNS
and connections. Chrome aborts external page requests and fails the run if any
are attempted. No real Supabase, Cloudflare, Vercel or Jetson is used.

No package install or browser download is needed on this machine. The runner
uses the site's **real vendored supabase-js 2.118.0** for both the probe and
product site, existing `playwright-core` under
`/Volumes/Neural/NexaRad/node_modules/.pnpm`, installed Google Chrome over CDP,
and the specified Python environment (`httpx`, `PyJWT`, `cryptography`, app
dependencies). Overrides: `HOUSE_E2E_PLAYWRIGHT` (package directory),
`HOUSE_E2E_CHROME` (executable), `HOUSE_E2E_SUPABASE_JS` (browser ES module).

## What the command proves

`seed.py` uses the real store/media APIs and `league.override` to seed ten
specified residents and Ezzy on current duty. Three current-week visits contain
pan + plate + bowl (**4**), cup (**1**), pot (**2**). A fourth belongs to a closed
week two Mondays ago. Only synthetic, visibly labeled JPEGs are generated.
The seed uses a one-minute deadline and the real matching-late-photo label.
Arrival evidence uses `filmstrip` / `arrival` and real capture metadata
`{visit_id,obligation_ids,frames}`. Andrew is the first visit's camera guess.
Data and JPEGs are read back through authenticated HTTP before Chrome starts.
Times follow the current house week in America/Los_Angeles; the first five
minutes of Monday are refused because a current-week overdue fixture cannot
yet exist. These fixtures represent distinct dishes with observed timing;
duplicate-alias and already-present/untimed rule changes belong to the parallel
integrator and are not simulated by changing eligibility in this harness.

The in-memory fake Auth implements ES256/JWKS, password/refresh grants, admin
create/password update, user lookup, settings and logout. Admin calls require a
fresh per-run API key. It is a protocol fixture, not a production GoTrue server.

The full journey uses independent **390×844** Chrome contexts for Jadon, Andrew
and Daniel, the real site and API contracts in `docs/house-api.md`:

1. Operator cookie + CSRF creates actual join links; each actor sets a password
   through `/join#token` and lands signed in using real supabase-js.
2. Jadon confirms Andrew through the camera button and names the cup as himself.
   Atomic visit membership, owner/namer audit, **4:1** points and Andrew's pick
   are checked through API reads and rendered standings.
3. Andrew disputes (**0:1**, no pick); the namer and disputer cannot settle.
   Daniel settles “them” (**4:1**) and then a second dispute “not them”
   (**0:1**, back to unnamed). Ezzy remains on duty.
4. Jadon names the pot: **4:3**, insufficient lead, no pick. Closed historical
   week selection renders “Closed” and refuses naming with HTTP 409.
5. Valid auth succeeds; expired/forged/wrong-audience/wrong-issuer/missing JWTs
   fail with 401. A different housemate cannot dispute (403).
6. Signed media works, refuses another evidence id, expires, then works again
   when the isolated server clock is restored. Cache headers are checked.
   A tunnel header refuses the operator dashboard (403) while allowing the
   authenticated house API. Andrew signs out and signs back in via name picker.

Media expiry advances only the isolated app child's wall clock past the URL's
actual signed `exp`; no verifier, secret, signature or expiry policy is replaced.
Monotonic timeouts remain untouched. Foundation tests clock advance/restore.

## Evidence and maintenance

- `report.json`: result, executed/pending coverage, checkout HEAD and hashes of
  harness sources.
- `journey-checks.json`: actual HTTP statuses, exact score/pick snapshots and
  returned atomic naming/settlement records. No JWT/password request bodies.
- `*-steps.json`: step-to-screenshot index. Each step has a 390×844 viewport
  capture and a full-page capture; failed steps capture all actor pages.
- `*-network.json`: blocked external requests and browser page errors (both
  must be empty); `app.log`, browser and Chrome logs provide failure details.
- `latest-run.txt`: path to the latest run. Keep all evidence in scratch.

The local static server substitutes an ES module at `/config.js` with the real
site's `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, `HOUSE_API_ORIGIN` exports;
the product source and SDK are unchanged. Optional `--config-path`,
`--site-config` (substitutes `AUTH_URL`, `API_URL`, `ANON_KEY`) and
`--ui-contract` (accessible-label regex overrides) support deliberate contract
changes. API paths and `data-dish-id` selectors match the merged product.
`unnamed_count` counts dishes; `unreviewed_count` counts visits.

Focused product regression command (scratch-only output):

```sh
PYTHONDONTWRITEBYTECODE=1 TMPDIR=/Volumes/Neural/dishwatch-scratch/houseleague/laneE \
  /Volumes/Neural/WashDishes/.venv/bin/python -B -m pytest -q -p no:cacheprovider \
  --basetemp=/Volumes/Neural/dishwatch-scratch/houseleague/laneE/pytest-merged \
  tests/test_house_api.py tests/test_league.py tests/test_filmstrip.py tests/test_left_out.py
```
