import { correctionLink } from "./help.js";
import {
  button,
  closeSheet,
  empty,
  h,
  heading,
  imageURL,
  mediaSize,
  openSheet,
  photo,
  tag,
  when,
} from "../ui.js";
/** @typedef {import("../types.js").Week} Week */
/** @typedef {import("../types.js").Visit} Visit */
import { verdictText } from "./visit.js";
import { watchFootage } from "./footage.js";

const evidenceLabels = {
  strong: "Clear photos",
  fair: "Decent photos",
  weak: "Hard to see",
};
/** The server's short reasons, said the way a person would. @param {string} reason */
function plainReason(reason) {
  return reason
    .replace("Arrival photos unavailable", "No photos of it arriving")
    .replace("Arrival not recorded", "The camera missed it arriving")
    .replace("No one in the photos", "no one’s in the photos")
    .replace(
      "Looks like a household object; check Not a dish",
      "This might not be a dish at all",
    )
    .replace(" · ", ", ");
}
/** @param {Week} data @param {import('../types.js').ScreenContext} context */
function reviewedVisits(data, context) {
  const reviewed = data.visits.filter((v) => v.resident || v.verdict);
  const history = data.verdicts;
  return reviewed.length + history.length
    ? h(
        "details",
        { class: "reviewed-visits" },
        h(
          "summary",
          {},
          `Reviewed this week (${reviewed.length + history.length})`,
        ),
        h(
          "div",
          { class: "reviewed-list" },
          reviewed.map((visit) =>
            h(
              "article",
              { class: "reviewed-visit" },
              h("h3", {}, visit.title),
              h(
                "p",
                { class: "help" },
                `${visit.area} · ${when(visit.at, data.week.timezone)}`,
              ),
              h(
                "p",
                { class: "review-attribution" },
                verdictText(visit) ||
                  (visit.named_by
                    ? `Named as ${visit.resident?.name} by ${visit.named_by.name}`
                    : `Named as ${visit.resident?.name}`),
              ),
              h(
                "details",
                {},
                h("summary", {}, "See photos"),
                photo(
                  visit.filmstrip_url || visit.photo_url,
                  `${visit.title} reviewed photos`,
                  Boolean(visit.filmstrip_url),
                  true,
                  mediaSize(visit, Boolean(visit.filmstrip_url)),
                ),
                watchFootage(visit, context),
              ),
            ),
          ),
          history.map((entry) =>
            h(
              "article",
              { class: "reviewed-visit" },
              h("h3", {}, entry.title),
              h(
                "p",
                { class: "review-attribution" },
                verdictText({ verdict: entry }),
              ),
            ),
          ),
        ),
      )
    : null;
}
/** Start with nearby duplicates; keep every other original reachable. @param {Visit} visit @param {Week} data @param {import("../types.js").ScreenContext["mutate"]} mutate */
function duplicatePicker(visit, data, mutate) {
  const at = Date.parse(visit.at);
  const targets = data.visits
    .filter((v) => v.id !== visit.id)
    .sort(
      (a, b) =>
        Number(b.area === visit.area) - Number(a.area === visit.area) ||
        Math.abs(Date.parse(a.at) - at) - Math.abs(Date.parse(b.at) - at),
    );
  const choices = h("div", { class: "duplicate-picker" });
  let shown = 0;
  const more = button("Show more dishes", showMore, "secondary");
  function showMore() {
    const next = targets.slice(shown, shown + 20);
    shown += next.length;
    choices.append(
      ...next.map((target) => {
        const choice = button(
          "",
          () => {
            closeSheet();
            void mutate(
              visit,
              "verdict",
              { verdict: "duplicate", of_visit_id: target.id },
              "Marked as a duplicate",
            );
          },
          "duplicate-choice",
        );
        const thumbnail = imageURL(target.photo_url || target.filmstrip_url);
        if (thumbnail) {
          const img = h("img", {
            src: thumbnail,
            alt: "",
            width: 80,
            height: 60,
            loading: "lazy",
            referrerpolicy: "no-referrer",
          });
          img.onerror = () => img.remove();
          choice.append(img);
        }
        choice.append(
          h(
            "span",
            {},
            h("strong", {}, target.title),
            h(
              "span",
              { class: "help" },
              `${target.area} · ${when(target.at, data.week.timezone)}`,
            ),
            target.resident
              ? h("span", { class: "help" }, `Named as ${target.resident.name}`)
              : null,
          ),
        );
        return choice;
      }),
    );
    more.hidden = shown >= targets.length;
    more.textContent = `Show more dishes (${targets.length - shown} left)`;
  }
  showMore();
  openSheet(
    "Which one is it the same as?",
    targets.length
      ? h("div", {}, choices, more)
      : h("p", { class: "help" }, "There isn’t another one to link this to."),
  );
}
/** @param {Visit} visit @param {Week} data @param {import("../types.js").ScreenContext["mutate"]} mutate */
function moreAnswers(visit, data, mutate) {
  /** @param {string} label @param {string} detail @param {()=>void} run */
  const option = (label, detail, run) => {
    const choice = h(
      "button",
      {
        type: "button",
        class: "sheet-option verdict-button",
        "aria-label": label,
        onclick: run,
      },
      h("strong", {}, label),
      h("span", {}, detail),
    );
    return choice;
  };
  /** @param {'not_dish'|'clean'} verdict @param {string} message */
  const answer = (verdict, message) => () => {
    closeSheet();
    void mutate(visit, "verdict", { verdict }, message);
  };
  openSheet(
    "What’s wrong with this one?",
    h(
      "div",
      { class: "sheet-options" },
      option(
        "Not a dish",
        "The camera picked up something else. Removes it for everyone.",
        answer("not_dish", "Marked not a dish"),
      ),
      option(
        "It’s clean",
        "It’s already been washed. Removes it for everyone.",
        answer("clean", "Marked clean"),
      ),
      option("Same as another", "This dish is already on the list.", () => {
        closeSheet();
        duplicatePicker(visit, data, mutate);
      }),
    ),
  );
}
/** @param {import("../types.js").ScreenContext} context */
export function namePage(context) {
  const { state, render, focusHeading, mutate } = context;
  const data = /** @type {Week} */ (state.data);
  const unreviewed = data.visits.filter(
    (v) =>
      !v.resident &&
      v.can_name &&
      !state.pending.has(v.id) &&
      (!v.verdict || v.verdict.kind === "cant_tell"),
  );
  const remaining = unreviewed.filter(
      (v) =>
        !state.skipped.has(v.id) && (state.showWeak || v.evidence !== "weak"),
    ),
    visit = remaining[0];
  const hiddenWeak = unreviewed.filter(
    (v) => v.evidence === "weak" && !state.skipped.has(v.id),
  ).length;
  const weakLink =
    !data.week.closed && (state.showWeak || hiddenWeak)
      ? h(
          "button",
          {
            type: "button",
            class: "weak-evidence-link",
            "aria-expanded": String(state.showWeak),
            onclick: () => {
              state.showWeak = !state.showWeak;
              render();
              focusHeading();
            },
          },
          state.showWeak
            ? "Hide the hard-to-see ones"
            : `Show ${hiddenWeak} that ${hiddenWeak === 1 ? "is" : "are"} hard to see`,
        )
      : null;
  const reviewed = reviewedVisits(data, context);
  if (!visit)
    return h(
      "div",
      { class: "name-done" },
      heading(data.week.closed ? "This week’s dishes" : "Dishes"),
      empty(
        data.week.closed
          ? "This week is final."
          : state.pending.size
            ? "All answered."
            : state.skipped.size
              ? "That’s the round."
              : "You’re all caught up.",
        data.week.closed
          ? "Dishes nobody named don’t count."
          : state.pending.size
            ? "Saving in a moment. You can still undo."
            : state.skipped.size
              ? "The ones you couldn’t tell stay unnamed. That’s better than a guess."
              : hiddenWeak
                ? "What’s left is hard to see. It’s fine to leave those unnamed."
                : "Nothing needs a name right now.",
      ),
      weakLink,
      state.skipped.size
        ? button(
            "Look at the skipped ones again",
            () => {
              state.skipped.clear();
              render();
              focusHeading();
            },
            "secondary",
          )
        : null,
      context.link("/", { class: "primary" }, "Back to this week"),
      reviewed,
    );
  // Open the next photo instantly when this one is answered.
  const next = remaining[1],
    upcoming = imageURL(next?.filmstrip_url || next?.photo_url || null);
  if (upcoming) new Image().src = upcoming;
  const people = [...data.standings].sort((a, b) =>
    a.name.localeCompare(b.name),
  );
  const guess = visit.guess;
  return h(
    "div",
    { class: "name-page" },
    heading("Whose dishes are these?", "Only name someone if you know."),
    h(
      "article",
      { class: "naming", "data-dish-id": visit.id },
      h(
        "div",
        { class: "evidence" },
        h(
          "header",
          { class: "visit-heading" },
          h("h2", {}, visit.title),
          tag(`${remaining.length} left`),
        ),
        h(
          "p",
          { class: "help visit-metadata" },
          `${visit.area} · ${when(visit.at, data.week.timezone)} · ${visit.points} ${visit.points === 1 ? "point" : "points"} · ${evidenceLabels[visit.evidence]}`,
        ),
        photo(
          visit.filmstrip_url || visit.photo_url,
          `${visit.title}: arrival photos`,
          true,
          false,
          mediaSize(visit, Boolean(visit.filmstrip_url)),
        ),
        h(
          "p",
          { class: "help visit-note" },
          visit.reason
            ? `${plainReason(visit.reason)}.`
            : visit.arrival_seen
              ? "Name whoever used them, not whoever moved them."
              : "The camera missed them being put down, so people nearby might not be the owner.",
        ),
      ),
      h(
        "section",
        { class: "name-panel", "aria-label": "Answer" },
        guess
          ? h(
              "details",
              { class: "camera-guess" },
              h("summary", {}, `The camera thinks it’s ${guess.name}`),
              h(
                "p",
                { class: "help" },
                `${guess.basis} It doesn’t count until someone taps a name.`,
              ),
            )
          : null,
        h(
          "div",
          {
            class: "name-chips",
            role: "group",
            "aria-label": "Whose dishes are these?",
          },
          people.map((p) =>
            button(
              p.name,
              () =>
                void mutate(
                  visit,
                  "name",
                  { resident_id: p.resident_id },
                  `Named ${p.name}`,
                ),
              `chip ${String(p.resident_id) === String(guess?.resident_id) ? "camera-chip" : ""}`,
            ),
          ),
        ),
        h(
          "div",
          { class: "other-answers" },
          button(
            "Can’t tell",
            () =>
              void mutate(
                visit,
                "verdict",
                { verdict: "cant_tell" },
                "Skipped",
              ),
            "verdict-button",
          ),
          button(
            "Something’s off",
            () => moreAnswers(visit, data, mutate),
            "verdict-button",
          ),
        ),
        h("p", { class: "action-error", role: "alert" }),
      ),
      h(
        "div",
        { class: "visit-extras" },
        watchFootage(visit, context),
        visit.filmstrip_url && visit.photo_url
          ? h(
              "details",
              { class: "later-photo" },
              h("summary", {}, "See the later photo"),
              photo(
                visit.photo_url,
                `${visit.title}, later`,
                false,
                true,
                mediaSize(visit, false),
              ),
            )
          : null,
        correctionLink(context, visit),
      ),
    ),
    weakLink,
    reviewed,
  );
}
