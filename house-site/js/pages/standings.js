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
  const top = rows[0],
    pick = data.pick;
  const threshold = Math.max(pick.needed_points, pick.needed_lead);
  const scale = Math.max(data.rules.min_points, top?.points || 0, 1);
  const gate = (
    /** @type {string} */ label,
    /** @type {number} */ current,
    /** @type {number} */ target,
    /** @type {number} */ remaining,
    /** @type {boolean} */ isLead = false,
  ) =>
    h(
      "div",
      { class: "gate" },
      h(
        "div",
        { class: "gate-label" },
        h("span", {}, label),
        h(
          "strong",
          {},
          isLead
            ? `Leads by ${current} · needs ${target}`
            : `${current} pts · needs ${target}`,
        ),
      ),
      h("meter", {
        min: 0,
        max: Math.max(target, current, 1),
        value: current,
        "aria-label": `${label}: ${current}, needs ${target}`,
      }),
      h(
        "span",
        { class: remaining === 0 ? "gate-met" : "help" },
        remaining === 0 ? "Met" : `${remaining} to go`,
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
      rows
        .filter((r) => r.points > 0)
        .map((r, i) => {
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
                String(r.resident_id) === String(viewerId) ? tag("You") : null,
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
    rows.some((r) => r.points === 0)
      ? h(
          "p",
          { class: "zero-standings" },
          h(
            "strong",
            {},
            `${rows.some((r) => r.points > 0) ? "Everyone else" : "Everyone"} 0`,
          ),
          " — ",
          rows
            .filter((r) => r.points === 0)
            .map((r) => r.name)
            .sort((a, b) => a.localeCompare(b))
            .join(", "),
        )
      : null,
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
          top?.points
            ? h(
                "div",
                { class: "gates" },
                gate(
                  `${top.name}’s points`,
                  pick.points,
                  data.rules.min_points,
                  pick.needed_points,
                ),
                gate(
                  "Lead over second",
                  pick.lead,
                  pick.lead === 0 ? pick.needed_lead : data.rules.min_lead,
                  pick.needed_lead,
                  true,
                ),
              )
            : null,
          pick.state !== "pick" && threshold > 0 && top?.points
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
