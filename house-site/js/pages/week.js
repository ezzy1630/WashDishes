import { learningRow } from "./help.js";
import { group, h, heading, icon, imageURL, openSheet, row } from "../ui.js";
import { closeTime, dutyCopy, pickCopy, weekSubtitle, personalCopy } from "./league-copy.js";
/** @typedef {import("../types.js").Week} Week */
import { standings } from "./standings.js";

/** The next dish waiting, with its own photo, so the ask is concrete. @param {import("../types.js").ScreenContext} context */
function reviewCard(context) {
  const { state, link, navigate } = context;
  const data = /** @type {Week} */ (state.data);
  if (data.week.closed)
    return group("", row({ label: "See this week’s dishes", href: context.href("/name"), run: (e) => { e.preventDefault(); navigate("/name"); } }));
  const next = data.visits.find(
    (v) => !v.resident && !v.verdict && v.can_name && v.evidence !== "weak",
  );
  const thumb = imageURL(next?.photo_url || next?.filmstrip_url || null);
  if (!data.clear_count)
    return h(
      "section",
      { class: "review-done" },
      h("h2", {}, "You’re caught up"),
      h("p", {}, "No dishes need a name right now."),
      data.weak_count
        ? link("/name", { class: "weak-evidence-link" }, `${data.weak_count} harder to tell`)
        : null,
    );
  return link(
    "/name",
    { class: `name-cta${thumb ? " has-photo" : ""}` },
    thumb
      ? h("img", { class: "cta-photo", src: thumb, alt: "", loading: "lazy", referrerpolicy: "no-referrer" })
      : null,
    h(
      "span",
      { class: "cta-text" },
      h(
        "span",
        { class: "cta-title", role: "heading", "aria-level": "2" },
        `${data.clear_count} ${data.clear_count === 1 ? "dish needs" : "dishes need"} a name`,
      ),
      h("span", { class: "cta-detail" }, "Know whose they are? It takes a tap."),
    ),
    h("span", { class: "cta-chevron", "aria-hidden": "true" }, icon("chevron")),
  );
}
/** @param {Week} data */
export function howItWorks(data) {
  const w = data.rules.weights;
  return [
    h("ol", { class: "how-steps" },
      h("li", {}, h("strong", {}, "The camera spots dishes left out."), " It takes photos and asks whose they are. It doesn’t decide on its own."),
      h("li", {}, h("strong", {}, "A housemate names them."), " Only name someone if you know they used the dish. Unnamed dishes don’t count against anyone."),
      h("li", {}, h("strong", {}, "Named dishes add up to points."), ` A pan or pot is ${w.pan === w.pot ? w.pan : `${w.pan} or ${w.pot}`}. Anything else is ${w.default}.`),
      h("li", {}, h("strong", {}, "Most points gets kitchen duty."), ` Next week’s duty goes to whoever has at least ${data.rules.min_points} points and leads by ${data.rules.min_lead}. If it’s closer than that, whoever’s on stays on.`),
    ),
    h("h3", {}, "If your name’s on the wrong dish"),
    h("p", {}, "Go to Me and tap That wasn’t me. Your points for it pause until another housemate settles it. You and the person who named you can’t settle it yourselves."),
    h("h3", {}, "When the week ends"),
    h("p", {}, `Weeks run Monday to Sunday. Naming stays open a little past the end of the week; this one closes ${closeTime(data)}. After that the result is final.`),
  ];
}
/** @param {import("../types.js").ScreenContext} context */
export function weekPage(context) {
  const { state, href, navigate } = context;
  const data = /** @type {Week} */ (state.data),
    viewer = state.me?.resident.id,
    duty = data.on_duty,
    copy = dutyCopy(data),
    pick = pickCopy(data),
    personal = personalCopy(data, viewer);
  const ownDuty =
    data.rules.enabled && duty !== null && String(duty.resident_id) === String(viewer);
  const staysOn =
    data.pick.state !== "disabled" && data.pick.state !== "pick" && duty
      ? `${ownDuty ? "You stay" : `${duty.name} stays`} on if that doesn’t change.`
      : "";
  return h(
    "div",
    { class: "week-page" },
    heading("This week", weekSubtitle(data)),
    h(
      "div",
      { class: "week-layout" },
      h(
        "div",
        { class: "week-main" },
        h(
          "section",
          { class: `status-card${ownDuty ? " personal-duty" : ""}`, "aria-label": "Kitchen duty" },
          h(
            "div",
            { class: "duty" },
            h("h2", {}, ownDuty ? "You’re on kitchen duty" : copy.title),
            h("p", { class: ownDuty ? "duty-reason" : "" }, copy.explanation),
          ),
          h(
            "div",
            { class: "pick" },
            h("h2", {}, pick.title),
            h("p", {}, pick.detail),
            staysOn ? h("p", {}, staysOn) : null,
          ),
        ),
        reviewCard(context),
        standings(data, viewer),
      ),
      h(
        "aside",
        { class: "week-aside" },
        group(
          "",
          row({
            label: "My dishes",
            value: `${personal.points} ${personal.points === 1 ? "point" : "points"}`,
            leading: "person",
            href: href("/mine"),
            run: (e) => {
              e.preventDefault();
              navigate("/mine", "tab");
            },
          }),
          learningRow(context),
          row({
            label: "How it works",
            leading: "info",
            run: () => openSheet("How it works", howItWorks(data)),
          }),
        ),
      ),
    ),
  );
}
