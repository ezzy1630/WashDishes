// Run with external tooling; see README. All output and browser profiles live in scratch.
import { createRequire } from "node:module";
import { mkdir, writeFile, readFile } from "node:fs/promises";
import assert from "node:assert/strict";
const scratch =
  process.env.DISHWATCH_SCRATCH ||
  "/Volumes/Neural/dishwatch-scratch/houseleague/laneC";
const require = createRequire(`${scratch}/tooling/package.json`);
const { chromium } = require("playwright-core");
const { default: AxeBuilder } = await import(
  `${scratch}/tooling/node_modules/@axe-core/playwright/dist/index.mjs`
);
const origin = process.env.MOCK_ORIGIN || "http://127.0.0.1:4317";
await mkdir(`${scratch}/screenshots`, { recursive: true });
await mkdir(`${scratch}/tmp`, { recursive: true });
process.env.TMPDIR = `${scratch}/tmp`;
const browser = await chromium.launch({
  executablePath:
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true,
  args: ["--disable-background-networking"],
});
const issues = [],
  checks = [],
  shots = [];
const control = async (body) => {
  const r = await fetch(`${origin}/dev/control`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  assert.equal(r.status, 200);
};
async function check(name, run) {
  await run();
  checks.push(name);
  console.log(`PASS ${name}`);
}
function observe(page) {
  page.on("pageerror", (e) => issues.push(e.message));
  page.on("console", (m) => {
    if (
      m.type() === "error" &&
      !m.text().includes("503") &&
      !m.text().includes("400")
    )
      issues.push(m.text());
  });
}
async function signedIn(page, slug = "ezzy") {
  await page.goto(`${origin}/?mock=1`);
  await page.getByLabel("Your name").selectOption(slug);
  await page.getByLabel("Password", { exact: true }).fill("house-demo");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.getByRole("heading", { name: "This week", exact: true }).waitFor();
}
async function navigation(page, label) {
  await page.getByRole("link", { name: label, exact: true }).click();
  await page.locator("h1").waitFor();
}
async function capture(page, name, size, theme) {
  await page.evaluate(() => document.fonts.ready);
  await page.locator("img").evaluateAll((imgs) =>
    Promise.all(
      imgs
        .filter((img) => !img.closest("details:not([open])"))
        .map((img) =>
          img.complete
            ? null
            : new Promise((resolve) => {
                img.onload = resolve;
                img.onerror = resolve;
              }),
        ),
    ),
  );
  await page
    .locator("img")
    .evaluateAll((imgs) =>
      Promise.all(
        imgs
          .filter((img) => !img.closest("details:not([open])"))
          .map((img) => img.decode().catch(() => {})),
      ),
    );
  await page.evaluate(async () => {
    for (const img of document.querySelectorAll("img")) {
      if (img.closest("details:not([open])")) continue;
      img.scrollIntoView();
      await new Promise(requestAnimationFrame);
    }
    window.scrollTo(0, 0);
    await new Promise(requestAnimationFrame);
    await new Promise(requestAnimationFrame);
  });
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth,
  );
  assert.equal(
    overflow,
    false,
    `${name} ${size} ${theme}: horizontal overflow`,
  );
  const small = await page
    .locator("button:not(:disabled), nav a, input, select, textarea, summary")
    .evaluateAll((nodes) =>
      nodes
        .filter(
          (n) =>
            n.getBoundingClientRect().height > 0 &&
            n.getBoundingClientRect().height < 43.5,
        )
        .map(
          (n) =>
            `${n.tagName}: ${n.textContent?.slice(0, 50)} (${n.getBoundingClientRect().height})`,
        ),
    );
  assert.deepEqual(small, [], `${name}: touch targets`);
  const audit = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  assert.deepEqual(
    audit.violations.map((v) => ({
      id: v.id,
      nodes: v.nodes.map((n) => n.target),
    })),
    [],
    `${name} accessibility`,
  );
  const filename = `${scratch}/screenshots/${name}-${size}-${theme}.png`;
  await page.screenshot({ path: filename, fullPage: true });
  shots.push(filename);
}
try {
  for (const [width, height] of [
    [390, 844],
    [1280, 800],
  ])
    for (const theme of ["light", "dark"]) {
      await control({ reset: true });
      const context = await browser.newContext({
          viewport: { width, height },
          colorScheme: theme,
          reducedMotion: "reduce",
        }),
        page = await context.newPage();
      observe(page);
      const size = `${width}x${height}`;
      await page.goto(`${origin}/?mock=1`);
      await page.getByLabel("Your name").selectOption("ezzy");
      await capture(page, "sign-in", size, theme);
      await page.goto(`${origin}/join?mock=1#demo-invite`);
      await capture(page, "join", size, theme);
      await signedIn(page);
      await capture(page, "this-week", size, theme);
      await navigation(page, "Name dishes");
      await page
        .getByRole("heading", { name: "Whose dishes are these?" })
        .waitFor();
      await capture(page, "name-visits", size, theme);
      await page
        .getByRole("button", { name: "Same as another", exact: true })
        .click();
      await capture(page, "duplicate-picker", size, theme);
      await page
        .getByRole("button", { name: "Same as another", exact: true })
        .click();
      await page.getByText("Reviewed this week (5)", { exact: true }).click();
      await page
        .getByText("Marked not a dish by Daniel", { exact: true })
        .waitFor();
      await capture(page, "reviewed-visits", size, theme);
      await navigation(page, "My dishes");
      await capture(page, "my-dishes", size, theme);
      await navigation(page, "Past weeks");
      await capture(page, "past-weeks", size, theme);
      await page.getByRole("button", { name: /Oct 26/ }).click();
      await page
        .getByText("Jadon would need 2 more points for a clear pick.")
        .waitFor();
      await capture(page, "no-pick", size, theme);
      await navigation(page, "This week");
      await control({ offline: true });
      await page.evaluate(() => window.dispatchEvent(new Event("focus")));
      await page
        .getByText("Dish Watch is offline right now", { exact: true })
        .waitFor();
      await capture(page, "offline", size, theme);
      await control({ offline: false });
      await page.getByRole("button", { name: "Try again" }).click();
      await page
        .getByText("Dish Watch is offline right now", { exact: true })
        .waitFor({ state: "hidden" });
      await context.close();
      console.log(`PASS screenshots ${size} ${theme}`);
    }
  await control({ reset: true });
  const context = await browser.newContext({
      viewport: { width: 390, height: 844 },
    }),
    page = await context.newPage();
  observe(page);
  await check(
    "sign-in rejects bad password; ten alphabetical names",
    async () => {
      await page.goto(`${origin}/?mock=1`);
      await page.getByLabel("Your name").selectOption("ezzy");
      const names = await page.locator("select option").allTextContents();
      assert.equal(names.length, 11);
      assert.deepEqual(names.slice(1), [
        "Andrew",
        "Calder",
        "Daniel",
        "Elliot",
        "Ezzy",
        "Henry",
        "Jacob",
        "Jadon",
        "Pedro",
        "Sutton",
      ]);
      await page.getByLabel("Password", { exact: true }).fill("wrong");
      await page.getByRole("button", { name: "Sign in", exact: true }).click();
      await page
        .getByRole("alert")
        .filter({ hasText: "didn’t match" })
        .waitFor();
      await page.getByLabel("Password", { exact: true }).fill("house-demo");
      await page.getByRole("button", { name: "Sign in", exact: true }).click();
      await page
        .getByRole("heading", { name: "This week", exact: true })
        .waitFor();
    },
  );
  await check(
    "visit guess is unconfirmed; confirm names all dishes; next visit; skip",
    async () => {
      await navigation(page, "Name dishes");
      const before = await page.evaluate(() =>
        fetch("/house/api/week", {
          headers: { Authorization: "Bearer mock:ezzy" },
        }).then((r) => r.json()),
      );
      assert.equal(before.visits[0].resident, null);
      assert.equal(before.unnamed_count, 3);
      await page
        .getByRole("button", { name: "Camera thinks Jadon — tap to confirm" })
        .click();
      await page.locator('article[data-dish-id="visit-103"]').waitFor();
      const after = await page.evaluate(() =>
        fetch("/house/api/week", {
          headers: { Authorization: "Bearer mock:ezzy" },
        }).then((r) => r.json()),
      );
      assert.equal(after.unnamed_count, 1);
      assert.equal(
        after.dishes.filter(
          (d) => [101, 102].includes(d.id) && d.resident?.name === "Jadon",
        ).length,
        2,
      );
      await page.getByRole("button", { name: "Can’t tell" }).click();
      await page.getByRole("heading", { name: "That’s the round." }).waitFor();
      await page
        .getByRole("button", { name: "Look at skipped visits" })
        .click();
      await page.getByRole("button", { name: "Andrew", exact: true }).click();
      await page
        .getByRole("heading", { name: "You’re all caught up." })
        .waitFor();
    },
  );
  await check(
    "four verdicts update the visit; duplicate links another; cant-tell stays uncounted",
    async () => {
      await control({ reset: true });
      await page.goto(`${origin}/name?mock=1`);
      await page
        .getByRole("heading", { name: "Whose dishes are these?" })
        .waitFor();
      await page
        .getByRole("button", { name: "Not a dish", exact: true })
        .click();
      await page.locator('article[data-dish-id="visit-103"]').waitFor();
      await page
        .getByRole("button", { name: "It’s clean", exact: true })
        .click();
      await page
        .getByRole("heading", { name: "You’re all caught up." })
        .waitFor();
      await page.getByText("Reviewed this week (7)", { exact: true }).click();
      await page
        .getByText("Marked not a dish by Ezzy", { exact: true })
        .waitFor();
      await page.getByText("Marked clean by Ezzy", { exact: true }).waitFor();
      let data = await page.evaluate(() =>
        fetch("/house/api/week", {
          headers: { Authorization: "Bearer mock:ezzy" },
        }).then((r) => r.json()),
      );
      assert.equal(data.unreviewed_count, 0);
      assert.equal(
        data.visits.find((v) => v.id === "visit-101").resident,
        null,
      );
      await control({ reset: true });
      await page.reload();
      await page
        .getByRole("button", { name: "Same as another", exact: true })
        .click();
      assert.equal(await page.locator(".duplicate-choice").count(), 6);
      await page.getByRole("button", { name: /Dinner plate/ }).click();
      await page.locator('article[data-dish-id="visit-103"]').waitFor();
      await page
        .getByRole("button", { name: "Can’t tell", exact: true })
        .click();
      await page.getByRole("heading", { name: "That’s the round." }).waitFor();
      data = await page.evaluate(() =>
        fetch("/house/api/week", {
          headers: { Authorization: "Bearer mock:ezzy" },
        }).then((r) => r.json()),
      );
      assert.equal(
        data.visits.find((v) => v.id === "visit-101").verdict.kind,
        "duplicate",
      );
      assert.equal(
        data.visits.find((v) => v.id === "visit-103").verdict.kind,
        "cant_tell",
      );
      assert.equal(data.unreviewed_count, 1);
      assert.equal(data.standings.find((s) => s.name === "Ezzy").points, 2);
      await control({ reset: true });
      await page.reload();
      await navigation(page, "My dishes");
    },
  );
  await check("photo zoom and Escape return focus", async () => {
    await navigation(page, "My dishes");
    const enlarge = page
      .getByRole("button", { name: /Enlarge.*evidence/ })
      .first();
    await enlarge.click();
    await page.locator("dialog[open]").waitFor();
    await page.keyboard.press("Escape");
    await page.locator("dialog[open]").waitFor({ state: "hidden" });
    assert.equal(
      await enlarge.evaluate((el) => document.activeElement === el),
      true,
    );
  });
  await check(
    "dispute persists a typed note; another resident settles it",
    async () => {
      await page.getByRole("button", { name: "That wasn’t me" }).click();
      await page
        .getByLabel("What happened?")
        .fill("I carried it to the sink, but didn’t use it.");
      await page.evaluate(() => window.dispatchEvent(new Event("focus")));
      await page.waitForTimeout(150);
      assert.equal(
        await page.getByLabel("What happened?").inputValue(),
        "I carried it to the sink, but didn’t use it.",
      );
      await page.getByRole("button", { name: "Send dispute" }).click();
      await page
        .getByText("Waiting for another housemate to settle it.")
        .waitFor();
      await page.getByRole("button", { name: "Not them", exact: true }).click();
      await page.getByRole("heading", { name: "All settled here." }).waitFor();
      await page.getByRole("button", { name: "Sign out", exact: true }).click();
      await signedIn(page, "henry");
      await navigation(page, "My dishes");
      await page.getByRole("button", { name: "It was them" }).click();
      await page.getByRole("heading", { name: "All settled here." }).waitFor();
    },
  );
  await check(
    "expired and incomplete invite; mismatch; join and reset auto sign-in",
    async () => {
      await page.getByRole("button", { name: "Sign out", exact: true }).click();
      await page.goto(`${origin}/join?mock=1`);
      await page
        .getByRole("heading", { name: "This link needs a token" })
        .waitFor();
      await page.goto(`${origin}/join?mock=1#expired`);
      await page.getByLabel("New password").fill("new-house-password");
      await page.getByLabel("Confirm password").fill("new-house-password");
      await page
        .getByRole("button", { name: "Set password & come in" })
        .click();
      await page
        .getByText("This house link has expired. Ask Ezzy for a fresh one.")
        .waitFor();
      await page.goto(`${origin}/join?mock=1#demo-invite`);
      await page.getByLabel("New password").fill("new-house-password");
      await page.getByLabel("Confirm password").fill("not-the-same");
      await page
        .getByRole("button", { name: "Set password & come in" })
        .click();
      await page.getByText("Those passwords don’t match yet.").waitFor();
      await page.getByLabel("Confirm password").fill("new-house-password");
      await page
        .getByRole("button", { name: "Set password & come in" })
        .click();
      await page
        .getByRole("heading", { name: "This week", exact: true })
        .waitFor();
      assert.equal(new URL(page.url()).hash, "");
      await page.goto(`${origin}/join?mock=1#demo-reset`);
      await page.getByLabel("New password").fill("reset-house-password");
      await page.getByLabel("Confirm password").fill("reset-house-password");
      await page
        .getByRole("button", { name: "Set password & come in" })
        .click();
      await page
        .getByRole("heading", { name: "This week", exact: true })
        .waitFor();
    },
  );
  await check(
    "empty visits, mine and settlements; missing photos; malformed and first-load offline",
    async () => {
      await control({ empty: true });
      await page.reload();
      await page
        .getByRole("heading", { name: "This week", exact: true })
        .waitFor();
      await navigation(page, "Name dishes");
      await page
        .getByRole("heading", { name: "You’re all caught up." })
        .waitFor();
      await navigation(page, "My dishes");
      await page
        .getByRole("heading", { name: "Nothing named as yours." })
        .waitFor();
      await page.getByRole("heading", { name: "All settled here." }).waitFor();
      await control({ reset: true, missingPhotos: true });
      await navigation(page, "Name dishes");
      await page.reload();
      await page
        .getByText("Pan and pot arrival photos isn’t available.")
        .waitFor();
      await control({ malformed: true });
      await page.reload();
      await page
        .getByText("Dish Watch is offline right now", { exact: true })
        .waitFor();
      await control({ reset: true, offline: true });
      await page.reload();
      await page
        .getByText("Dish Watch is offline right now", { exact: true })
        .waitFor();
      await control({ offline: false });
      await page.getByRole("button", { name: "Try again" }).click();
      await page.getByRole("heading", { name: "Name the dishes" }).waitFor();
    },
  );
  await check(
    "refresh every minute while visible, pause while hidden, refresh on focus",
    async () => {
      await page.clock.install();
      await page.reload();
      await page.getByRole("heading", { name: "Name the dishes" }).waitFor();
      let requests = 0;
      page.on("request", (r) => {
        if (r.url().includes("/house/api/week")) requests++;
      });
      await page.clock.fastForward(60000);
      await page.waitForTimeout(100);
      assert.ok(requests >= 1);
      const before = requests;
      await page.evaluate(() =>
        Object.defineProperty(document, "hidden", {
          configurable: true,
          value: true,
        }),
      );
      await page.clock.fastForward(120000);
      await page.waitForTimeout(100);
      assert.equal(requests, before);
      await page.evaluate(() => {
        Object.defineProperty(document, "hidden", {
          configurable: true,
          value: false,
        });
        document.dispatchEvent(new Event("visibilitychange"));
      });
      await page.waitForTimeout(200);
      assert.ok(requests > before);
    },
  );
  await context.close();
  assert.deepEqual(issues, [], "Browser exceptions/CSP errors");
  await writeFile(
    `${scratch}/verification.json`,
    JSON.stringify(
      { shots, checks, browser: await browser.version(), issues },
      null,
      2,
    ),
  );
  console.log(
    `Verified ${shots.length} screenshots and ${checks.length} flow groups.`,
  );
} finally {
  await browser.close();
  await control({ reset: true });
}
