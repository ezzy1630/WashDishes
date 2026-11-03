import { empty, h, heading, tag } from "../ui.js";
/** @typedef {import("../types.js").Week} Week */
/** @typedef {import("../types.js").Visit} Visit */
import { record } from "./visit.js";
/** @param {import("../types.js").ScreenContext} context */
export function minePage(context) {
  const { state } = context;
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
              mine.map((d) => record(d, false, context)),
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
              open.map((d) => record(d, true, context)),
            )
          : empty(
              "All settled here.",
              "Open disputes you can settle will show up here.",
            ),
      ),
    ),
  );
}
