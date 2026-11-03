# House league browser proof

From this checkout, using the existing Python environment:

```sh
/Volumes/Neural/WashDishes/.venv/bin/python scripts/house_e2e/run.py
```

The default command runs the infrastructure checks, then the full product journey.
Exit **0** means everything requested in that mode passed; **1** means a failure;
**2** means the foundation passed but product lanes are missing. It never reports
the absent product journey as green. To explicitly run just the foundation today:

```sh
/Volumes/Neural/WashDishes/.venv/bin/python scripts/house_e2e/run.py --foundation
```

Each run creates a new private directory under
`/Volumes/Neural/dishwatch-scratch/houseleague/laneE/`. Its `report.json` records
executed/pending coverage, checkout HEAD and SHA-256 hashes of harness sources.
`*-steps.json` indexes screenshots; failed steps capture every open actor page.
`latest-run.txt` points to the most recent directory. Logs, profiles, passwords,
signing keys, SQLite databases and all images remain there. Do not commit them.
All owned servers and Chrome processes stop on success or failure. The three
loopback origins use HTTPS with a per-run certificate: Python/Node trust that
certificate explicitly and Chrome pins its public key for this run. No product
origin validation or TLS verification is disabled. `SUPABASE_SECRET_KEY` uses
the fake Auth admin key required by the merged API.

## Dependencies

No global packages or browser downloads are needed on this machine. The runner
finds existing `playwright-core` under `/Volumes/Neural/NexaRad/node_modules/.pnpm`
and uses installed Google Chrome over CDP. Override package and Chrome paths with
`HOUSE_E2E_PLAYWRIGHT` and `HOUSE_E2E_CHROME` if needed. Python needs the app's
dependencies plus `httpx`, `PyJWT`, and `cryptography` (already in the specified
environment).

The probe uses the site's vendored supabase-js UMD file if found at
`house-site/vendor/supabase.js` or `supabase.min.js`, otherwise the scratch copy.
`HOUSE_E2E_SUPABASE_JS` can point to another existing UMD file. The foundation
was exercised with real **supabase-js 2.118.0**, installed only in scratch:

```sh
npm install --prefix /Volumes/Neural/dishwatch-scratch/houseleague/laneE/deps \
  --cache /Volumes/Neural/dishwatch-scratch/houseleague/laneE/npm-cache \
  --no-audit --no-fund --save-exact @supabase/supabase-js@2.118.0
```

This optional setup contacts npm only. Harness execution contacts loopback only.
The app child blocks external DNS/connections; Chrome aborts external page
requests and fails the run if any are attempted. No real Supabase, Cloudflare,
Vercel, camera or Jetson is used. Inference/recording workers are disabled.

## Fixture and coverage

`seed.py` uses the real store and media APIs, a fresh database, ten specified
residents, a one-minute fixture deadline, and visibly synthetic JPEGs. The current
house week has three visits: pan + plate + bowl (**4**), cup (**1**), pot (**2**).
A fourth visit belongs to a week closed two Mondays ago. The grouped arrival
evidence has kind `filmstrip`, label `arrival`, and capture metadata containing
`visit_id`, `obligation_ids`, `frames`. Andrew is the first visit's camera guess.
The command reads residents, dishes and JPEGs back through authenticated HTTP.
Times follow the current Monday in America/Los_Angeles; it refuses the first
five minutes of Monday when a current-week overdue fixture cannot yet exist.

The fake Auth implements ES256/JWKS, password and refresh grants, admin create
and password update, user lookup, settings and logout. Admin calls require a
fresh per-run service key. Fake Auth is an in-memory protocol fixture, not a
general GoTrue emulator or a production security implementation.

The full journey opens independent 390×844 Chrome contexts for Jadon, Andrew
and Daniel. Operator session/CSRF creates join links through the actual operator
API. All visit mutations happen through UI controls and their real HTTP
responses are checked. API reads assert exact totals **4:1 → 0:1 → 4:1 → 0:1**,
return to unnamed, and **4:3 with no pick** (insufficient lead). Other residents
must remain at zero. The script checks who can dispute/settle, past-week naming
refusal, JWT expiry/forgery/audience/issuer, missing auth, signed media binding
and expiry, operator tunnel isolation, sign-out and name-picker sign-in.

Media expiry uses the product's own freshly signed URL and real verifier. A
scratch file advances only the isolated app child's wall clock beyond `exp`;
the same URL must fail, then succeed again when the clock is restored. The
verifier, secret, signature and expiry policy are never replaced. Monotonic
timeouts stay untouched. Foundation verifies clock advancement/restoration.

## Merge handoff: authored, not yet executed

At base `93f8541`, Lane B's API and Lane C's site do not exist. The full journey
has therefore **not** run. Before claiming full proof after merging, reconcile
these explicit integration assumptions against `docs/house-api.md` and the site:

- The site loads `/config.js` with `window.HOUSE_CONFIG = {supabaseUrl,
  supabaseAnonKey, apiBase}`. For a different config shape, pass
  `--config-path /actual/path.js --site-config /scratch/path/local-config.js`.
  The template replaces literal `AUTH_URL`, `API_URL`, `ANON_KEY` with local
  values; no product source edit is needed. The real site's SDK remains real.
- Operator join route spelling is discovered from the running app's POST routes
  under `/api/` containing `join`. Exactly one is required. Resident id comes
  from the path or `{resident_id}` body; its result supplies `token`, `join_token`,
  `url`, `join_url`, or `link`. Ambiguity fails with the discovered routes.
- Lane A's capture metadata must be the grouping input. If the merged lane
  exposes a dedicated visit creation API, update only `seed.py` to use it.
  The full journey requires exactly the three grouped dish ids and four points.
- **Seed a non-null current duty person using Lane B's actual store API in
  `seed.py`.** That API/schema is unavailable in this checkout. The journey
  deliberately fails if duty is null so retention is tested meaningfully.
- Cards use `data-visit-id`, or an accessible `article` containing their unique
  title. The camera button is explicitly a guess/confirmation (text or
  `button[data-guess]` / `button.guess`), rather than a generic name chip.
  `--ui-contract /scratch/path/ui.json` overrides regex strings for accessible
  labels: `join`, `nameTab`, `guess`, `signOut`, `signIn`, `dispute`,
  `disputeSubmit`, `settle`, `them`, `notThem`. Defaults live in `browser.mjs`.
- Standings expose numeric `points` and a resident id; `pick`, `resident` and
  `guess` expose a resident id/object or null. `unnamed_count` counts visits.
  A disagreement with the merged documented contract needs an adapter change,
  not relaxed scores, skipped interactions, or a false green.

Today's green coverage is the foundation: fake Auth through real supabase-js
and Chrome, ES256/JWKS checks, admin create/update, synthetic seeding, actual
app/operator startup, media readback and isolated clock control. The product
journey, its UI selectors, duty fixture and all house API security checks remain
pending the merge and a successful default command.
