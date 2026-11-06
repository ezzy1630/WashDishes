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
import { announce, brand, button, fill, h, heading } from "./ui.js";

/** @typedef {import('./types.js').Visit} Visit */
/** @typedef {'week'|'name'|'mine'|'past'|'help'|'footage'} Page */
const root = /** @type {HTMLDivElement} */ (document.getElementById("app"));
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
let answerError = "";
const answers = pendingAnswers({
  send: async (entry) => {
    const updated = await act(entry.visit.id, entry.action, entry.body);
    if (updated.verdict?.kind === "cant_tell")
      state.skipped.add(entry.visit.id);
  },
  changed: () => {
    state.pending = new Set(answers.entries.keys());
    const editing =
      document.activeElement?.matches("input,textarea,select") ||
      document.querySelector("dialog[open]");
    if (!editing) render();
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
  return location.pathname.startsWith("/footage") ? "footage" : location.pathname.startsWith("/help") ? "help" : location.pathname.startsWith("/name")
    ? "name"
    : location.pathname.startsWith("/mine")
      ? "mine"
      : location.pathname.startsWith("/past")
        ? "past"
        : "week";
}
/** @param {string} path */
function navigate(path) {
  stopFootage();
  root.querySelector(".footage-page")?.remove();
  void answers.flush();
  history.pushState({}, "", href(path));
  state.skipped.clear();
  state.showWeak = false;
  if (page() !== "past" && state.selected) {
    state.selected = "";
    state.data = null;
  }
  render();
  window.scrollTo(0, 0);
  focusHeading();
  if (state.me && !state.data) void refresh();
}
function focusHeading() {
  requestAnimationFrame(() =>
    document.querySelector("h1")?.focus({ preventScroll: true }),
  );
}
function footer() {
  if (!state.me) return null;
  return h(
    "footer",
    { class: "site-footer" },
    h(
      "div",
      { class: "footer-account" },
      h("span", {}, state.me.resident.name),
      button(
        "Sign out",
        async () => {
          try {
            await answers.flush();
            await signOut();
            clearSession();
          } catch (error) {
            announce(
              error instanceof Error ? error.message : "Couldn’t sign out.",
            );
          }
        },
        "quiet",
      ),
    ),
  );
}
function clearSession() {
  stopFootage();
  answers.cancel();
  state.pending.clear();
  answerError = "";
  epoch++;
  state.me = null;
  state.data = null;
  state.lastLoaded = 0;
  state.offline = false;
  state.loading = false;
  state.skipped.clear();
  state.showWeak = false;
  state.selected = "";
  history.replaceState({}, "", href("/"));
  renderAuth();
}
onSignedOut(clearSession);
function offlineBanner() {
  return h(
    "aside",
    { class: "offline", role: "status" },
    h(
      "div",
      {},
      h("strong", {}, "Dish Watch is offline right now"),
      h(
        "p",
        {},
        state.lastLoaded
          ? `Last loaded ${new Date(state.lastLoaded).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })}. These are the last loaded results.`
          : "The kitchen connection is taking a break. Please try again in a bit.",
      ),
    ),
    button("Try again", () => void refresh(), "secondary"),
  );
}
/** @param {Node} content */
function shell(content) {
  const current = page();
  const links = /** @type {const} */ ([
    ["week", "This week", "/"],
    ["name", "Name dishes", "/name"],
    ["mine", "My dishes", "/mine"],
    ["past", "Past weeks", "/past"],
    ["footage", "Footage", "/footage"],
    ["help", "Help camera", "/help"],
  ]);
  fill(
    root,
    h(
      "header",
      { class: "site-header" },
      h(
        "div",
        { class: "header-inner" },
        brand(href, navigate),
        h("span", { class: "house-note" }, "The weekly kitchen league"),
      ),
    ),
    mock
      ? h(
          "div",
          { class: "demo-banner" },
          "Synthetic demo · Example names and results",
        )
      : null,
    h(
      "nav",
      { class: "nav", "aria-label": "Main navigation" },
      links.map(([key, label, path]) =>
        h(
          "a",
          {
            href: href(path),
            "aria-current": current === key ? "page" : null,
            onclick: (/** @type {MouseEvent} */ e) => {
              e.preventDefault();
              navigate(path);
            },
          },
          label,
        ),
      ),
    ),
    h(
      "main",
      { id: "main", class: `main page-${current}` },
      state.offline ? offlineBanner() : null,
      answerError
        ? h(
            "div",
            { class: "answer-error", role: "alert" },
            h("p", {}, answerError),
            button(
              "Dismiss",
              () => {
                answerError = "";
                render();
              },
              "quiet",
            ),
          )
        : null,
      answers.entries.size
        ? h(
            "div",
            {
              class: "answer-receipts",
              "aria-label": "Answers waiting to save",
            },
            [...answers.entries.values()].map((entry) =>
              h(
                "div",
                { class: "answer-receipt", role: "status" },
                h(
                  "p",
                  {},
                  h("strong", {}, entry.visit.title),
                  h(
                    "span",
                    {},
                    entry.saving
                      ? "Saving answer…"
                      : `${entry.message} Saving in a moment.`,
                  ),
                ),
                h(
                  "button",
                  {
                    type: "button",
                    class: "secondary",
                    disabled: entry.saving,
                    onclick: () => {
                      answers.undo(entry.visit.id);
                      focusHeading();
                    },
                  },
                  entry.saving ? "Saving…" : "Undo",
                ),
              ),
            ),
          )
        : null,
      content,
    ),
    footer(),
  );
  if (state.offline)
    root
      .querySelectorAll(
        '.chip,.guess-choice,.verdict-button,.duplicate-choice,.settle-actions button,.record-actions button[type="submit"]',
      )
      .forEach((button) => {
        if (button instanceof HTMLButtonElement) button.disabled = true;
      });
  document.title = `${links.find((l) => l[0] === current)?.[1]} · Highest House`;
}
async function enter() {
  state.loading = true;
  shell(h("p", { class: "loading", role: "status" }, "Loading the house…"));
  await refresh(true);
  focusHeading();
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
  try {
    // The first authenticated read warms the server's signing-key cache. A
    // parallel cold read deliberately returns 503 while that refresh is held.
    const identity = !state.me || initial ? await getMe() : state.me;
    const data = await getWeek(selected);
    if (requestEpoch !== epoch || selected !== state.selected) return;
    state.me = identity;
    state.data = data;
    state.offline = false;
    state.lastLoaded = Date.now();
  } catch (error) {
    if (requestEpoch !== epoch) return;
    if (error instanceof HouseError && error.status === 401) {
      await signOut().catch(() => {});
      clearSession();
      announce("Please sign in again.");
      return;
    }
    state.offline = true;
  } finally {
    refreshing = false;
    state.loading = false;
    // Never replace a housemate's note while they are typing or move their photo zoom.
    const editing =
      document.activeElement?.matches("input,textarea,select") ||
      document.querySelector("dialog[open]");
    if (requestEpoch === epoch && !editing) render();
    if (refreshAgain) {
      refreshAgain = false;
      if (state.me || state.offline) void refresh();
    }
  }
}
function render() {
  if (!state.me && !state.offline && !state.loading) {
    renderAuth();
    return;
  }
  if (state.me && page() === "footage") {
    // Weekly background reads must not replace or interrupt the active video.
    if (!root.querySelector(".footage-page")) shell(footagePage());
    return;
  }
  if (!state.data) {
    shell(
      state.offline
        ? heading(
            "We’ll be back.",
            "Your league will be here when the kitchen reconnects.",
          )
        : h("p", { class: "loading", role: "status" }, "Loading…"),
    );
    return;
  }
  shell(
    page() === "help" ? helpPage(context) : page() === "name"
      ? namePage(context)
      : page() === "mine"
        ? minePage(context)
        : page() === "past"
          ? pastPage(context)
          : weekPage(context),
  );
}
/** @param {Visit} dish @param {'name'|'dispute'|'settle'|'verdict'} action @param {unknown} body @param {string} message */
async function mutate(dish, action, body, message) {
  if (state.working || state.offline) {
    announce("Reconnect to the kitchen before changing a dish.");
    return;
  }
  if (action === "name" || action === "verdict") {
    answerError = "";
    answers.add(dish, action, body, message);
    window.scrollTo(0, 0);
    focusHeading();
    return;
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
        announce(
          "Dish Watch is offline right now. Your change wasn’t confirmed.",
        );
      }
    }
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
  render,
  refresh,
  focusHeading,
  mutate,
  enter,
};
const { renderAuth, renderJoin } = createAuthScreens(context);
window.addEventListener("popstate", () => {
  stopFootage();
  root.querySelector(".footage-page")?.remove();
  state.selected =
    page() === "past"
      ? new URLSearchParams(location.search).get("start") || ""
      : "";
  state.data = null;
  render();
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
state.selected =
  page() === "past"
    ? new URLSearchParams(location.search).get("start") || ""
    : "";
if (location.pathname.replace(/\/$/, "") === "/join") renderJoin();
else
  accessToken()
    .then((token) => (token ? enter() : renderAuth()))
    .catch(renderAuth);
