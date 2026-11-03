import { learningPrompt } from "./help.js";
import { h, heading, icon } from "../ui.js";
import {
  dutyCopy,
  weekSubtitle,
  pickDetail,
  personalCopy,
} from "./league-copy.js";
/** @typedef {import("../types.js").Week} Week */
import { standings } from "./standings.js";
/** @param {import("../types.js").ScreenContext} context */
export function weekPage(context) {
  const { state, href, navigate } = context;
  const data = /** @type {Week} */ (state.data),
    duty = data.on_duty;
  const copy = dutyCopy(data);
  const personal = personalCopy(data, state.me?.resident.id);
  return h(
    "div",
    {},
    heading("This week", weekSubtitle(data)),
    learningPrompt(context),
    h(
      "section",
      { class: "personal-status", "aria-label": "Your week" },
      h("div", {}, h("h2", {}, personal.title), h("p", {}, personal.detail)),
      h(
        "a",
        {
          class: "personal-link",
          href: href("/mine"),
          onclick: (/** @type {MouseEvent} */ e) => {
            e.preventDefault();
            navigate("/mine");
          },
        },
        "Check my dishes",
        icon("arrow"),
      ),
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
          h("div", {}, h("h2", {}, copy.title), h("p", {}, copy.explanation)),
        ),
        h(
          "section",
          { class: "pick" },
          h(
            "h2",
            {},
            data.pick.state === "disabled"
              ? "No weekly pick while duty is paused."
              : data.pick.state === "pick"
                ? `${data.pick.name} ${data.week.closed ? "is picked" : "is in line"} for next week.`
                : data.week.closed
                  ? "No one picked this week."
                  : "No clear pick yet.",
          ),
          h("p", {}, pickDetail(data)),
          data.pick.state !== "disabled" && data.pick.state !== "pick" && duty
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
          "div",
          { class: "review-cta" },
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
              data.week.closed
                ? "Week closed"
                : data.clear_count
                  ? `${data.clear_count} clear ${data.clear_count === 1 ? "one" : "ones"} to name`
                  : "You’re caught up",
            ),
            h("p", {}, "Unnamed visits simply don’t count."),
            h(
              "span",
              { class: "cta-action primary" },
              !data.week.closed && data.clear_count
                ? "Review the visits"
                : "See reviewed visits",
              icon("arrow"),
            ),
          ),
          !data.week.closed && data.weak_count
            ? h(
                "a",
                {
                  class: "weak-evidence-link",
                  href: href("/name"),
                  onclick: (/** @type {MouseEvent} */ e) => {
                    e.preventDefault();
                    navigate("/name");
                    state.showWeak = true;
                    context.render();
                  },
                },
                `+${data.weak_count} with weak evidence`,
              )
            : null,
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
            "Wrong name? Only the named person can dispute it in My dishes. Points pause until a third housemate settles it. The namer and named person sit it out.",
          ),
        ),
      ),
    ),
  );
}
