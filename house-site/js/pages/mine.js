import { empty, group, h, heading, openSheet, row } from "../ui.js";
/** @typedef {import("../types.js").Week} Week */
import { record } from "./visit.js";
import { howItWorks } from "./week.js";
import { learningRow } from "./help.js";

const standalone = () =>
  matchMedia("(display-mode: standalone)").matches ||
  /** @type {{standalone?:boolean}} */ (navigator).standalone === true;
function addToHomeScreen() {
  openSheet("Add to your Home Screen", [
    h("p", {}, "It opens full screen, just like an app."),
    h(
      "ol",
      { class: "how-steps" },
      h("li", {}, "Tap the Share button at the bottom of Safari."),
      h("li", {}, "Scroll down and tap Add to Home Screen."),
      h("li", {}, "Tap Add, then open House from your Home Screen and sign in once more."),
    ),
  ]);
}
/** @param {import("../types.js").ScreenContext} context */
export function minePage(context) {
  const { state, href, navigate } = context;
  const data = /** @type {Week} */ (state.data),
    viewer = state.me?.resident.id,
    mine = data.visits.filter(
      (d) => d.resident && String(d.resident.id) === String(viewer),
    ),
    open = data.visits.filter(
      (d) => d.dispute?.state === "open" && d.can_settle,
    );
  const points =
    data.standings.find((p) => String(p.resident_id) === String(viewer))
      ?.points ?? 0;
  const go = (/** @type {string} */ path) => (/** @type {MouseEvent} */ e) => {
    e.preventDefault();
    navigate(path);
  };
  const phone = /iPhone|iPad|iPod/.test(navigator.userAgent);
  return h(
    "div",
    { class: "mine-page" },
    heading(
      state.me?.resident.name || "Me",
      `${points} ${points === 1 ? "point" : "points"} this week`,
    ),
    h(
      "div",
      { class: "mine-layout" },
      h(
        "div",
        { class: "mine-main" },
        open.length
          ? h(
              "section",
              { class: "settlement-list", id: "settlements" },
              h("h2", { class: "group-title" }, `Help settle ${open.length === 1 ? "this" : "these"}`),
              h(
                "p",
                { class: "group-note" },
                "Someone says this wasn’t theirs, and their points are paused. Only answer if you know.",
              ),
              h(
                "div",
                { class: "record-list" },
                open.map((d) => record(d, true, context)),
              ),
            )
          : null,
        h(
          "section",
          { class: "mine-dishes" },
          h("h2", { class: "group-title" }, "Dishes with your name"),
          mine.length
            ? h(
                "div",
                { class: "record-list" },
                mine.map((d) => record(d, false, context)),
              )
            : empty(
                "Nothing named as yours.",
                "If someone names you on a dish this week, it’ll show up here and you can dispute it.",
              ),
        ),
        open.length
          ? null
          : h(
              "p",
              { class: "all-settled", id: "settlements" },
              h("span", { role: "heading", "aria-level": "2" }, "All settled here."),
              " No disputes need you right now.",
            ),
      ),
      h(
        "aside",
        { class: "mine-aside" },
        group(
          "",
          row({ label: "Past weeks", leading: "clock", href: href("/past"), run: go("/past") }),
          learningRow(context, true),
          row({
            label: "How it works",
            leading: "info",
            run: () => openSheet("How it works", howItWorks(data)),
          }),
          phone && !standalone()
            ? row({ label: "Add to Home Screen", leading: "plus", run: addToHomeScreen })
            : null,
        ),
        group(
          "",
          row({
            label: "Sign out",
            tone: "danger",
            chevron: false,
            run: () => void context.signOut(),
          }),
        ),
      ),
    ),
  );
}
