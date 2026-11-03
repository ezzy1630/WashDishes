import {
  button,
  date,
  empty,
  h,
  heading,
  imageURL,
  mediaSize,
  photo,
  tag,
} from "../ui.js";
/** @typedef {import("../types.js").Week} Week */
/** @typedef {import("../types.js").Visit} Visit */
import { verdictText } from "./visit.js";
/** @param {Week} data */
function reviewedVisits(data) {
  const reviewed = data.visits.filter((v) => v.resident || v.verdict);
  return reviewed.length
    ? h(
        "details",
        { class: "reviewed-visits" },
        h("summary", {}, `Reviewed this week (${reviewed.length})`),
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
                `${visit.area} · ${date(visit.at, data.week.timezone, true)}`,
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
              ),
            ),
          ),
        ),
      )
    : null;
}
/** @param {Visit} visit @param {Week} data @param {import("../types.js").ScreenContext["mutate"]} mutate */
function verdictChoices(visit, data, mutate) {
  const choices = h("div", { class: "other-answers" });
  const picker = h("div", { class: "duplicate-picker" });
  const targets = data.visits.filter((v) => v.id !== visit.id);
  const duplicate = button(
    "Same as another",
    () => {
      if (picker.hasChildNodes()) {
        picker.replaceChildren();
        duplicate.setAttribute("aria-expanded", "false");
        return;
      }
      duplicate.setAttribute("aria-expanded", "true");
      picker.replaceChildren(
        h("p", { class: "help" }, "Which visit is this the same as?"),
        ...targets.map((target) => {
          const choice = button(
            "",
            () =>
              void mutate(
                visit,
                "verdict",
                { verdict: "duplicate", of_visit_id: target.id },
                "Marked as a duplicate. On to the next visit.",
              ),
            "duplicate-choice",
          );
          const thumbnail = imageURL(target.photo_url || target.filmstrip_url);
          if (thumbnail) {
            const img = h("img", {
              src: thumbnail,
              alt: "",
              width: 80,
              height: 60,
              loading: "eager",
              decoding: "sync",
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
                `${target.area} · ${date(target.at, data.week.timezone, true)}`,
              ),
              target.resident
                ? h(
                    "span",
                    { class: "help" },
                    `Named as ${target.resident.name}`,
                  )
                : null,
            ),
          );
          return choice;
        }),
        ...(!targets.length
          ? [
              h(
                "p",
                { class: "help" },
                "There isn’t another visit to link this to.",
              ),
            ]
          : []),
      );
      picker.querySelector("button")?.focus();
    },
    "verdict-button",
  );
  duplicate.setAttribute("aria-expanded", "false");
  choices.append(
    button(
      "Not a dish",
      () =>
        void mutate(
          visit,
          "verdict",
          { verdict: "not_dish" },
          "Marked not a dish. On to the next visit.",
        ),
      "verdict-button",
    ),
    button(
      "It’s clean",
      () =>
        void mutate(
          visit,
          "verdict",
          { verdict: "clean" },
          "Marked clean. On to the next visit.",
        ),
      "verdict-button",
    ),
    duplicate,
    button(
      "Can’t tell",
      () =>
        void mutate(
          visit,
          "verdict",
          { verdict: "cant_tell" },
          "Skipped. It stays unnamed.",
        ),
      "verdict-button",
    ),
  );
  return h(
    "div",
    { class: "verdict-answers" },
    h("p", { class: "help" }, "Something else?"),
    choices,
    picker,
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
      (!v.verdict || v.verdict.kind === "cant_tell"),
  );
  const remaining = unreviewed.filter((v) => !state.skipped.has(v.id)),
    visit = remaining[0];
  const intro = `${data.unreviewed_count} to review. A dish only counts once someone names it.`;
  const reviewed = reviewedVisits(data);
  if (!visit)
    return h(
      "div",
      {},
      heading("Name the dishes", intro),
      empty(
        state.skipped.size ? "That’s the round." : "You’re all caught up.",
        state.skipped.size
          ? "The visits you couldn’t tell stay unnamed. That’s better than a guess."
          : "There’s nothing waiting for you to review right now.",
      ),
      state.skipped.size
        ? button(
            "Look at skipped visits",
            () => {
              state.skipped.clear();
              render();
              focusHeading();
            },
            "secondary",
          )
        : null,
      reviewed,
    );
  const people = [...data.standings].sort((a, b) =>
    a.name.localeCompare(b.name),
  );
  return h(
    "div",
    {},
    heading("Name the dishes", intro),
    h(
      "article",
      { class: "naming", "data-dish-id": visit.id },
      h(
        "div",
        { class: "evidence" },
        h(
          "div",
          { class: "section-heading" },
          h("h2", {}, visit.title),
          tag(
            `${remaining.length} ${remaining.length === 1 ? "visit" : "visits"} left`,
          ),
        ),
        h(
          "p",
          { class: "help visit-metadata" },
          `${visit.area} · ${date(visit.at, data.week.timezone, true)} · ${visit.points} potential ${visit.points === 1 ? "point" : "points"}`,
        ),
        photo(
          visit.filmstrip_url,
          `${visit.title} arrival photos`,
          true,
          false,
          mediaSize(visit),
        ),
        h(
          "div",
          { class: "later-photo" },
          h("h3", {}, "The later photo"),
          photo(
            visit.photo_url,
            `${visit.title}, later`,
            false,
            false,
            mediaSize(visit, false),
          ),
        ),
      ),
      h(
        "section",
        { class: "name-panel" },
        h("h2", { class: "person-question" }, "Whose dishes are these?"),
        h(
          "p",
          { class: "help" },
          visit.arrival_seen
            ? "Name the person who used or left them. Carrying dishes doesn’t make them yours."
            : "Arrival not recorded — only name someone if you know",
        ),
        visit.guess
          ? h(
              "div",
              { class: "camera-guess" },
              button(
                `Camera thinks ${visit.guess.name} — tap to confirm`,
                () =>
                  void mutate(
                    visit,
                    "name",
                    { resident_id: visit.guess?.resident_id },
                    "Confirmed. On to the next visit.",
                  ),
                "guess-choice",
              ),
              h("p", { class: "help" }, visit.guess.basis),
              h(
                "p",
                { class: "help" },
                "A guess only counts once someone confirms it.",
              ),
            )
          : null,
        h(
          "div",
          { class: "name-chips" },
          people.map((p) =>
            button(
              p.name,
              () =>
                void mutate(
                  visit,
                  "name",
                  { resident_id: p.resident_id },
                  "Named. On to the next visit.",
                ),
              `chip ${String(p.resident_id) === String(visit.guess?.resident_id) ? "camera-chip" : ""}`,
            ),
          ),
        ),
        verdictChoices(visit, data, mutate),
        h("p", { class: "action-error", role: "alert" }),
      ),
    ),
    reviewed,
  );
}
