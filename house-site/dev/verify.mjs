// Run with external tooling; see README. All output and browser profiles live in scratch.
import { createRequire } from "node:module";
import { mkdir, writeFile, readFile } from "node:fs/promises";
import assert from "node:assert/strict";
import { inspectLayout } from "./layout.mjs";
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
  shots = [],
  layouts = [];
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
      !m.text().includes("400") &&
      !m.text().includes("409")
    )
      issues.push(m.text());
  });
}
async function signedIn(page, slug = "ezzy") {
  await page.goto(`${origin}/?mock=1`);
  await page.getByLabel("Your name").fill(slug);
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
  if (name === "filmstrip-zoom") {
    const dialog = await page.locator("dialog[open]").evaluate((el) => ({
      scrollHeight: el.scrollHeight,
      clientHeight: el.clientHeight,
      closeTop: el.querySelector("button").getBoundingClientRect().top,
    }));
    assert.equal(
      dialog.scrollHeight,
      dialog.clientHeight,
      "zoom scroll stays inside its image viewport",
    );
    assert.ok(dialog.closeTop >= 0, "zoom Close stays on screen");
  }
  const layout = await inspectLayout(page);
  assert.ok(
    layout.scrollWidth <= layout.clientWidth,
    `${name} ${size} ${theme}: document width ${JSON.stringify(layout)}`,
  );
  assert.deepEqual(
    layout.violations,
    [],
    `${name} ${size} ${theme}: content overflows its card`,
  );
  layouts.push({ screen: name, size, theme, ...layout });
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
  await page.screenshot({
    path: filename,
    fullPage: name !== "filmstrip-zoom",
  });
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
          isMobile: width < 600,
          hasTouch: width < 600,
        }),
        page = await context.newPage();
      observe(page);
      const size = `${width}x${height}`;
      await page.goto(`${origin}/?mock=1`);
      await page.getByLabel("Your name").fill("ezzy");
      await capture(page, "sign-in", size, theme);
      await page.goto(`${origin}/join?mock=1#demo-invite`);
      await capture(page, "join", size, theme);
      await signedIn(page);
      await capture(page, "this-week", size, theme);
      assert.equal(
        await page.locator(".page-heading p").innerText(),
        "Nov 2 – 8 · closes Mon 12 PM",
      );
      assert.equal(
        await page.locator(".pick > p").first().innerText(),
        "7 points · 4 ahead",
      );
      const tiedLeadGate = await page.evaluate(async () => {
        const { getWeek } = await import("/js/api.js");
        const { standings } = await import("/js/pages/standings.js");
        const data = await getWeek();
        const view = standings({
          ...data,
          rules: { ...data.rules, min_lead: 0 },
          pick: {
            resident_id: null,
            name: null,
            state: "tie",
            points: 4,
            lead: 0,
            needed_points: 0,
            needed_lead: 1,
            reason: "Tied for first · needs a 1-point lead",
          },
        });
        return view.querySelectorAll(".gate")[1].textContent;
      });
      assert.match(tiedLeadGate, /Leads by 0 · needs 1/);
      assert.match(tiedLeadGate, /1 to go/);
      assert.deepEqual(
        await page.evaluate(async () => {
          const { weekRange } = await import("/js/pages/league-copy.js");
          return [
            weekRange("2026-11-02", "2026-11-09", "America/Los_Angeles"),
            weekRange("2026-10-26", "2026-11-02", "America/Los_Angeles"),
            weekRange("2026-12-28", "2027-01-04", "America/Los_Angeles"),
            weekRange("2026-03-02", "2026-03-09", "America/Los_Angeles"),
          ];
        }),
        ["Nov 2 – 8", "Oct 26 – Nov 1", "Dec 28 – Jan 3", "Mar 2 – 8"],
        "exclusive dates across month, year and DST boundaries",
      );
      assert.equal(await page.locator(".rankings li").count(), 3);
      assert.equal(await page.locator(".rank-bar path").count(), 0);
      await page.getByText("7 pts · needs 4", { exact: true }).waitFor();
      await page.getByText("Leads by 4 · needs 2", { exact: true }).waitFor();
      for (const mode of ["pick", "override", "disabled"]) {
        await control({ dutyMode: mode });
        await page.evaluate(() => window.dispatchEvent(new Event("focus")));
        await page
          .locator(".duty")
          .getByText(
            mode === "pick"
              ? "Most dish points the week of Oct 26."
              : mode === "override"
                ? "Chosen by the house. On kitchen since Nov 2."
                : "The weekly pick is switched off.",
            { exact: true },
          )
          .waitFor();
        if (mode === "disabled")
          assert.equal(
            await page.locator(".pick > p").first().innerText(),
            "Standings still count.",
          );
        await capture(page, `duty-${mode}`, size, theme);
      }
      await control({ dutyMode: "carry" });
      await page.evaluate(() => window.dispatchEvent(new Event("focus")));
      await page
        .getByText(
          "Most dish points the week of Oct 19. Nobody else has been a clear pick since, so Ezzy stays on.",
          { exact: true },
        )
        .waitFor();

      await navigation(page, "Name dishes");
      await page
        .getByRole("heading", { name: "Whose dishes are these?" })
        .waitFor();
      await capture(page, "name-visits", size, theme);
      await page
        .locator(".name-chips")
        .getByRole("button", { name: "Theo", exact: true })
        .click();
      await page.locator('article[data-dish-id="visit-103"]').waitFor();
      await capture(page, "naming-undo", size, theme);
      await page.getByRole("button", { name: "Undo", exact: true }).click();
      await page.locator('article[data-dish-id="visit-101"]').waitFor();
      const widths = await page
        .locator(".naming .arrival-photo")
        .evaluate((el) => {
          let container = el.parentElement;
          while (getComputedStyle(container).display === "contents")
            container = container.parentElement;
          return {
            photo: el.getBoundingClientRect().width,
            content: container.getBoundingClientRect().width,
          };
        });
      assert.equal(
        Math.round(widths.photo),
        Math.round(widths.content),
        "arrival grid fills the evidence content width",
      );

      assert.deepEqual(
        await page.locator(".name-chips button").allTextContents(),
        [
          "Ari",
          "Ezzy",
          "Finn",
          "Jules",
          "Leo",
          "Milo",
          "Nico",
          "Remy",
          "Theo",
          "Wren",
        ],
      );
      assert.equal(await page.locator(".camera-chip").innerText(), "Wren");
      async function assertImageRatios() {
        const sizes = await page
          .locator(".naming .photo img")
          .evaluateAll((imgs) =>
            imgs.map((img) => {
              const box = img.closest(".photo").getBoundingClientRect();
              return {
                width: img.naturalWidth,
                height: img.naturalHeight,
                declaredWidth: Number(img.getAttribute("width")),
                declaredHeight: Number(img.getAttribute("height")),
                ratio: box.width / box.height,
              };
            }),
          );
        for (const image of sizes) {
          assert.equal(image.declaredWidth, image.width);
          assert.equal(image.declaredHeight, image.height);
          assert.ok(
            Math.abs(image.ratio - image.width / image.height) < 0.01,
            "box reserves actual JPEG ratio",
          );
        }
        return sizes;
      }
      const dimensions = await assertImageRatios();
      assert.deepEqual(
        [dimensions[0].width, dimensions[0].height],
        [1200, 1668],
        "production six-frame renderer",
      );
      await page
        .getByRole("button", { name: /Enlarge.*arrival photos/ })
        .click();
      await page.locator("dialog[open]").waitFor();
      assert.equal(
        await page
          .locator(".zoom-scroll img")
          .evaluate((img) => Math.round(img.getBoundingClientRect().width)),
        1200,
      );
      await page.locator(".zoom-scroll").evaluate((el) => {
        el.scrollTop = 600;
      });
      assert.ok(
        await page.locator(".zoom-scroll").evaluate((el) => el.scrollTop > 0),
      );
      await capture(page, "filmstrip-zoom", size, theme);
      await page.keyboard.press("Escape");

      await page
        .getByRole("button", { name: "Same as another", exact: true })
        .click();
      await capture(page, "duplicate-picker", size, theme);
      await page
        .getByRole("button", { name: "Same as another", exact: true })
        .click();
      await page.getByText("Reviewed this week (5)", { exact: true }).click();
      await page
        .getByText("Marked not a dish by Milo", { exact: true })
        .waitFor();
      await capture(page, "reviewed-visits", size, theme);
      await page
        .locator(".name-chips")
        .getByRole("button", { name: "Theo", exact: true })
        .click();
      await page
        .getByRole("heading", { name: "Dinner plate", exact: true })
        .waitFor();
      await page
        .getByText("Arrival not recorded — only name someone if you know", {
          exact: true,
        })
        .waitFor();
      await capture(page, "arrival-not-recorded", size, theme);
      const short = await assertImageRatios();
      assert.deepEqual(
        [short[0].width, short[0].height],
        [1200, 1177],
        "production four-frame renderer",
      );
      await navigation(page, "My dishes");
      await capture(page, "my-dishes", size, theme);
      await navigation(page, "Past weeks");
      await capture(page, "past-weeks", size, theme);
      assert.equal(
        await page.locator(".past-detail > .section-heading h2").innerText(),
        "Nov 2 – 8",
      );
      assert.deepEqual(
        await page.locator(".week-choice strong").allTextContents(),
        ["Nov 2 – 8", "Oct 26 – Nov 1", "Oct 19 – 25"],
      );
      await page.getByRole("button", { name: /Oct 26/ }).click();
      await page
        .getByText("Wren would need 2 more points for a clear pick.")
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
      isMobile: true,
      hasTouch: true,
    }),
    page = await context.newPage();
  observe(page);
  await check(
    "close time follows the API, including a different weekday and minute",
    async () => {
      await control({ closesAt: "2026-11-10T15:30:00-08:00" });
      await signedIn(page);
      await page
        .getByText("Nov 2 – 8 · closes Tue 3:30 PM", { exact: true })
        .waitFor();
      await control({ closesAt: null });
      await page.evaluate(() => window.dispatchEvent(new Event("focus")));
      await page
        .getByText("Nov 2 – 8 · closes Mon 12 PM", { exact: true })
        .waitFor();
      await page.getByRole("button", { name: "Sign out", exact: true }).click();
    },
  );
  await check(
    "sign-in uses a private name input and generic errors",
    async () => {
      await page.goto(`${origin}/?mock=1`);
      await page.getByLabel("Your name").fill("ezzy");
      assert.equal(await page.locator("select").count(), 0);
      await page.getByLabel("Your name").fill("unknown-person");
      await page.getByLabel("Password", { exact: true }).fill("house-demo");
      await page.getByRole("button", { name: "Sign in", exact: true }).click();
      await page
        .getByRole("alert")
        .filter({ hasText: "didn’t match" })
        .waitFor();
      await page.getByLabel("Your name").fill("EZZY");
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
    "Undo sends no name; a refused save restores the visit and explains failure",
    async () => {
      await navigation(page, "Name dishes");
      let writes = 0;
      await page.route("**/house/api/visits/*/name", (route) => {
        writes++;
        return route.fulfill({
          status: 409,
          contentType: "application/json",
          body: JSON.stringify({
            detail: "Already named — refresh before naming again.",
          }),
        });
      });
      await page
        .locator(".name-chips")
        .getByRole("button", { name: "Theo", exact: true })
        .click();
      await page.getByRole("button", { name: "Undo", exact: true }).click();
      assert.equal(writes, 0);
      await page.locator('article[data-dish-id="visit-101"]').waitFor();
      await page
        .locator(".name-chips")
        .getByRole("button", { name: "Theo", exact: true })
        .click();
      await page.locator(".answer-error").waitFor();
      assert.equal(writes, 1);
      await page.locator('article[data-dish-id="visit-101"]').waitFor();
      assert.match(
        await page.locator(".answer-error").innerText(),
        /answer not saved.*Already named/,
      );
      await page.getByRole("button", { name: "Dismiss", exact: true }).click();
      await page.unroute("**/house/api/visits/*/name");
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
        .getByRole("button", { name: "Camera thinks Wren — tap to confirm" })
        .click();
      await page.locator('article[data-dish-id="visit-103"]').waitFor();
      await page.locator(".answer-receipt").waitFor({ state: "hidden" });
      const after = await page.evaluate(() =>
        fetch("/house/api/week", {
          headers: { Authorization: "Bearer mock:ezzy" },
        }).then((r) => r.json()),
      );
      assert.equal(after.unnamed_count, 1);
      assert.equal(
        after.dishes.filter(
          (d) => [101, 102].includes(d.id) && d.resident?.name === "Wren",
        ).length,
        2,
      );
      await page.getByRole("button", { name: "Can’t tell" }).click();
      await page.getByRole("heading", { name: "That’s the round." }).waitFor();
      await page
        .locator(".answer-receipt")
        .first()
        .waitFor({ state: "hidden" });
      await page
        .getByRole("button", { name: "Look at skipped visits" })
        .click();
      await page.getByRole("button", { name: "Theo", exact: true }).click();
      await page
        .getByRole("heading", { name: "You’re all caught up." })
        .waitFor();
      await page
        .locator(".answer-receipt")
        .first()
        .waitFor({ state: "hidden" });
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
      await page
        .locator(".answer-receipt")
        .first()
        .waitFor({ state: "hidden" });
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
        data.visits.some((v) => v.id === "visit-101"),
        false,
      );
      assert.equal(
        data.verdicts.find((v) => v.visit_id === "visit-101").kind,
        "not_dish",
      );
      await control({ reset: true });
      await page.reload();
      await page
        .getByRole("button", { name: "Same as another", exact: true })
        .click();
      assert.equal(await page.locator(".duplicate-choice").count(), 5);
      await page.getByRole("button", { name: /Dinner plate/ }).click();
      await page.locator('article[data-dish-id="visit-103"]').waitFor();
      await page
        .getByRole("button", { name: "Can’t tell", exact: true })
        .click();
      await page.getByRole("heading", { name: "That’s the round." }).waitFor();
      await page
        .locator(".answer-receipt")
        .first()
        .waitFor({ state: "hidden" });
      data = await page.evaluate(() =>
        fetch("/house/api/week", {
          headers: { Authorization: "Bearer mock:ezzy" },
        }).then((r) => r.json()),
      );
      assert.equal(
        data.verdicts.find((v) => v.visit_id === "visit-101").kind,
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
  await check(
    "phone pinch and zoom controls resize and pan the two-column arrival grid",
    async () => {
      await navigation(page, "Name dishes");
      await page
        .getByRole("button", { name: /Enlarge.*arrival photos/ })
        .click();
      const viewport = page.locator(".zoom-scroll"),
        img = page.locator(".zoom-scroll img");
      await page.getByRole("button", { name: "Zoom in", exact: true }).click();
      assert.equal(await img.evaluate((el) => el.width), 1800);
      await page.keyboard.press("Escape");
      await page
        .getByRole("button", { name: /Enlarge.*arrival photos/ })
        .click();
      const rect = await viewport.boundingBox(),
        cdp = await context.newCDPSession(page),
        y = rect.y + 50;
      await cdp.send("Input.dispatchTouchEvent", {
        type: "touchStart",
        touchPoints: [
          { x: rect.x + 50, y },
          { x: rect.x + 140, y },
        ],
      });
      await cdp.send("Input.dispatchTouchEvent", {
        type: "touchMove",
        touchPoints: [
          { x: rect.x + 20, y },
          { x: rect.x + 200, y },
        ],
      });
      await cdp.send("Input.dispatchTouchEvent", {
        type: "touchEnd",
        touchPoints: [],
      });
      assert.ok(
        (await img.evaluate((el) => el.width)) > 1200,
        "two-finger pinch enlarges the image",
      );
      await page.keyboard.press("Escape");
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
        .getByText(
          "These points don’t count while you wait for a third housemate to settle it.",
        )
        .waitFor();
      await page.getByRole("button", { name: "Not them", exact: true }).click();
      await page.getByRole("heading", { name: "All settled here." }).waitFor();
      await page.getByRole("button", { name: "Sign out", exact: true }).click();
      await signedIn(page, "finn");
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
        .getByRole("heading", { name: "This house link is incomplete" })
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
      await page
        .locator(".answer-receipt")
        .first()
        .waitFor({ state: "hidden" });
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
  await check(
    "evidence tiers prioritize clear visits and weak visits require disclosure",
    async () => {
      await control({ reset: true, evidenceQueue: true });
      await page.goto(`${origin}/?mock=1`);
      await page
        .getByRole("heading", { name: "2 clear ones to name" })
        .waitFor();
      await page
        .getByRole("link", { name: "+1 with weak evidence", exact: true })
        .click();
      await page.getByRole("button", { name: "Hide weak evidence" }).waitFor();
      assert.equal(
        await page.locator("article.naming").getAttribute("data-dish-id"),
        "visit-101",
      );
      await page
        .getByRole("button", { name: "Not a dish", exact: true })
        .click();
      await page.locator('article[data-dish-id="visit-103"]').waitFor();
      await page
        .getByRole("button", { name: "It’s clean", exact: true })
        .click();
      await page.locator('article[data-dish-id="visit-113"]').waitFor();
      await page
        .getByText("Arrival not recorded · No one in the photos", {
          exact: true,
        })
        .waitFor();
      await page.getByRole("button", { name: "Hide weak evidence" }).click();
      assert.equal(await page.locator("article.naming").count(), 0);
      await page
        .getByRole("button", { name: "+1 with weak evidence", exact: true })
        .waitFor();
    },
  );
  await context.close();
  assert.deepEqual(issues, [], "Browser exceptions/CSP errors");
  await writeFile(
    `${scratch}/verification.json`,
    JSON.stringify(
      { shots, layouts, checks, browser: await browser.version(), issues },
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
