import { pickDetail, shiftDay, weekRange } from "./league-copy.js";
import { mock } from "../auth.js";
import { button, date, h, heading, icon, tag } from "../ui.js";
/** @typedef {import("../types.js").Week} Week */
import { range, standings } from "./standings.js";
/** @param {import("../types.js").ScreenContext} context */
export function pastPage(context) {
  const { state, href, render, refresh } = context;
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
          h(
            "strong",
            {},
            weekRange(w.start, shiftDay(w.start, 7), data.week.timezone),
          ),
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
    heading(
      "Past weeks",
      `League started ${date(data.start_week, data.week.timezone)}.`,
    ),
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
          data.pick.state === "pick"
            ? `${data.pick.name} ${data.week.closed ? "was picked." : "is in line."}`
            : "No one picked.",
        ),
        h("p", { class: "help" }, pickDetail(data)),
        h(
          "p",
          { class: "help past-freeze" },
          data.week.closed
            ? "Final result. Names and points are frozen."
            : "Still in progress. Only named visits count.",
        ),
        standings(data, state.me?.resident.id),
      ),
    ),
  );
}
