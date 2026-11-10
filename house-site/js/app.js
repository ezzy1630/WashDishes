import { pendingAnswers } from "./answers.js";
import { HouseError, act, getMe, getWeek } from "./api.js";
import { accessToken, mock, onSignedOut, signOut } from "./auth.js";
import { createAuthScreens } from "./pages/auth.js";
import { minePage } from "./pages/mine.js";
import { namePage } from "./pages/name.js";
import { pastPage } from "./pages/past.js";
import { helpPage } from "./pages/help.js";
import { weekPage } from "./pages/week.js";
import { footagePage, stopFootage } from "./pages/footage.js";
import { announce, button, fill, h, heading, icon } from "./ui.js";

/** @typedef {import('./types.js').Visit} Visit */
/** @typedef {'week'|'name'|'mine'|'past'|'help'|'footage'} Page */
/** @typedef {'push'|'pop'|'tab'|'advance'|'retreat'} Motion */
const root = /** @type {HTMLDivElement} */ (document.getElementById("app"));
history.scrollRestoration = "manual";
/** @type {import('./types.js').State} */
const state = {
  me: null,
  data: null,
  offline: false,
  lastLoaded: 0,
  selected: "",
  skipped: new Set(),
  showWeak: false,
  pending: new Set(),
  working: false,
  loading: false,
};
let answerError = "",
  // A readable refusal (for example an account that isn't linked yet), which is
  // a different thing from the kitchen computer being unreachable.
  problem = "",
  badge = 0,
  fingerprint = "",
  renderedAt = 0,
  renderOwed = false;
/** @type {Motion|null} */
let motion = null;
/** @type {IntersectionObserver|null} */
let titleWatch = null;
/** @type {Map<string, number>} */
const scrolls = new Map();

const answers = pendingAnswers({
  send: async (entry) => {
    const updated = await act(entry.visit.id, entry.action, entry.body);
    if (updated.verdict?.kind === "cant_tell")
      state.skipped.add(entry.visit.id);
  },
  changed: () => {
    state.pending = new Set(answers.entries.keys());
    renderReceipts();
    render();
  },
  saved: async (entry) => {
    announce(`${entry.visit.title}: saved.`);
    await refresh();
  },
  failed: (entry, error) => {
    answerError = `${entry.visit.title}: answer not saved. ${error instanceof Error ? error.message : "Try again."}`;
    announce(answerError);
  },
});
let epoch = 0,
  refreshing = false,
  refreshAgain = false;
const mockQuery = mock ? "?mock=1" : "";
/** @param {string} path */
function href(path) {
  return path + (mock ? (path.includes("?") ? "&mock=1" : mockQuery) : "");
}
/** @returns {Page} */
function page() {
  const path = location.pathname;
  return path.startsWith("/footage")
    ? "footage"
    : path.startsWith("/help")
      ? "help"
      : path.startsWith("/name")
        ? "name"
        : path.startsWith("/mine")
          ? "mine"
          : path.startsWith("/past")
            ? "past"
            : "week";
}
/** @param {Page} current @returns {{parent:string,label:string}|null} Pushed screens go back to their parent. */
function parentOf(current) {
  if (current === "help") return { parent: "/mine", label: "Me" };
  if (current === "past")
    return state.selected
      ? { parent: "/past", label: "Past weeks" }
      : { parent: "/mine", label: "Me" };
  return null;
}
const depth = () => Number(history.state?.depth) || 0;
let lastDepth = depth();
/** @param {string} path @param {'push'|'pop'|'tab'} [direction] */
function navigate(path, direction = "push") {
  scrolls.set(location.pathname + location.search, window.scrollY);
  stopFootage();
  root.querySelector(".footage-page")?.remove();
  void answers.flush();
  lastDepth = depth() + 1;
  history.pushState({ depth: lastDepth }, "", href(path));
  state.skipped.clear();
  state.showWeak = false;
  syncSelection();
  motion = direction;
  render(true);
  const saved = direction === "tab" ? scrolls.get(location.pathname + location.search) : 0;
  window.scrollTo(0, saved || 0);
  focusHeading();
  if (state.me && !state.data) void refresh();
}
function syncSelection() {
  const selected =
    page() === "past"
      ? new URLSearchParams(location.search).get("start") || ""
      : "";
  if (selected !== state.selected) {
    state.selected = selected;
    state.data = null;
    fingerprint = "";
  }
}
function focusHeading() {
  requestAnimationFrame(() =>
    document
      .querySelector("#main")
      ?.querySelector("h1")
      ?.focus({ preventScroll: true }),
  );
}
/** @type {import("./types.js").ScreenContext["link"]} */
function link(path, attrs, ...children) {
  return h(
    "a",
    {
      ...attrs,
      href: href(path),
      onclick: (/** @type {MouseEvent} */ e) => {
        e.preventDefault();
        navigate(path);
      },
    },
    ...children,
  );
}
async function leave() {
  try {
    await answers.flush();
    await signOut();
    clearSession();
  } catch (error) {
    announce(error instanceof Error ? error.message : "Couldn’t sign out.");
  }
}
function clearSession() {
  stopFootage();
  answers.cancel();
  state.pending.clear();
  answerError = "";
  problem = "";
  badge = 0;
  fingerprint = "";
  epoch++;
  state.me = null;
  state.data = null;
  state.lastLoaded = 0;
  state.offline = false;
  state.loading = false;
  state.skipped.clear();
  state.showWeak = false;
  state.selected = "";
  scrolls.clear();
  history.replaceState({}, "", href("/"));
  renderAuth();
}
onSignedOut(clearSession);

/** Tabs keep their place in the DOM across pages so nothing flashes on navigation. */
const tabs = /** @type {const} */ ([
  ["week", "This week", "/", "house"],
  ["name", "Dishes", "/name", "dish"],
  ["footage", "Footage", "/footage", "film"],
  ["mine", "Me", "/mine", "person"],
]);
/** @type {{title:HTMLElement,back:HTMLElement,bar:HTMLElement,main:HTMLElement,tabbar:HTMLElement,receipts:HTMLElement}|null} */
let frame = null;
function mountFrame() {
  if (frame && root.contains(frame.main)) return frame;
  document.documentElement.classList.toggle("demo", mock);
  const title = h("p", { class: "navbar-title", "aria-hidden": "true" }),
    back = h("div", { class: "navbar-leading" }),
    bar = h("header", { class: "navbar" }, back, title, h("div")),
    main = h("main", { id: "main", class: "main" }),
    receipts = h("div", { class: "receipts" }),
    tabbar = h("nav", { class: "tabbar", "aria-label": "Main navigation" });
  fill(
    root,
    bar,
    mock
      ? h("div", { class: "demo-banner" }, "Demo · Made-up names and results")
      : null,
    main,
    h(
      "div",
      { class: "floating" },
      receipts,
      h("p", { id: "notice", class: "notice", hidden: true }),
    ),
    tabbar,
  );
  frame = { title, back, bar, main, tabbar, receipts };
  return frame;
}
function paintTabs() {
  if (!frame) return;
  const current = page();
  const active =
    current === "past" || current === "help" ? "mine" : current;
  fill(
    frame.tabbar,
    h(
      "a",
      {
        class: "tabbar-brand",
        href: href("/"),
        onclick: (/** @type {MouseEvent} */ e) => {
          e.preventDefault();
          navigate("/", "tab");
        },
      },
      h("span", { class: "brand-mark" }, icon("house")),
      "Highest House",
    ),
    tabs.map(([key, label, path, glyph]) =>
      h(
        "a",
        {
          class: "tab",
          href: href(path),
          "aria-current": active === key ? "page" : null,
          "aria-label": key === "name" && badge ? `${label}, ${badge} waiting` : null,
          onclick: (/** @type {MouseEvent} */ e) => {
            e.preventDefault();
            if (current === key) window.scrollTo({ top: 0, behavior: "smooth" });
            else if (active === key && current !== key) navigate(path, "pop");
            else navigate(path, "tab");
          },
        },
        h(
          "span",
          { class: "tab-icon" },
          icon(glyph),
          key === "name" && badge
            ? h("span", { class: "tab-badge", "aria-hidden": "true" }, badge > 99 ? "99+" : badge)
            : null,
        ),
        h("span", { class: "tab-label" }, label),
      ),
    ),
  );
}
function paintBar() {
  if (!frame) return;
  const current = page();
  const parent = parentOf(current);
  frame.bar.classList.toggle("navbar-pushed", Boolean(parent));
  document.documentElement.classList.toggle("pushed", Boolean(parent));
  frame.back.replaceChildren(
    ...(parent
      ? [
          button(
            "",
            () => {
              if (depth() > 0) history.back();
              else navigate(parent.parent, "pop");
            },
            "navbar-back",
          ),
        ]
      : []),
  );
  const back = frame.back.querySelector("button");
  if (back && parent) back.append(icon("back"), parent.label);
}
/** Compact title fades in once the large title scrolls under the bar. */
function watchTitle() {
  titleWatch?.disconnect();
  if (!frame) return;
  const heading = frame.main.querySelector("h1");
  const bar = frame.bar;
  frame.title.textContent = heading?.textContent || "";
  if (!heading) {
    bar.classList.add("navbar-scrolled");
    return;
  }
  bar.classList.remove("navbar-scrolled");
  titleWatch = new IntersectionObserver(
    ([entry]) => bar.classList.toggle("navbar-scrolled", !entry.isIntersecting),
    { rootMargin: "-56px 0px 0px 0px" },
  );
  titleWatch.observe(heading);
}
function receiptNodes() {
  /** @type {HTMLElement[]} */
  const nodes = [];
  if (answerError)
    nodes.push(
      h(
        "div",
        { class: "answer-error", role: "alert" },
        h("p", {}, answerError),
        button(
          "Dismiss",
          () => {
            answerError = "";
            renderReceipts();
          },
          "receipt-action",
        ),
      ),
    );
  const entries = [...answers.entries.values()];
  // Quick answers stack in the same pill: the newest stays undoable, others show as a count.
  entries.slice(-2).forEach((entry) =>
    nodes.push(
      h(
        "div",
        { class: "answer-receipt", role: "status" },
        h(
          "p",
          {},
          h("strong", {}, entry.saving ? "Saving…" : entry.message),
          h("span", {}, entry.visit.title),
        ),
        button(
          entry.saving ? "Saving…" : "Undo",
          () => {
            motion = "retreat";
            answers.undo(entry.visit.id);
            window.scrollTo(0, 0);
            focusHeading();
          },
          "receipt-action",
        ),
      ),
    ),
  );
  nodes.forEach((node) => {
    const action = node.querySelector("button");
    if (action && action.textContent === "Saving…") action.disabled = true;
  });
  return nodes;
}
function renderReceipts() {
  frame?.receipts.replaceChildren(...receiptNodes());
}
function offlineBanner() {
  return h(
    "aside",
    { class: "offline", role: "status" },
    h(
      "div",
      {},
      h("strong", {}, "Can’t reach the kitchen right now"),
      h(
        "p",
        {},
        state.lastLoaded
          ? `You’re seeing what loaded at ${new Date(state.lastLoaded).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })}.`
          : "It usually comes back on its own in a minute or two.",
      ),
    ),
    button("Try again", () => void refresh(), "secondary"),
  );
}
/** @param {()=>void} update */
function transition(update) {
  const kind = motion;
  motion = null;
  const animate =
    kind &&
    kind !== "tab" &&
    "startViewTransition" in document &&
    !document.hidden &&
    !matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (!animate) {
    update();
    return;
  }
  const html = document.documentElement;
  html.dataset.motion = kind;
  document
    .startViewTransition(update)
    .finished.finally(() => delete html.dataset.motion);
}
/** @param {Node} content */
function shell(content) {
  const current = page();
  mountFrame();
  if (!frame) return;
  const main = frame.main;
  main.className = `main page-${current}`;
  main.replaceChildren(
    ...(state.offline && state.data ? [offlineBanner()] : []),
    content,
  );
  paintBar();
  paintTabs();
  renderReceipts();
  watchTitle();
  if (state.offline)
    root
      .querySelectorAll(
        '.chip,.verdict-button,.duplicate-choice,.settle-actions button,.record-actions button[type="submit"]',
      )
      .forEach((button) => {
        if (button instanceof HTMLButtonElement) button.disabled = true;
      });
  const label = main.querySelector("h1")?.textContent;
  document.title = label ? `${label} · Highest House` : "Highest House";
}
/** Placeholder shapes keep the layout still while the first read arrives. */
function skeleton() {
  return h(
    "div",
    { class: "skeleton", role: "status", "aria-label": "Loading" },
    h("div", { class: "skeleton-title" }),
    h("div", { class: "skeleton-card" }),
    h("div", { class: "skeleton-card short" }),
  );
}
async function enter() {
  state.loading = true;
  motion = null;
  shell(skeleton());
  await refresh(true);
  focusHeading();
}
/** Signed photo links change on every read; they shouldn't cause a redraw by themselves. */
function contentKey(/** @type {import("./types.js").Week} */ data) {
  return JSON.stringify(data, (key, value) =>
    key.endsWith("_url") ? Boolean(value) : value,
  );
}
/** @param {boolean} [initial] */
async function refresh(initial = false) {
  if (state.working) return;
  if (refreshing) {
    refreshAgain = true;
    return;
  }
  refreshing = true;
  const requestEpoch = epoch,
    selected = state.selected;
  let changed = true;
  try {
    // The first authenticated read warms the server's signing-key cache. A
    // parallel cold read deliberately returns 503 while that refresh is held.
    const identity = !state.me || initial ? await getMe() : state.me;
    const data = await getWeek(selected);
    if (requestEpoch !== epoch || selected !== state.selected) return;
    const key = contentKey(data);
    changed =
      key !== fingerprint ||
      !state.me ||
      state.offline ||
      Boolean(problem) ||
      // Photo links last 30 minutes, so a long-open page still gets fresh ones.
      Date.now() - renderedAt > 15 * 60000;
    fingerprint = key;
    state.me = identity;
    state.data = data;
    state.offline = false;
    problem = "";
    if (!selected)
      badge = data.week.closed ? 0 : data.clear_count;
    state.lastLoaded = Date.now();
  } catch (error) {
    if (requestEpoch !== epoch) return;
    if (error instanceof HouseError && error.status === 401) {
      await signOut().catch(() => {});
      clearSession();
      announce("Please sign in again.");
      return;
    }
    if (error instanceof HouseError && error.status >= 400 && error.status < 429)
      problem = error.message;
    else state.offline = true;
  } finally {
    refreshing = false;
    state.loading = false;
    if (requestEpoch === epoch && changed) render();
    if (refreshAgain) {
      refreshAgain = false;
      if (state.me || state.offline) void refresh();
    }
  }
}
/** Never replace a note someone is typing or a sheet or photo they have open. */
function busy() {
  return (
    document.activeElement?.matches("input,textarea,select") ||
    document.querySelector("dialog[open]")
  );
}
for (const id of ["sheet", "photo-dialog"])
  document.getElementById(id)?.addEventListener("close", () => {
    if (renderOwed && !busy()) render();
  });
/** @param {boolean} [force] */
function render(force = false) {
  if (!force && busy()) {
    renderOwed = true;
    return;
  }
  renderOwed = false;
  renderedAt = Date.now();
  if (problem) {
    transition(() =>
      shell(
        h(
          "div",
          { class: "page-message" },
          heading("We can’t show this yet", problem),
          button("Sign out", () => void leave(), "secondary"),
        ),
      ),
    );
    return;
  }
  if (!state.me && !state.offline && !state.loading) {
    renderAuth();
    return;
  }
  if (state.me && page() === "footage") {
    // Weekly background reads must not replace or interrupt the active video.
    if (!root.querySelector(".footage-page"))
      transition(() => shell(footagePage()));
    else {
      paintBar();
      paintTabs();
      renderReceipts();
    }
    return;
  }
  if (!state.data) {
    transition(() =>
      shell(
        state.offline
          ? h(
              "div",
              { class: "page-message" },
              heading(
                "Can’t reach the kitchen",
                "The computer in the kitchen isn’t answering. Nothing’s lost, and it usually comes back in a minute or two.",
              ),
              button("Try again", () => void refresh(), "secondary"),
            )
          : skeleton(),
      ),
    );
    return;
  }
  transition(() =>
    shell(
      page() === "help"
        ? helpPage(context)
        : page() === "name"
          ? namePage(context)
          : page() === "mine"
            ? minePage(context)
            : page() === "past"
              ? pastPage(context)
              : weekPage(context),
    ),
  );
}
/** @param {Visit} dish @param {'name'|'dispute'|'settle'|'verdict'} action @param {unknown} body @param {string} message */
async function mutate(dish, action, body, message) {
  if (state.working || state.offline) {
    announce("Reconnect to the kitchen before changing a dish.");
    return false;
  }
  if (action === "name" || action === "verdict") {
    answerError = "";
    motion = "advance";
    answers.add(dish, action, body, message);
    window.scrollTo(0, 0);
    focusHeading();
    return true;
  }
  state.working = true;
  epoch++;
  const scope = [...document.querySelectorAll("[data-dish-id]")].find(
    (el) => el.getAttribute("data-dish-id") === String(dish.id),
  );
  scope?.querySelectorAll("button").forEach((b) => (b.disabled = true));
  const error = scope?.querySelector(".action-error");
  if (error) error.textContent = "";
  try {
    const updated = await act(dish.id, action, body);
    if (state.data) {
      state.data.visits = state.data.visits.map((d) =>
        d.id === dish.id ? updated : d,
      );
      state.data.unnamed_count = state.data.visits
        .filter((d) => !d.resident)
        .reduce((n, v) => n + v.dish_ids.length, 0);
    }
    announce(message);
    if (!updated.resident && updated.verdict?.kind === "cant_tell")
      state.skipped.add(dish.id);
    else state.skipped.delete(dish.id);
    state.working = false;
    await refresh();
    render();
    focusHeading();
    return true;
  } catch (e) {
    if (e instanceof HouseError && e.status === 401) {
      await signOut().catch(() => {});
      clearSession();
    } else {
      const message =
        e instanceof Error ? e.message : "That didn’t save. Please try again.";
      if (error) error.textContent = message;
      if (e instanceof HouseError && (e.status === 0 || e.status >= 500)) {
        state.offline = true;
        announce(`${message} Your change wasn’t confirmed.`);
      }
    }
    return false;
  } finally {
    state.working = false;
    scope?.querySelectorAll("button").forEach((b) => (b.disabled = false));
  }
}
/** @type {import("./types.js").ScreenContext} */
const context = {
  root,
  state,
  href,
  navigate,
  link,
  signOut: leave,
  render,
  refresh,
  focusHeading,
  mutate,
  enter,
};
const { renderAuth, renderJoin } = createAuthScreens(context);
window.addEventListener("popstate", (event) => {
  stopFootage();
  root.querySelector(".footage-page")?.remove();
  const before = lastDepth;
  lastDepth = Number(event.state?.depth) || 0;
  syncSelection();
  motion = lastDepth < before ? "pop" : "push";
  render(true);
  window.scrollTo(0, scrolls.get(location.pathname + location.search) || 0);
  if (state.me) void refresh();
});
window.addEventListener("focus", () => {
  if (state.me && !document.hidden) void refresh();
});
window.addEventListener("pagehide", () => {
  void answers.flush();
});
document.addEventListener("visibilitychange", () => {
  if (document.hidden) void answers.flush();
  if (!document.hidden && state.me) void refresh();
});
setInterval(() => {
  if (!document.hidden && state.me) void refresh();
}, 60000);
syncSelection();
if (location.pathname.replace(/\/$/, "") === "/join") renderJoin();
else
  accessToken()
    .then((token) => (token ? enter() : renderAuth()))
    .catch(renderAuth);
