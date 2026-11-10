import { h, tag } from "../ui.js";
import { weekRange } from "./league-copy.js";
/** @typedef {import("../types.js").Week} Week */
/** @param {Week} data */
export function range(data) {
  return weekRange(data.week.start, data.week.end, data.week.timezone);
}
/** @param {Week} data @param {string|number|undefined} viewerId */
export function standings(data, viewerId) {
  const rows = [...data.standings].sort(
    (a, b) => b.points - a.points || a.name.localeCompare(b.name),
  );
  const scorers = rows.filter((r) => r.points > 0),
    zero = rows
      .filter((r) => r.points === 0)
      .map((r) => r.name)
      .sort((a, b) => a.localeCompare(b));
  return h(
    "section",
    { class: "standings group" },
    h("h2", { class: "group-title" }, "Points"),
    h(
      "div",
      { class: "group-rows" },
      scorers.length
        ? h(
            "ol",
            { class: "rankings" },
            scorers.map((r, i) =>
              h(
                "li",
                { class: i === 0 ? "first-place" : "" },
                h("span", { class: "rank", "aria-label": `Rank ${i + 1}` }, i + 1),
                h(
                  "div",
                  { class: "rank-name" },
                  h("span", {}, r.name),
                  String(r.resident_id) === String(viewerId) ? tag("You") : null,
                  h(
                    "span",
                    { class: "dish-count" },
                    `${r.dishes} ${r.dishes === 1 ? "dish" : "dishes"}`,
                  ),
                ),
                h("strong", { class: "points" }, r.points),
              ),
            ),
          )
        : h(
            "p",
            { class: "no-points" },
            data.week.closed
              ? "No one got any points this week."
              : "No one has points yet. They show up once someone names a dish.",
          ),
      scorers.length && zero.length
        ? h(
            "p",
            { class: "zero-standings" },
            h("span", {}, "No points"),
            h("span", {}, zero.join(", ")),
          )
        : null,
    ),
    data.rules.enabled
      ? h(
          "p",
          { class: "group-note" },
          data.week.closed
            ? "Most points got kitchen duty the next week."
            : "Fewer is better. Most points gets kitchen duty next week.",
        )
      : null,
  );
}
