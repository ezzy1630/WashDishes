import { pickCopy, shiftDay, weekRange } from "./league-copy.js";
import { date, group, h, heading, row } from "../ui.js";
/** @typedef {import("../types.js").Week} Week */
import { range, standings } from "./standings.js";
/** @param {import("../types.js").ScreenContext} context */
export function pastPage(context) {
  const { state, href, navigate } = context;
  const data = /** @type {Week} */ (state.data);
  if (state.selected) {
    const pick = pickCopy(data);
    return h(
      "div",
      { class: "past-detail" },
      heading(range(data), data.week.closed ? "Final result. Names and points can’t change." : "Still going. Only named dishes count."),
      h(
        "section",
        { class: "status-card" },
        h("div", { class: "pick" }, h("h2", {}, pick.title), h("p", { class: "past-pick" }, pick.detail)),
      ),
      standings(data, state.me?.resident.id),
    );
  }
  const today = data.week.start;
  const choices = [...data.weeks]
    .sort((a, b) => b.start.localeCompare(a.start))
    .map((w) =>
      row({
        label: weekRange(w.start, shiftDay(w.start, 7), data.week.timezone),
        detail: w.pick_name
          ? `${w.pick_name} ${w.closed ? "was picked" : "is on track"}`
          : w.closed
            ? "No one was picked"
            : "Still going",
        value: w.start === today && !w.closed ? "This week" : "",
        href: `${href("/past")}${href("/past").includes("?") ? "&" : "?"}start=${encodeURIComponent(w.start)}`,
        run: (e) => {
          e.preventDefault();
          navigate(`/past?start=${encodeURIComponent(w.start)}`);
        },
      }),
    );
  return h(
    "div",
    { class: "past-page" },
    heading("Past weeks", `Started ${date(data.start_week, data.week.timezone)}.`),
    group("", choices),
  );
}
