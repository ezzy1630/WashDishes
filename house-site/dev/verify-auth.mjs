// Run the actual vendored Supabase client against intercepted Auth HTTP responses.
import { createRequire } from "node:module";
import assert from "node:assert/strict";
import { writeFile, mkdir } from "node:fs/promises";
import { SUPABASE_URL } from "../config.js";
const scratch =
  process.env.DISHWATCH_SCRATCH ||
  "/Volumes/Neural/dishwatch-scratch/houseleague/laneC";
const output = process.env.DISHWATCH_OUTPUT || scratch;
const require = createRequire(`${scratch}/tooling/package.json`),
  { chromium } = require("playwright-core");
const origin = process.env.MOCK_ORIGIN || "http://127.0.0.1:4317",
  authOrigin = "https://lane-c-auth.supabase.co";
await mkdir(`${output}/tmp`, { recursive: true });
process.env.TMPDIR = `${output}/tmp`;
const browser = await chromium.launch({
  executablePath:
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true,
});
const context = await browser.newContext(),
  page = await context.newPage(),
  calls = [],
  issues = [];
page.on("pageerror", (e) => issues.push(e.message));
page.on("console", (m) => {
  if (m.type() === "error") issues.push(m.text());
});
const user = {
  id: "00000000-0000-4000-8000-000000000001",
  aud: "authenticated",
  role: "authenticated",
  email: "ezzy@highest-house.ezzyrappeport.com",
  app_metadata: { provider: "email", providers: ["email"] },
  user_metadata: {},
  created_at: "2026-11-01T00:00:00Z",
};
function token(label) {
  return [
    Buffer.from(JSON.stringify({ alg: "HS256", typ: "JWT" })).toString(
      "base64url",
    ),
    Buffer.from(
      JSON.stringify({
        sub: user.id,
        exp: Math.floor(Date.now() / 1000) + 3600,
        label,
      }),
    ).toString("base64url"),
    "test-signature",
  ].join(".");
}
const first = token("first"),
  fresh = token("fresh");
await page.route(`${origin}/config.js`, (r) =>
  r.fulfill({
    contentType: "text/javascript",
    body: `export const SUPABASE_URL=${JSON.stringify(authOrigin)};export const SUPABASE_PUBLISHABLE_KEY='sb_publishable_local_interception_only';export const HOUSE_API_ORIGIN=${JSON.stringify(origin)};`,
  }),
);
await page.route(`${origin}/`, async (r) => {
  const response = await r.fetch();
  await r.fulfill({
    response,
    headers: {
      ...response.headers(),
      "content-security-policy": response
        .headers()
        ["content-security-policy"].replaceAll(SUPABASE_URL, authOrigin),
    },
  });
});
await page.route(`${authOrigin}/**`, async (r) => {
  const url = new URL(r.request().url());
  calls.push({ path: url.pathname, grant: url.searchParams.get("grant_type") });
  if (url.pathname.endsWith("/token")) {
    const body = r.request().postDataJSON(),
      refresh = url.searchParams.get("grant_type") === "refresh_token";
    if (refresh) assert.equal(body.refresh_token, "test-refresh-token");
    else
      assert.deepEqual(body, {
        email: user.email,
        password: "house-demo",
        gotrue_meta_security: {},
      });
    await r.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        access_token: refresh ? fresh : first,
        refresh_token: "test-refresh-token",
        expires_in: 3600,
        token_type: "bearer",
        user,
      }),
    });
  } else if (url.pathname.endsWith("/logout")) await r.fulfill({ status: 204 });
  else throw new Error(`Unexpected Auth request: ${url.pathname}`);
});
await page.route(`${origin}/house/api/**`, async (r) => {
  const headers = r.request().headers();
  assert.ok(
    [first, fresh].includes(headers.authorization?.slice(7)),
    "real access token on API call",
  );
  await r.fulfill({
    response: await r.fetch({
      headers: { ...headers, authorization: "Bearer mock:ezzy" },
    }),
  });
});
try {
  await page.goto(origin);
  await page.getByLabel("Your name").fill("ezzy");
  await page.getByLabel("Password", { exact: true }).fill("house-demo");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.getByRole("heading", { name: "This week", exact: true }).waitFor();
  assert.equal(
    await page.evaluate(
      () => JSON.parse(localStorage.getItem("highest-house-auth")).access_token,
    ),
    first,
  );
  await page.evaluate(() => {
    const session = JSON.parse(localStorage.getItem("highest-house-auth"));
    session.expires_at = 1;
    localStorage.setItem("highest-house-auth", JSON.stringify(session));
  });
  await page.reload();
  await page.getByRole("heading", { name: "This week", exact: true }).waitFor();
  assert.equal(
    await page.evaluate(
      () => JSON.parse(localStorage.getItem("highest-house-auth")).access_token,
    ),
    fresh,
  );
  assert.ok(calls.some((c) => c.grant === "refresh_token"));
  await page.locator(".tab", { hasText: "Me" }).click();
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await page.getByRole("button", { name: "Sign in", exact: true }).waitFor();
  assert.equal(
    await page.evaluate(() => localStorage.getItem("highest-house-auth")),
    null,
  );
  assert.deepEqual(issues, []);
  await writeFile(
    `${output}/auth-verification.json`,
    JSON.stringify(
      {
        checks: [
          "official client password sign-in",
          "slug to hidden email",
          "bearer forwarded to house API",
          "persisted session",
          "expired session refresh",
          "local sign-out clears session",
        ],
        calls,
        issues,
        browser: await browser.version(),
      },
      null,
      2,
    ),
  );
  console.log(
    "PASS actual vendored Supabase client: password sign-in, session persistence/refresh, bearer API, sign-out. Auth HTTP intercepted; no Supabase resources used.",
  );
} finally {
  await browser.close();
}
