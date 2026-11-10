// Run with external tooling; see README. All output and browser profiles live in scratch.
import { createRequire } from "node:module";
import { mkdir, writeFile, readFile } from "node:fs/promises";
import assert from "node:assert/strict";
import { inspectLayout } from "./layout.mjs";
const scratch =
  process.env.DISHWATCH_SCRATCH ||
  "/Volumes/Neural/dishwatch-scratch/houseleague/laneC";
const output = process.env.DISHWATCH_OUTPUT || scratch;
const require = createRequire(`${scratch}/tooling/package.json`);
const { chromium } = require("playwright-core");
const { default: AxeBuilder } = await import(
  `${scratch}/tooling/node_modules/@axe-core/playwright/dist/index.mjs`
);
const origin = process.env.MOCK_ORIGIN || "http://127.0.0.1:4317";
await mkdir(`${output}/screenshots`, { recursive: true });
await mkdir(`${output}/tmp`, { recursive: true });
process.env.TMPDIR = `${output}/tmp`;
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
      !m.text().includes("409") &&
      // The hiccup and unlinked-account checks provoke these on purpose.
      !m.text().includes("429") &&
      !m.text().includes("403") &&
      !m.location().url.includes("/dev/photos/missing.jpg")
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
  // Past weeks is pushed from Me; the other destinations are tabs.
  if (label === "Past weeks") {
    await page.locator(".tab", { hasText: "Me" }).click();
    await page.getByRole("link", { name: "Past weeks", exact: true }).click();
    await page.locator(".past-page").waitFor();
    return;
  }
  const tab = { "Name dishes": "Dishes", "My dishes": "Me" }[label] || label;
  await page.locator(".tab", { hasText: tab }).click();
  await page.locator("#main h1").waitFor();
}
async function signOutVia(page) {
  await navigation(page, "My dishes");
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await page.getByLabel("Your name").waitFor();
}
/** Not a dish, It's clean and Same as another live in one sheet. */
async function moreAnswer(page, label) {
  await page.getByRole("button", { name: "Something’s off", exact: true }).click();
  await page.getByRole("button", { name: label, exact: true }).click();
}
async function capture(page, name, size, theme) {
  await page.evaluate(() => document.fonts.ready);
  await captureImages(page, name, size, theme);
}
async function captureImages(page, name, size, theme) {
  // Lazy images outside the scroll viewport (inside sheets, behind the photo
  // viewer) never start loading by themselves; screenshots need them complete.
  await page.evaluate(() =>
    document.querySelectorAll('img[loading="lazy"]').forEach((img) => {
      if (!img.closest("details:not([open])")) img.loading = "eager";
    }),
  );
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
    .locator(
      "button:not(:disabled), nav a, a.row, .learning-panel a, input:not([type=range]), select, textarea, summary",
    )
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
  const filename = `${output}/screenshots/${name}-${size}-${theme}.png`;
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
        "Nov 2 – 8 · ends Mon 12 PM",
      );
      assert.equal(
        await page.locator(".pick > p").first().innerText(),
        "7 points, 4 ahead of second place.",
      );
      const tied = await page.evaluate(async () => {
        const { getWeek } = await import("/js/api.js");
        const { pickCopy } = await import("/js/pages/league-copy.js");
        const data = await getWeek();
        return pickCopy({
          ...data,
          rules: { ...data.rules, min_lead: 1 },
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
      });
      assert.equal(tied.title, "It’s a tie at the top");
      assert.match(tied.detail, /Tied at 4 points\. Someone needs a 1-point lead\./);
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
      for (const mode of ["pick", "override", "disabled"]) {
        await control({ dutyMode: mode });
        await page.evaluate(() => window.dispatchEvent(new Event("focus")));
        await page
          .locator(".duty, .personal-duty")
          .getByText(
            mode === "pick"
              ? "Most points the week of Oct 26."
              : mode === "override"
                ? "Chosen by the house on Nov 2."
                : "No one gets picked while it’s off.",
            { exact: true },
          )
          .waitFor();
        if (mode === "disabled")
          assert.equal(
            await page.locator(".pick > p").first().innerText(),
            "Points still count.",
          );
        await capture(page, `duty-${mode}`, size, theme);
      }
      await control({ dutyMode: "carry" });
      await page.evaluate(() => window.dispatchEvent(new Event("focus")));
      await page
        .getByText(
          "Most points the week of Oct 19. No one’s had a clear lead since, so Ezzy stays on.",
          { exact: true },
        )
        .waitFor();

      assert.equal(
        await page
          .getByRole("heading", {
            name: "You’re on kitchen duty",
            exact: true,
          })
          .count(),
        1,
      );
      assert.equal(
        await page
          .getByRole("heading", {
            name: "Ezzy’s on kitchen duty",
            exact: true,
          })
          .count(),
        0,
      );
      await capture(page, "week-on-duty", size, theme);
      const otherContext = await browser.newContext({
        viewport: { width, height },
        colorScheme: theme,
        reducedMotion: "reduce",
      });
      const otherPage = await otherContext.newPage();
      observe(otherPage);
      await signedIn(otherPage, "wren");
      await otherPage
        .getByRole("heading", {
          name: "Ezzy’s on kitchen duty",
          exact: true,
        })
        .waitFor();
      await capture(otherPage, "week-not-on-duty", size, theme);
      await otherContext.close();
      for (const task of ["present", "kind", "same", "identity", "missed"]) {
        await control({ reset: true, learningTask: task });
        await page.goto(`${origin}/help?mock=1`);
        await page.locator(".learning-card .learning-question h2").waitFor();
        await capture(page, `help-${task}`, size, theme);
        if (task === "missed") {
          const photo = page.locator(".learning-draw");
          const rect = await photo.boundingBox();
          await page.mouse.move(
            rect.x + rect.width * 0.2,
            rect.y + rect.height * 0.3,
          );
          await page.mouse.down();
          await page.mouse.move(
            rect.x + rect.width * 0.6,
            rect.y + rect.height * 0.7,
          );
          await page.mouse.up();
          await page
            .getByText("Adjust the box by number", { exact: true })
            .click();
          assert.equal(
            await page.getByLabel("Top percent", { exact: true }).inputValue(),
            "30",
            "coordinate fields reflect the drawn box",
          );
          await page.getByLabel("Left percent", { exact: true }).fill("20");
          await page.getByLabel("Width percent", { exact: true }).fill("30");
          await capture(page, "help-missed-box", size, theme);
          const saved = page.waitForRequest((r) =>
            r.url().endsWith("/learning/3/answer"),
          );
          await page
            .getByRole("button", { name: "Save", exact: true })
            .click();
          const payload = (await saved).postDataJSON();
          assert.equal(payload.answer, "box");
          assert.equal(payload.value.kind, "plate");
          assert.equal(payload.value.box[0], 0.2);
          assert.equal(payload.value.box[2], 0.3);
        } else {
          await page.locator(".learning-choice").first().click();
        }
        await page.getByRole("button", { name: "Undo", exact: true }).waitFor();
        if (task === "present") await capture(page, "help-undo", size, theme);
        await page.getByRole("button", { name: "Undo", exact: true }).click();
        await page
          .getByRole("button", { name: "Undo", exact: true })
          .waitFor({ state: "hidden" });
        assert.equal(
          await page.locator(".learning-question h2").innerText(),
          {
            present: "Is this a dish?",
            kind: "What is it?",
            same: "Is this the same dish?",
            identity: "Is this you?",
            missed: "Did the camera miss a dish?",
          }[task],
        );
      }
      for (const [state, heading] of [
        ["empty", "That’s all for now."],
        ["load-error", "Couldn’t load your questions"],
        ["missing", "Is this a dish?"],
      ]) {
        await control({ reset: true, learningState: state });
        await page.goto(`${origin}/help?mock=1`);
        await page
          .getByRole("heading", { name: heading, exact: true })
          .waitFor();
        if (state === "missing") {
          await page
            .getByText(
              "This photo is gone. Skip to the next question.",
              { exact: true },
            )
            .waitFor();
          assert.equal(
            await page.locator(".learning-choice:disabled").count(),
            2,
          );
          assert.equal(
            await page
              .getByRole("button", { name: "Not sure, skip", exact: true })
              .isEnabled(),
            true,
          );
        }
        await capture(page, `help-${state}`, size, theme);
        if (state === "load-error") {
          await control({ learningState: "cards" });
          await page
            .getByRole("button", { name: "Try again", exact: true })
            .click();
          await page
            .getByRole("heading", { name: "Is this a dish?", exact: true })
            .waitFor();
        }
      }
      await control({ reset: true, learningState: "error" });
      await page.goto(`${origin}/help?mock=1`);
      await page.getByRole("button", { name: "Yes", exact: true }).click();
      await page.getByRole("alert").filter({ hasText: "reach the kitchen" }).waitFor();
      await capture(page, "help-save-error", size, theme);
      assert.equal(
        await page.locator(".learning-question h2").innerText(),
        "Is this a dish?",
      );
      await control({ reset: true });
      await navigation(page, "Name dishes");
      await page
        .getByRole("button", { name: "The yellow box is in the wrong spot", exact: true })
        .click();
      await page
        .getByRole("heading", {
          name: "Did the camera miss a dish?",
          exact: true,
        })
        .waitFor();
      assert.equal(new URL(page.url()).pathname, "/help");
      const operator = await context.newPage();
      observe(operator);
      for (const state of ["cards", "conflict", "empty", "load-error"]) {
        await control({ reset: true, learningState: state });
        await operator.goto(`${origin}/dev/learning-operator.html`);
        await operator
          .getByRole("heading", { name: "Learning", exact: true })
          .waitFor();
        if (state === "load-error")
          await operator
            .getByRole("button", { name: "Retry", exact: true })
            .waitFor();
        else
          await operator
            .locator(".learning-panel .row-actions button")
            .first()
            .waitFor();
        await capture(operator, `operator-learning-${state}`, size, theme);
        if (state === "conflict") {
          await operator
            .getByRole("button", { name: "Use yes · resident 1", exact: true })
            .click();
          await operator
            .getByText("No learning runs yet.", { exact: true })
            .waitFor();
        }
        if (state === "load-error") {
          await control({ learningState: "empty" });
          await operator
            .getByRole("button", { name: "Retry", exact: true })
            .click();
          await operator
            .getByText("No learning runs yet.", { exact: true })
            .waitFor();
        }
      }
      await operator.close();
      await control({ reset: true });
      await navigation(page, "This week");
      await page
        .getByRole("heading", { name: "This week", exact: true })
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
      assert.ok(
        Math.round(widths.photo) >= Math.round(widths.content),
        "arrival grid fills at least the evidence content width",
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
        [1080, 1688],
        "production chronological evidence renderer",
      );
      await page
        .getByRole("button", { name: /Enlarge.*arrival photos/ })
        .click();
      await page.locator("dialog[open]").waitFor();
      assert.equal(
        await page
          .locator(".zoom-scroll img")
          .evaluate((img) => Math.round(img.getBoundingClientRect().width)),
        await page.locator(".zoom-scroll").evaluate((el) => el.clientWidth),
        "photos open fitted to the screen",
      );
      for (const step of [1, 2])
        await page.getByRole("button", { name: "Zoom in", exact: true }).click();
      await page.locator(".zoom-scroll").evaluate((el) => {
        el.scrollTop = 600;
      });
      assert.ok(
        await page.locator(".zoom-scroll").evaluate((el) => el.scrollTop > 0),
      );
      await capture(page, "filmstrip-zoom", size, theme);
      await page.keyboard.press("Escape");

      await moreAnswer(page, "Same as another");
      await capture(page, "duplicate-picker", size, theme);
      await page.keyboard.press("Escape");
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
        .getByText("The camera missed them being put down, so people nearby might not be the owner.", {
          exact: true,
        })
        .waitFor();
      await capture(page, "arrival-not-recorded", size, theme);
      const short = await assertImageRatios();
      assert.deepEqual(
        [short[0].width, short[0].height],
        [1080, 1096],
        "production already-present evidence renderer",
      );
      await navigation(page, "My dishes");
      await capture(page, "my-dishes", size, theme);
      await navigation(page, "Past weeks");
      await capture(page, "past-weeks", size, theme);
      assert.deepEqual(
        await page.locator(".past-page .row-label").allTextContents(),
        ["Nov 2 – 8", "Oct 26 – Nov 1", "Oct 19 – 25"],
      );
      await page.getByRole("link", { name: /Oct 26/ }).click();
      await page.getByText("Top score is 2. It takes 4 points and a 2-point lead.").waitFor();
      await page.locator(".past-detail h1").filter({ hasText: "Oct 26 – Nov 1" }).waitFor();
      await capture(page, "no-pick", size, theme);
      await navigation(page, "This week");
      await control({ offline: true });
      await page.evaluate(() => window.dispatchEvent(new Event("focus")));
      await page
        .getByText("Can’t reach the kitchen right now", { exact: true })
        .waitFor();
      await capture(page, "offline", size, theme);
      await control({ offline: false });
      await page.getByRole("button", { name: "Try again" }).click();
      await page
        .getByText("Can’t reach the kitchen right now", { exact: true })
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
        .getByText("Nov 2 – 8 · ends Tue 3:30 PM", { exact: true })
        .waitFor();
      await control({ closesAt: null });
      await page.evaluate(() => window.dispatchEvent(new Event("focus")));
      await page
        .getByText("Nov 2 – 8 · ends Mon 12 PM", { exact: true })
        .waitFor();
      await signOutVia(page);
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
        .locator(".camera-chip")
        .getByText("Wren", { exact: true })
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
        .getByRole("button", { name: "Look at the skipped ones again" })
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
      await moreAnswer(page, "Not a dish");
      await page.locator('article[data-dish-id="visit-103"]').waitFor();
      await moreAnswer(page, "It’s clean");
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
      await moreAnswer(page, "Same as another");
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
      const fitted = await img.evaluate((el) => el.width);
      await page.getByRole("button", { name: "Zoom in", exact: true }).click();
      assert.equal(await img.evaluate((el) => el.width), Math.round(fitted * 1.5));
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
        (await img.evaluate((el) => el.width)) > fitted,
        "two-finger pinch enlarges the image",
      );
      await page.keyboard.press("Escape");
      await navigation(page, "My dishes");
    },
  );
  await check("photo zoom and Escape return focus", async () => {
    await navigation(page, "My dishes");
    await page.getByText("See the photos", { exact: true }).first().click();
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
      // A deferred background render must not discard a rejected dispute's draft or error.
      await page.route("**/house/api/week*", async (route) => {
        const response = await route.fetch();
        const data = await response.json();
        data.unnamed_count += 1;
        await route.fulfill({ response, json: data });
      });
      const refresh = page.waitForResponse((r) => r.url().includes("/house/api/week"));
      await page.evaluate(() => window.dispatchEvent(new Event("focus")));
      await refresh;
      let attempts = 0;
      await page.route("**/house/api/visits/*/dispute", async (route) => {
        attempts++;
        await new Promise((resolve) => setTimeout(resolve, 100));
        if (attempts <= 2) return route.fulfill({
          status: attempts === 1 ? 409 : 503,
          contentType: "application/json",
          body: JSON.stringify({ detail: "The record changed. Try again." }),
        });
        return route.continue();
      });
      for (let attempt = 0; attempt < 2; attempt++) {
        await page.getByRole("button", { name: "Send dispute" }).click();
        await page.locator("dialog[open] .form-error").filter({ hasText: "Your note is still here" }).waitFor();
        assert.equal(await page.getByLabel("What happened?").inputValue(),
          "I carried it to the sink, but didn’t use it.");
      }
      await capture(page, "dispute-save-error", "390x844", "light");
      await page.unroute("**/house/api/week*");
      await page.getByRole("button", { name: "Send dispute" }).click();
      await page
        .getByText(
          "These points don’t count until another housemate settles it.",
        )
        .waitFor();
      await page.getByRole("button", { name: "Not them", exact: true }).click();
      await page.getByRole("heading", { name: "All settled here." }).waitFor();
      await signOutVia(page);
      await signedIn(page, "finn");
      await navigation(page, "My dishes");
      await page.getByRole("button", { name: "It was them" }).click();
      await page.getByRole("heading", { name: "All settled here." }).waitFor();
    },
  );
  await check(
    "expired and incomplete invite; mismatch; join and reset auto sign-in",
    async () => {
      await signOutVia(page);
      await page.goto(`${origin}/join?mock=1`);
      await page
        .getByRole("heading", { name: "This link is incomplete" })
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
        .locator("article.naming")
        .getByText("The photo for this one is gone.")
        .waitFor();
      await control({ malformed: true });
      await page.reload();
      await page
        .getByRole("heading", { name: "Can’t reach the kitchen", exact: true })
        .waitFor();
      await control({ reset: true, offline: true });
      await page.reload();
      await page
        .getByRole("heading", { name: "Can’t reach the kitchen", exact: true })
        .waitFor();
      await control({ offline: false });
      await page.getByRole("button", { name: "Try again" }).click();
      await page.getByRole("heading", { name: "Whose dishes are these?" }).waitFor();
    },
  );
  await check(
    "refresh every minute while visible, pause while hidden, refresh on focus",
    async () => {
      await page.clock.install();
      await page.reload();
      await page.getByRole("heading", { name: "Whose dishes are these?" }).waitFor();
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
        .getByRole("heading", { name: "2 dishes need a name" })
        .waitFor();
      await navigation(page, "Name dishes");
      await page
        .getByRole("button", { name: "Show 1 that is hard to see", exact: true })
        .click();
      await page.getByRole("button", { name: "Hide the hard-to-see ones" }).waitFor();
      assert.equal(
        await page.locator("article.naming").getAttribute("data-dish-id"),
        "visit-101",
      );
      await moreAnswer(page, "Not a dish");
      await page.locator('article[data-dish-id="visit-103"]').waitFor();
      await moreAnswer(page, "It’s clean");
      await page.locator('article[data-dish-id="visit-113"]').waitFor();
      await page
        .getByText("The camera missed it arriving, no one’s in the photos.", {
          exact: true,
        })
        .waitFor();
      await page.getByRole("button", { name: "Hide the hard-to-see ones" }).click();
      assert.equal(await page.locator("article.naming").count(), 0);
      await page
        .getByRole("button", { name: "Show 1 that is hard to see", exact: true })
        .waitFor();
    },
  );
  await check(
    "a brief server hiccup never shows the offline screen",
    async () => {
      await control({ reset: true });
      await page.goto(`${origin}/?mock=1`);
      await page.getByRole("heading", { name: "This week", exact: true }).waitFor();
      let failures = 0;
      await page.route("**/house/api/week*", (route) => {
        if (failures++ < 2)
          return route.fulfill({ status: failures === 1 ? 503 : 429, contentType: "application/json", body: "{}" });
        return route.continue();
      });
      await page.reload();
      await page.getByRole("heading", { name: "This week", exact: true }).waitFor({ timeout: 8000 });
      assert.equal(failures, 3);
      assert.equal(await page.getByText("Can’t reach the kitchen", { exact: false }).count(), 0);
      await page.unroute("**/house/api/week*");
    },
  );
  await check(
    "an unlinked account gets its reason, not an offline message",
    async () => {
      await page.route("**/house/api/me", (route) =>
        route.fulfill({
          status: 403,
          contentType: "application/json",
          body: JSON.stringify({ detail: "Your account is not linked to an active housemate. Ask the operator for a sign-in link." }),
        }),
      );
      await page.reload();
      await page.getByText(/not linked to an active housemate/).waitFor();
      assert.equal(await page.getByText("Can’t reach the kitchen", { exact: false }).count(), 0);
      await page.unroute("**/house/api/me");
      await page.reload();
      await page.getByRole("heading", { name: "This week", exact: true }).waitFor();
    },
  );
  await check(
    "lowercase account names read as names; tabs keep their place and background reads don't redraw",
    async () => {
      assert.deepEqual(
        await page.evaluate(async () => {
          const { displayName } = await import("/js/contracts.js");
          return ["andrew", "Mary", "mary-jane", "de la cruz"].map(displayName);
        }),
        ["Andrew", "Mary", "Mary-Jane", "De La Cruz"],
      );
      await page.evaluate(() => {
        window.__tab = document.querySelector(".tabbar");
        window.__stat = document.querySelector("#main .status-card");
      });
      await page.evaluate(() => window.dispatchEvent(new Event("focus")));
      await page.waitForTimeout(300);
      assert.ok(
        await page.evaluate(() => window.__stat.isConnected),
        "an unchanged background read leaves the page in place",
      );
      await page.evaluate(() => window.scrollTo(0, 200));
      const scrolled = await page.evaluate(() => window.scrollY);
      await navigation(page, "Footage");
      await navigation(page, "This week");
      assert.equal(await page.evaluate(() => window.scrollY), scrolled, "a tab keeps its scroll position");
      assert.ok(await page.evaluate(() => window.__tab.isConnected), "the tab bar persists");
    },
  );
  await check("duplicate originals beyond the nearest twenty remain reachable", async () => {
    await control({ reset: true });
    const context = await browser.newContext({ viewport: { width: 390, height: 844 }, reducedMotion: "reduce" });
    const page = await context.newPage();
    observe(page);
    try {
      await signedIn(page);
      await page.route("**/house/api/week*", async (route) => {
        const response = await route.fetch();
        const data = await response.json();
        const source = data.visits.find((v) => !v.resident && v.can_name);
        data.visits = [source, ...Array.from({ length: 25 }, (_, i) => ({
          ...source, id: `target-${i}`, title: `Target ${i}`,
          at: new Date(Date.parse(source.at) - 1000 * (i + 1)).toISOString(),
          resident: null, photo_url: null, filmstrip_url: null,
        }))];
        await route.fulfill({ response, json: data });
      });
      await page.goto(`${origin}/name?mock=1`);
      await moreAnswer(page, "Same as another");
      assert.equal(await page.locator("dialog[open] .duplicate-choice").count(), 20);
      await page.getByRole("button", { name: /Show more dishes/ }).click();
      assert.equal(await page.locator("dialog[open] .duplicate-choice").count(), 25);
      assert.equal(await page.locator("dialog[open] .duplicate-choice").filter({ hasText: "Target 24" }).count(), 1);
      assert.equal(await page.getByRole("button", { name: /Show more dishes/ }).count(), 0);
      await capture(page, "duplicate-all-originals", "390x844", "light");
    } finally { await context.close(); }
  });
  await check("every client route is served by the deployed rewrites", async () => {
    const config = JSON.parse(await readFile(new URL("../vercel.json", import.meta.url), "utf8"));
    const sources = config.rewrites.map((r) => r.source);
    for (const route of ["/join", "/name", "/mine", "/past", "/help", "/footage"])
      assert.ok(sources.includes(route), `${route} survives a refresh`);
  });
  await context.close();
  assert.deepEqual(issues, [], "Browser exceptions/CSP errors");
  await writeFile(
    `${output}/verification.json`,
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
