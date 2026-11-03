import { h, heading, icon } from "../ui.js";
import { dutyCopy, weekSubtitle } from "./league-copy.js";
/** @typedef {import("../types.js").Week} Week */
import { standings } from "./standings.js";
/** @param {import("../types.js").ScreenContext} context */
export function weekPage(context) {
  const { state, href, navigate } = context;
  const data = /** @type {Week} */ (state.data),
    duty = data.on_duty;
  const copy = dutyCopy(data);
  return h(
    "div",
    {},
    heading("This week", weekSubtitle(data)),
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
          h("div", {}, h("h2", {}, copy.title), h("p", {}, copy.explanation)),
        ),
        h(
          "section",
          { class: "pick" },
          h(
            "h2",
            {},
            !data.rules.enabled
              ? "No weekly pick while duty is paused."
              : data.pick.name
                ? `${data.pick.name} ${data.week.closed ? "is picked" : "is in line"} for next week.`
                : data.week.closed
                  ? "No one picked this week."
                  : "No clear pick yet.",
          ),
          h("p", {}, data.pick.reason),
          data.rules.enabled && !data.pick.name && duty
            ? h(
                "p",
                { class: "help" },
                `${duty.name} stays on duty if there’s no clear pick.`,
              )
            : null,
        ),
        standings(data, state.me?.resident.id),
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
