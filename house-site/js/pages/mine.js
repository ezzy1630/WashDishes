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
  const points =
    data.standings.find(
      (p) => String(p.resident_id) === String(state.me?.resident.id),
    )?.points ?? 0;
  return h(
    "div",
    {},
    heading(
      "My dishes",
      `${points} ${points === 1 ? "point" : "points"} count toward your week. Only you can dispute a visit named as yours.`,
    ),
    open.length
      ? h(
          "a",
          { class: "settlement-jump secondary", href: "#settlements" },
          `${open.length} ${open.length === 1 ? "dispute needs" : "disputes need"} a third housemate. Help settle it.`,
        )
      : null,
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
        { class: "settlement-list", id: "settlements" },
        h(
          "div",
          { class: "section-heading" },
          h("h2", {}, "Help settle a dispute"),
          tag(String(open.length)),
        ),
        h(
          "p",
          { class: "help section-intro" },
          "Points are paused. Only settle it if you know; the named person and the person who named them can’t settle it.",
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
