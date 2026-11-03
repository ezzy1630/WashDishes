# Vendored browser client

`supabase-js-2.118.0.js` is a bundled ESM build of **@supabase/supabase-js 2.118.0** and its locked dependencies, produced with **esbuild 0.28.2**, browser platform, ES2022 target, minified, with legal comments retained. The app imports this local file; it makes no runtime CDN request. Dependency licenses are in `LICENSES.txt`.

SHA-256: `a5e5632da0e4538be06950acbfab62b76d68af8abea86dfa41d3710773c0a4f7`.

The adjacent `.d.ts` re-exports the official pinned package's declarations for development checks. It adds no runtime dependency. `../dev/package-lock.json` pins the reproduction toolchain and transitive dependencies.

To reproduce, install that lockfile into the external scratch tooling directory as described in `../README.md`, then run `node house-site/dev/vendor.mjs` from the repository root. Review and update the recorded digest if changing the bundle. The ordinary site has no install or build step.

Auth methods follow the [official sign-in](https://supabase.com/docs/reference/javascript/auth-signinwithpassword), [session](https://supabase.com/docs/reference/javascript/auth-getsession), and [auth-state](https://supabase.com/docs/reference/javascript/auth-onauthstatechange) contracts. The browser test uses the real checked-in client with intercepted Auth HTTP responses; it doesn't provision or contact a Supabase project.
