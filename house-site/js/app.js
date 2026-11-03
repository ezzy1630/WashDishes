import {
  h,
  fill,
  icon,
  button,
  field,
  empty,
  photo,
  imageURL,
  date,
  announce,
} from "./ui.js";
import {
  mock,
  configured,
  accessToken,
  signIn,
  signOut,
  onSignedOut,
} from "./auth.js";
import { getNames, getMe, getWeek, redeem, act, HouseError } from "./api.js";

/** @typedef {Awaited<ReturnType<typeof getWeek>>} Week */
/** @typedef {Week['visits'][number]} Visit */
/** @typedef {Awaited<ReturnType<typeof getMe>>} Me */
/** @typedef {'week'|'name'|'mine'|'past'} Page */
const root = /** @type {HTMLDivElement} */ (document.getElementById("app"));
/** @type {{me:Me|null, data:Week|null, names:Awaited<ReturnType<typeof getNames>>, offline:boolean, lastLoaded:number, selected:string, skipped:Set<string|number>, working:boolean, loading:boolean}} */
const state = {
  me: null,
  data: null,
  names: [],
  offline: false,
  lastLoaded: 0,
  selected: "",
  skipped: new Set(),
  working: false,
  loading: false,
};
let epoch = 0,
  refreshing = false,
  refreshAgain = false;
const mockQuery = mock ? "?mock=1" : "";
/** @param {string} path */
function href(path) {
  return path + mockQuery;
}
/** @returns {Page} */
function page() {
  return location.pathname.startsWith("/name")
    ? "name"
    : location.pathname.startsWith("/mine")
      ? "mine"
      : location.pathname.startsWith("/past")
        ? "past"
        : "week";
}
/** @param {string} path */
function navigate(path) {
  history.pushState({}, "", href(path));
  state.skipped.clear();
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
/** @param {string} title @param {string} [intro] */
function heading(title, intro = "") {
  return h(
    "header",
    { class: "page-heading" },
    h("h1", { tabindex: "-1" }, title),
    intro ? h("p", {}, intro) : null,
  );
}
/** @param {string} text @param {string} [className] */
function tag(text, className = "") {
  return h("span", { class: `tag ${className}` }, text);
}
function brand() {
  return h(
    "a",
    {
      class: "brand",
      href: href("/"),
      onclick: (/** @type {MouseEvent} */ e) => {
        e.preventDefault();
        navigate("/");
      },
    },
    h("span", { class: "brand-mark" }, icon("house")),
    h("span", {}, "Highest House", h("small", {}, "Dish Watch")),
  );
}
function footer() {
  return h(
    "footer",
    { class: "site-footer" },
    h("p", {}, "A shared kitchen. A little accountability."),
    state.me
      ? h(
          "div",
          { class: "footer-account" },
          h("span", {}, state.me.resident.name),
          button(
            "Sign out",
            async () => {
              try {
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
        )
      : h("span", {}, "Just for the house."),
  );
}
function clearSession() {
  epoch++;
  state.me = null;
  state.data = null;
  state.lastLoaded = 0;
  state.offline = false;
  state.loading = false;
  state.skipped.clear();
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
  ]);
  fill(
    root,
    h(
      "header",
      { class: "site-header" },
      h(
        "div",
        { class: "header-inner" },
        brand(),
        h("span", { class: "house-note" }, "The weekly kitchen league"),
      ),
    ),
    mock
      ? h(
          "div",
          { class: "demo-banner" },
          "Local demo · Example names and results; private test photos",
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
async function loadNames() {
  try {
    state.names = (await getNames()).sort((a, b) =>
      a.name.localeCompare(b.name),
    );
    return true;
  } catch {
    return false;
  }
}
function renderAuth() {
  if (location.pathname.replace(/\/$/, "") === "/join") {
    renderJoin();
    return;
  }
  const select = h(
    "select",
    { name: "resident", required: true },
    h(
      "option",
      { value: "" },
      state.names.length ? "Pick your name" : "Loading names…",
    ),
    state.names.map((n) => h("option", { value: n.slug }, n.name)),
  );
  const password = h("input", {
    type: "password",
    name: "password",
    autocomplete: "current-password",
    required: true,
  });
  const error = h("p", { class: "form-error", role: "alert" });
  const submit = h(
    "button",
    { type: "submit", class: "primary" },
    "Sign in",
    icon("arrow"),
  );
  const form = h(
    "form",
    {
      onsubmit: async (/** @type {SubmitEvent} */ event) => {
        event.preventDefault();
        if (submit.disabled) return;
        submit.disabled = true;
        submit.textContent = "Signing in…";
        error.textContent = "";
        try {
          await signIn(select.value, password.value);
          password.value = "";
          await enter();
        } catch (e) {
          error.textContent =
            e instanceof Error ? e.message : "Couldn’t sign in. Try again.";
          password.focus();
        } finally {
          submit.disabled = false;
          submit.replaceChildren("Sign in", icon("arrow"));
        }
      },
    },
    field("Your name", select),
    field("Password", password),
    error,
    submit,
  );
  const connect = h("div", { class: "auth-connection" });
  fill(
    root,
    h("header", { class: "auth-header" }, brand()),
    mock
      ? h(
          "p",
          { class: "demo-banner" },
          "Local demo · Use password “house-demo”",
        )
      : null,
    h(
      "main",
      { id: "main", class: "auth" },
      h("div", { class: "auth-symbol", "aria-hidden": "true" }, icon("dish")),
      heading(
        "Good house. Shared kitchen.",
        "Sign in to see how this week is stacking up.",
      ),
      form,
      connect,
      h(
        "p",
        { class: "auth-help" },
        "First time here, or forgot your password?",
        h("br"),
        "Ask Ezzy for a fresh house link.",
      ),
    ),
    footer(),
  );
  if (!configured && !mock) {
    connect.append(
      h(
        "p",
        { class: "help" },
        "Sign-in is waiting for the house connection to be set up.",
      ),
    );
    submit.disabled = true;
  }
  if (!state.names.length) {
    select.disabled = true;
    submit.disabled = true;
    void loadNames().then((ok) => {
      if (!select.isConnected) return;
      fill(
        select,
        h("option", { value: "" }, "Pick your name"),
        state.names.map((n) => h("option", { value: n.slug }, n.name)),
      );
      select.disabled = !ok;
      submit.disabled = !ok || (!configured && !mock);
      if (!ok)
        connect.replaceChildren(
          h("p", { role: "status" }, "Dish Watch is offline right now"),
          button("Try again", renderAuth, "secondary"),
        );
    });
  }
  document.title = "Sign in · Highest House";
}
function renderJoin() {
  const token = location.hash.slice(1);
  const error = h("p", { class: "form-error", role: "alert" });
  const password = h("input", {
    type: "password",
    name: "password",
    autocomplete: "new-password",
    minlength: 8,
    required: true,
  });
  const confirm = h("input", {
    type: "password",
    name: "confirm",
    autocomplete: "new-password",
    minlength: 8,
    required: true,
  });
  const submit = h(
    "button",
    { class: "primary", type: "submit" },
    "Set password & come in",
    icon("arrow"),
  );
  const form = h(
    "form",
    {
      onsubmit: async (/** @type {SubmitEvent} */ event) => {
        event.preventDefault();
        if (submit.disabled) return;
        error.textContent = "";
        if (password.value.length < 8) {
          error.textContent = "Use at least 8 characters.";
          password.focus();
          return;
        }
        if (password.value !== confirm.value) {
          error.textContent = "Those passwords don’t match yet.";
          confirm.focus();
          return;
        }
        submit.disabled = true;
        submit.textContent = "Setting your password…";
        let saved = false;
        try {
          const { login } = await redeem(token, password.value);
          saved = true;
          history.replaceState({}, "", href("/join"));
          await signIn(login, password.value);
          password.value = "";
          confirm.value = "";
          history.replaceState({}, "", href("/"));
          await enter();
        } catch (e) {
          if (saved) {
            form.replaceChildren(
              h(
                "p",
                {},
                "Your password is saved. Sign in with your name and new password.",
              ),
              h("a", { class: "primary", href: href("/") }, "Go to sign in"),
            );
          } else {
            error.textContent =
              e instanceof Error
                ? e.message
                : "Couldn’t use that link. Try again.";
            submit.disabled = false;
            submit.replaceChildren("Set password & come in", icon("arrow"));
          }
        }
      },
    },
    field(
      "New password",
      password,
      "At least 8 characters. Make it one you don’t use elsewhere.",
    ),
    field("Confirm password", confirm),
    error,
    submit,
  );
  fill(
    root,
    h("header", { class: "auth-header" }, brand()),
    mock
      ? h(
          "p",
          { class: "demo-banner" },
          "Local demo · Invite and password reset",
        )
      : null,
    h(
      "main",
      { id: "main", class: "auth" },
      heading(
        "Your place in the house.",
        "Set a password, or choose a new one. Same house, fresh start.",
      ),
      token
        ? form
        : empty(
            "This link needs a token",
            "Open the complete house link Ezzy sent you, or ask for a fresh one.",
          ),
      h(
        "a",
        { class: "text-link back-link", href: href("/") },
        "Back to sign in",
      ),
    ),
    footer(),
  );
  document.title = "Join the house · Dish Watch";
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
    if (!state.me || initial) {
      const identity = await getMe();
      if (requestEpoch !== epoch) return;
      state.me = identity;
    }
    const data = await getWeek(selected);
    if (requestEpoch !== epoch || selected !== state.selected) return;
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
    page() === "name"
      ? namePage()
      : page() === "mine"
        ? minePage()
        : page() === "past"
          ? pastPage()
          : weekPage(),
  );
}
/** @param {Week} data */
function range(data) {
  return `${date(data.week.start, data.week.timezone)} – ${date(data.week.end, data.week.timezone)}`;
}
/** @param {Week} data */
function standings(data) {
  const rows = [...data.standings].sort(
    (a, b) => b.points - a.points || a.name.localeCompare(b.name),
  );
  const top = rows[0],
    second = rows[1],
    lead = top ? top.points - (second?.points || 0) : 0;
  const threshold =
    top && data.rules.enabled
      ? Math.max(
          0,
          data.rules.min_points - top.points,
          (second?.points || 0) + data.rules.min_lead - top.points,
          second && top.points === second.points ? 1 : 0,
        )
      : 0;
  const scale = Math.max(data.rules.min_points, top?.points || 0, 1);
  const gate = (
    /** @type {string} */ label,
    /** @type {number} */ current,
    /** @type {number} */ target,
  ) =>
    h(
      "div",
      { class: "gate" },
      h(
        "div",
        { class: "gate-label" },
        h("span", {}, label),
        h("strong", {}, `${current} / ${target}`),
      ),
      h("meter", {
        min: 0,
        max: Math.max(target, current, 1),
        value: current,
        "aria-label": `${label}: ${current}, needs ${target}`,
      }),
      h(
        "span",
        { class: current >= target ? "gate-met" : "help" },
        current >= target ? "Met" : `${target - current} to go`,
      ),
    );
  return h(
    "section",
    { class: "standings" },
    h(
      "div",
      { class: "section-heading" },
      h("h2", {}, "The week’s stack"),
      h("span", { class: "help" }, "Points"),
    ),
    h(
      "ol",
      { class: "rankings" },
      rows.map((r, i) => {
        const svg = document.createElementNS(
          "http://www.w3.org/2000/svg",
          "svg",
        );
        svg.setAttribute("viewBox", "0 0 100 5");
        svg.setAttribute("preserveAspectRatio", "none");
        svg.setAttribute("aria-hidden", "true");
        svg.classList.add("rank-bar");
        const rect = document.createElementNS(svg.namespaceURI, "rect");
        rect.setAttribute("width", String((r.points / scale) * 100));
        rect.setAttribute("height", "5");
        rect.setAttribute("rx", "2.5");
        svg.append(rect);
        const line = document.createElementNS(svg.namespaceURI, "path");
        const x = Math.min(99, (data.rules.min_points / scale) * 100);
        line.setAttribute("d", `M${x} 0v5`);
        line.classList.add("threshold-line");
        svg.append(line);
        return h(
          "li",
          { class: i === 0 && r.points ? "first-place" : "" },
          h("span", { class: "rank", "aria-label": `Rank ${i + 1}` }, i + 1),
          h(
            "div",
            { class: "rank-person" },
            h(
              "div",
              { class: "rank-name" },
              h("span", {}, r.name),
              String(r.resident_id) === String(state.me?.resident.id)
                ? tag("You")
                : null,
              h(
                "span",
                { class: "dish-count" },
                `${r.dishes} ${r.dishes === 1 ? "dish" : "dishes"}`,
              ),
            ),
            svg,
          ),
          h("strong", { class: "points" }, r.points),
        );
      }),
    ),
    data.rules.enabled
      ? h(
          "div",
          { class: "thresholds" },
          h(
            "p",
            {},
            `A pick needs ${data.rules.min_points} points and a ${data.rules.min_lead}-point lead.`,
            h("span", { class: "help" }, " Ties mean no pick."),
          ),
          top
            ? h(
                "div",
                { class: "gates" },
                gate(`${top.name}’s points`, top.points, data.rules.min_points),
                gate("Lead over second", lead, data.rules.min_lead),
              )
            : null,
          data.pick.resident_id === null && threshold > 0 && top
            ? h(
                "p",
                { class: "threshold-explanation" },
                `${top.name} would need ${threshold} more ${threshold === 1 ? "point" : "points"} for a clear pick.`,
              )
            : null,
        )
      : h("p", { class: "quiet-note" }, "The weekly pick is paused."),
  );
}
function weekPage() {
  const data = /** @type {Week} */ (state.data),
    duty = data.on_duty;
  return h(
    "div",
    {},
    heading(
      "This week",
      `${range(data)} · ${data.week.closed ? "Closed" : `Closes ${date(data.week.closes_at, data.week.timezone, true)}`} · ${data.week.timezone}`,
    ),
    h(
      "div",
      { class: "week-layout" },
      h(
        "div",
        { class: "week-main" },
        h(
          "section",
          { class: "duty" },
          h("div", { class: "duty-mark", "aria-hidden": "true" }, icon("dish")),
          h(
            "div",
            {},
            h(
              "h2",
              {},
              duty ? `${duty.name}’s on kitchen duty` : "No one’s on duty yet",
            ),
            h(
              "p",
              {},
              duty?.why || "The house hasn’t set a kitchen duty person yet.",
            ),
            duty
              ? h(
                  "p",
                  { class: "help" },
                  `Since ${date(duty.since, data.week.timezone)}`,
                )
              : null,
          ),
        ),
        h(
          "section",
          { class: "pick" },
          h(
            "h2",
            {},
            data.pick.name
              ? `${data.pick.name} ${data.week.closed ? "is picked" : "is in line"} for next week.`
              : data.week.closed
                ? "No one picked this week."
                : "No clear pick yet.",
          ),
          h("p", {}, data.pick.reason),
          !data.pick.name && duty
            ? h(
                "p",
                { class: "help" },
                `${duty.name} stays on duty if there’s no clear pick.`,
              )
            : null,
        ),
        standings(data),
      ),
      h(
        "aside",
        { class: "week-aside" },
        h(
          "a",
          {
            class: "name-cta",
            href: href("/name"),
            onclick: (/** @type {MouseEvent} */ e) => {
              e.preventDefault();
              navigate("/name");
            },
          },
          h(
            "div",
            { class: "cta-art", "aria-hidden": "true" },
            icon("dish"),
            icon("dish"),
            icon("dish"),
          ),
          h(
            "h2",
            {},
            data.unreviewed_count
              ? `${data.unreviewed_count} to review`
              : "Nothing waiting for review",
          ),
          h(
            "p",
            {},
            data.unreviewed_count
              ? "A dish only counts once someone names it."
              : "Thanks for helping the house keep it fair.",
          ),
          h(
            "span",
            { class: "cta-action" },
            data.unreviewed_count ? "Review the visits" : "See reviewed visits",
            icon("arrow"),
          ),
        ),
        h(
          "section",
          { class: "house-rules" },
          h("h2", {}, "How we’re playing"),
          h(
            "p",
            {},
            `Pans & pots: ${data.rules.weights.pan === data.rules.weights.pot ? `${data.rules.weights.pan} points each` : `${data.rules.weights.pan} & ${data.rules.weights.pot} points`}. Everything else: ${data.rules.weights.default}.`,
          ),
          h(
            "p",
            {},
            "A dish only counts once someone names it. A clear weekly pick takes kitchen duty next week. Too close? The current person stays on.",
          ),
          h(
            "p",
            {},
            "On duty? Help name the dishes. Any housemate can say “That wasn’t me.” Another person settles it.",
          ),
        ),
      ),
    ),
  );
}
/** @param {Visit} visit */
function verdictText(visit) {
  if (!visit.verdict) return "";
  const labels = {
    not_dish: "Marked not a dish",
    clean: "Marked clean",
    duplicate: "Marked same as another visit",
    cant_tell: "Couldn’t tell",
  };
  return `${labels[visit.verdict.kind]} by ${visit.verdict.by.name}`;
}
/** @param {Week} data */
function reviewedVisits(data) {
  const reviewed = data.visits.filter((v) => v.resident || v.verdict);
  return reviewed.length
    ? h(
        "details",
        { class: "reviewed-visits" },
        h("summary", {}, `Reviewed this week (${reviewed.length})`),
        h(
          "div",
          { class: "reviewed-list" },
          reviewed.map((visit) =>
            h(
              "article",
              { class: "reviewed-visit" },
              h("h3", {}, visit.title),
              h(
                "p",
                { class: "help" },
                `${visit.area} · ${date(visit.at, data.week.timezone, true)}`,
              ),
              h(
                "p",
                { class: "review-attribution" },
                verdictText(visit) ||
                  (visit.named_by
                    ? `Named as ${visit.resident?.name} by ${visit.named_by.name}`
                    : `Named as ${visit.resident?.name}`),
              ),
              h(
                "details",
                {},
                h("summary", {}, "See photos"),
                photo(
                  visit.filmstrip_url || visit.photo_url,
                  `${visit.title} reviewed photos`,
                  true,
                  true,
                ),
              ),
            ),
          ),
        ),
      )
    : null;
}
/** @param {Visit} visit @param {Week} data */
function verdictChoices(visit, data) {
  const choices = h("div", { class: "other-answers" });
  const picker = h("div", { class: "duplicate-picker" });
  const targets = data.visits.filter((v) => v.id !== visit.id);
  const duplicate = button(
    "Same as another",
    () => {
      if (picker.hasChildNodes()) {
        picker.replaceChildren();
        duplicate.setAttribute("aria-expanded", "false");
        return;
      }
      duplicate.setAttribute("aria-expanded", "true");
      picker.replaceChildren(
        h("p", { class: "help" }, "Which visit is this the same as?"),
        ...targets.map((target) => {
          const choice = button(
            "",
            () =>
              void mutate(
                visit,
                "verdict",
                { verdict: "duplicate", of_visit_id: target.id },
                "Marked as a duplicate. On to the next visit.",
              ),
            "duplicate-choice",
          );
          const thumbnail = imageURL(target.photo_url || target.filmstrip_url);
          if (thumbnail) {
            const img = h("img", {
              src: thumbnail,
              alt: "",
              width: 80,
              height: 60,
              loading: "eager",
              decoding: "sync",
              referrerpolicy: "no-referrer",
            });
            img.onerror = () => img.remove();
            choice.append(img);
          }
          choice.append(
            h(
              "span",
              {},
              h("strong", {}, target.title),
              h(
                "span",
                { class: "help" },
                `${target.area} · ${date(target.at, data.week.timezone, true)}`,
              ),
              target.resident
                ? h(
                    "span",
                    { class: "help" },
                    `Named as ${target.resident.name}`,
                  )
                : null,
            ),
          );
          return choice;
        }),
        ...(!targets.length
          ? [
              h(
                "p",
                { class: "help" },
                "There isn’t another visit to link this to.",
              ),
            ]
          : []),
      );
      picker.querySelector("button")?.focus();
    },
    "verdict-button",
  );
  duplicate.setAttribute("aria-expanded", "false");
  choices.append(
    button(
      "Not a dish",
      () =>
        void mutate(
          visit,
          "verdict",
          { verdict: "not_dish" },
          "Marked not a dish. On to the next visit.",
        ),
      "verdict-button",
    ),
    button(
      "It’s clean",
      () =>
        void mutate(
          visit,
          "verdict",
          { verdict: "clean" },
          "Marked clean. On to the next visit.",
        ),
      "verdict-button",
    ),
    duplicate,
    button(
      "Can’t tell",
      () =>
        void mutate(
          visit,
          "verdict",
          { verdict: "cant_tell" },
          "Skipped. It stays unnamed.",
        ),
      "verdict-button",
    ),
  );
  return h(
    "div",
    { class: "verdict-answers" },
    h("p", { class: "help" }, "Something else?"),
    choices,
    picker,
  );
}
function namePage() {
  const data = /** @type {Week} */ (state.data);
  const unreviewed = data.visits.filter(
    (v) =>
      !v.resident &&
      v.can_name &&
      (!v.verdict || v.verdict.kind === "cant_tell"),
  );
  const remaining = unreviewed.filter((v) => !state.skipped.has(v.id)),
    visit = remaining[0];
  const intro = `${data.unreviewed_count} to review. A dish only counts once someone names it.`;
  const reviewed = reviewedVisits(data);
  if (!visit)
    return h(
      "div",
      {},
      heading("Name the dishes", intro),
      empty(
        state.skipped.size ? "That’s the round." : "You’re all caught up.",
        state.skipped.size
          ? "The visits you couldn’t tell stay unnamed. That’s better than a guess."
          : "There’s nothing waiting for you to review right now.",
      ),
      state.skipped.size
        ? button(
            "Look at skipped visits",
            () => {
              state.skipped.clear();
              render();
              focusHeading();
            },
            "secondary",
          )
        : null,
      reviewed,
    );
  const people = [...data.standings].sort((a, b) =>
    a.name.localeCompare(b.name),
  );
  return h(
    "div",
    {},
    heading("Name the dishes", intro),
    h(
      "article",
      { class: "naming", "data-dish-id": visit.id },
      h(
        "div",
        { class: "evidence" },
        h(
          "div",
          { class: "section-heading" },
          h("h2", {}, visit.title),
          tag(
            `${remaining.length} ${remaining.length === 1 ? "visit" : "visits"} left`,
          ),
        ),
        h(
          "p",
          { class: "help visit-metadata" },
          `${visit.area} · ${date(visit.at, data.week.timezone, true)} · ${visit.points} potential ${visit.points === 1 ? "point" : "points"}`,
        ),
        photo(visit.filmstrip_url, `${visit.title} arrival photos`, true),
        h(
          "div",
          { class: "later-photo" },
          h("h3", {}, "The later photo"),
          photo(visit.photo_url, `${visit.title}, later`),
        ),
      ),
      h(
        "section",
        { class: "name-panel" },
        h("h2", { class: "person-question" }, "Whose dishes are these?"),
        h(
          "p",
          { class: "help" },
          "Name the person who used or left them. Carrying dishes doesn’t make them yours.",
        ),
        visit.guess
          ? h(
              "div",
              { class: "camera-guess" },
              button(
                `Camera thinks ${visit.guess.name} — tap to confirm`,
                () =>
                  void mutate(
                    visit,
                    "name",
                    { resident_id: visit.guess?.resident_id },
                    "Confirmed. On to the next visit.",
                  ),
                "guess-choice",
              ),
              h("p", { class: "help" }, visit.guess.basis),
              h(
                "p",
                { class: "help" },
                "A guess only counts once someone confirms it.",
              ),
            )
          : null,
        h(
          "div",
          { class: "name-chips" },
          people
            .filter(
              (p) => String(p.resident_id) !== String(visit.guess?.resident_id),
            )
            .map((p) =>
              button(
                p.name,
                () =>
                  void mutate(
                    visit,
                    "name",
                    { resident_id: p.resident_id },
                    "Named. On to the next visit.",
                  ),
                "chip",
              ),
            ),
        ),
        verdictChoices(visit, data),
        h("p", { class: "action-error", role: "alert" }),
      ),
    ),
    reviewed,
  );
}
/** @param {Visit} dish @param {boolean} [settle] */
function record(dish, settle = false) {
  const data = /** @type {Week} */ (state.data),
    dispute = dish.dispute;
  const actions = h("div", { class: "record-actions" });
  if (settle && dish.can_settle)
    actions.append(
      h(
        "div",
        { class: "settle-actions" },
        button(
          "It was them",
          () =>
            void mutate(
              dish,
              "settle",
              { decision: "them" },
              "Settled. The house record is updated.",
            ),
          "secondary",
        ),
        button(
          "Not them",
          () =>
            void mutate(
              dish,
              "settle",
              { decision: "not_them" },
              "Settled. The house record is updated.",
            ),
          "secondary",
        ),
      ),
    );
  else if (dish.can_dispute && dispute?.state !== "open")
    actions.append(
      button(
        "That wasn’t me",
        () => {
          const note = h("textarea", {
            name: `note-${dish.id}`,
            rows: 3,
            maxlength: 1000,
            required: true,
            placeholder: "Tell the house what happened.",
          });
          const error = h("p", { class: "form-error", role: "alert" });
          const form = h(
            "form",
            {
              class: "dispute-form",
              onsubmit: (/** @type {SubmitEvent} */ e) => {
                e.preventDefault();
                if (!note.value.trim()) {
                  error.textContent = "Add a short note before sending.";
                  note.focus();
                  return;
                }
                void mutate(
                  dish,
                  "dispute",
                  { note: note.value.trim() },
                  "Dispute sent. Another housemate can settle it.",
                );
              },
            },
            field("What happened?", note),
            error,
            h(
              "div",
              { class: "form-actions" },
              h("button", { type: "submit", class: "primary" }, "Send dispute"),
              button(
                "Cancel",
                () => {
                  actions.replaceChildren(disputeButton);
                },
                "quiet",
              ),
            ),
          );
          const disputeButton = button(
            "That wasn’t me",
            () => {
              actions.replaceChildren(form);
              note.focus();
            },
            "secondary",
          );
          actions.replaceChildren(form);
          note.focus();
        },
        "secondary",
      ),
    );
  const named = dish.named_by
    ? `Named by ${dish.named_by.name}${dish.named_at ? ` · ${date(dish.named_at, data.week.timezone, true)}` : ""}`
    : "No housemate named on the record.";
  return h(
    "article",
    { class: "dish-record", "data-dish-id": dish.id },
    h(
      "header",
      { class: "record-heading" },
      h(
        "div",
        {},
        h(
          "h3",
          {},
          dish.title,
          settle && dish.resident ? ` · ${dish.resident.name}` : "",
        ),
        h(
          "p",
          { class: "help" },
          `${dish.area} · ${date(dish.at, data.week.timezone, true)}`,
        ),
      ),
      tag(`${dish.points} ${dish.points === 1 ? "point" : "points"}`),
    ),
    photo(dish.filmstrip_url || dish.photo_url, `${dish.title} evidence`, true),
    dish.filmstrip_url && dish.photo_url
      ? h(
          "details",
          { class: "later-details" },
          h("summary", {}, "See the later photo"),
          photo(dish.photo_url, `${dish.title}, later`),
        )
      : null,
    h("p", { class: "help attribution" }, named),
    dish.verdict
      ? h("p", { class: "review-attribution" }, verdictText(dish))
      : null,
    dispute
      ? h(
          "div",
          { class: "dispute-note" },
          tag(
            dispute.state === "open"
              ? "Disputed · open"
              : dispute.state === "upheld"
                ? "Settled · it was them"
                : "Settled · not them",
            "review",
          ),
          h(
            "p",
            {},
            h("strong", {}, `${dispute.by.name}: `),
            h("q", {}, dispute.note),
          ),
          dispute.settled_by
            ? h("p", { class: "help" }, `Settled by ${dispute.settled_by.name}`)
            : !settle
              ? h(
                  "p",
                  { class: "help" },
                  "Waiting for another housemate to settle it.",
                )
              : null,
        )
      : null,
    actions,
    h("p", { class: "action-error", role: "alert" }),
  );
}
function minePage() {
  const data = /** @type {Week} */ (state.data),
    mine = data.visits.filter(
      (d) =>
        d.resident && String(d.resident.id) === String(state.me?.resident.id),
    ),
    open = data.visits.filter(
      (d) => d.dispute?.state === "open" && d.can_settle,
    );
  return h(
    "div",
    {},
    heading(
      "My dishes",
      "Visits named as yours. Take a look — you always get a say.",
    ),
    h(
      "div",
      { class: "mine-layout" },
      h(
        "section",
        {},
        h(
          "div",
          { class: "section-heading" },
          h("h2", {}, "Named as mine"),
          tag(String(mine.length)),
        ),
        mine.length
          ? h(
              "div",
              { class: "record-list" },
              mine.map((d) => record(d)),
            )
          : empty(
              "Nothing named as yours.",
              "No visits have your name on them this week.",
            ),
      ),
      h(
        "section",
        { class: "settlement-list" },
        h(
          "div",
          { class: "section-heading" },
          h("h2", {}, "Help settle a dispute"),
          tag(String(open.length)),
        ),
        h(
          "p",
          { class: "help section-intro" },
          "Only settle it if you know. The people involved sit this one out.",
        ),
        open.length
          ? h(
              "div",
              { class: "record-list" },
              open.map((d) => record(d, true)),
            )
          : empty(
              "All settled here.",
              "Open disputes you can settle will show up here.",
            ),
      ),
    ),
  );
}
/** @param {Visit} dish @param {'name'|'dispute'|'settle'|'verdict'} action @param {unknown} body @param {string} message */
async function mutate(dish, action, body, message) {
  if (state.working || state.offline) {
    announce("Reconnect to the kitchen before changing a dish.");
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
    if (action === "name" || action === "verdict") focusHeading();
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
function pastPage() {
  const data = /** @type {Week} */ (state.data);
  const choices = [...data.weeks]
    .sort((a, b) => b.start.localeCompare(a.start))
    .map((w) => {
      const choice = button(
        "",
        () => {
          state.selected = w.start;
          history.replaceState(
            {},
            "",
            `${href("/past")}${mock ? "&" : "?"}start=${encodeURIComponent(w.start)}`,
          );
          state.data = null;
          render();
          void refresh();
        },
        `week-choice ${w.start === data.week.start ? "selected" : ""}`,
      );
      choice.setAttribute("aria-pressed", String(w.start === data.week.start));
      choice.append(
        h(
          "span",
          { class: "week-copy" },
          h("strong", {}, date(w.start, data.week.timezone)),
          h(
            "span",
            {},
            w.pick_name
              ? `${w.pick_name} · ${w.closed ? "picked" : "in line"}`
              : w.closed
                ? "No one picked"
                : "Still playing",
          ),
        ),
        h(
          "span",
          { class: "week-indicator", "aria-hidden": "true" },
          icon("arrow"),
        ),
      );
      return h("li", {}, choice);
    });
  return h(
    "div",
    {},
    heading("Past weeks", "The kitchen has a little history."),
    h(
      "div",
      { class: "past-layout" },
      h(
        "section",
        {},
        h("h2", {}, "Pick a week"),
        h("ul", { class: "week-list" }, choices),
      ),
      h(
        "section",
        { class: "past-detail" },
        h(
          "div",
          { class: "section-heading" },
          h("h2", {}, range(data)),
          tag(data.week.closed ? "Closed" : "In progress"),
        ),
        h(
          "p",
          { class: "past-pick" },
          data.pick.name
            ? `${data.pick.name} ${data.week.closed ? "was picked." : "is in line."}`
            : "No one picked.",
        ),
        h("p", { class: "help" }, data.pick.reason),
        standings(data),
      ),
    ),
  );
}
window.addEventListener("popstate", () => {
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
document.addEventListener("visibilitychange", () => {
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
