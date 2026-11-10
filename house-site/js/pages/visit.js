import { button, closeSheet, field, h, openSheet, photo, tag, mediaSize, when } from "../ui.js";
import { watchFootage } from "./footage.js";
/** @typedef {import("../types.js").Week} Week */
/** @typedef {import("../types.js").Visit} Visit */
/** @param {{verdict: Visit["verdict"]}} visit */
export function verdictText(visit) {
  if (!visit.verdict) return "";
  const labels = {
    not_dish: "Marked not a dish",
    clean: "Marked clean",
    duplicate: "Marked same as another",
    cant_tell: "Couldn’t tell",
  };
  return `${labels[visit.verdict.kind]} by ${visit.verdict.by?.name || "Ezzy"}`;
}
/** @param {Visit} dish @param {import("../types.js").ScreenContext} context */
function disputeSheet(dish, context) {
  const note = h("textarea", {
    name: `note-${dish.id}`,
    rows: 3,
    maxlength: 1000,
    required: true,
    placeholder: "e.g. That pot was already there when I came in.",
  });
  const error = h("p", { class: "form-error", role: "alert" });
  const form = h(
    "form",
    {
      class: "dispute-form",
      onsubmit: (/** @type {SubmitEvent} */ e) => {
        e.preventDefault();
        if (!note.value.trim()) {
          error.textContent = "Add a short note before sending.";
          note.focus();
          return;
        }
        const text = note.value.trim();
        closeSheet();
        void context.mutate(
          dish,
          "dispute",
          { note: text },
          "Dispute sent. Another housemate can settle it.",
        );
      },
    },
    field("What happened?", note),
    h(
      "p",
      { class: "help" },
      "Your points for this pause until another housemate checks. You and the person who named you can’t settle it.",
    ),
    error,
    h("button", { type: "submit", class: "primary" }, "Send dispute"),
  );
  openSheet(dish.title, form);
  note.focus();
}
/** @param {Visit} dish @param {boolean} settle @param {import("../types.js").ScreenContext} context */
export function record(dish, settle, context) {
  const { state, mutate } = context;
  const data = /** @type {Week} */ (state.data),
    dispute = dish.dispute;
  const actions = h("div", { class: "record-actions" });
  if (settle && dish.can_settle)
    actions.append(
      h(
        "div",
        { class: "settle-actions" },
        button(
          "It was them",
          () =>
            void mutate(
              dish,
              "settle",
              { decision: "them" },
              "Settled. The house record is updated.",
            ),
          "secondary",
        ),
        button(
          "Not them",
          () =>
            void mutate(
              dish,
              "settle",
              { decision: "not_them" },
              "Settled. The house record is updated.",
            ),
          "secondary",
        ),
      ),
    );
  else if (dish.can_dispute && dispute?.state !== "open")
    actions.append(
      button("That wasn’t me", () => disputeSheet(dish, context), "secondary"),
    );
  const showing = dish.filmstrip_url || dish.photo_url;
  return h(
    "article",
    { class: "dish-record", "data-dish-id": dish.id },
    h(
      "header",
      { class: "record-heading" },
      h(
        "div",
        {},
        h("h3", {}, dish.title, settle && dish.resident ? ` · ${dish.resident.name}` : ""),
        h(
          "p",
          { class: "help" },
          `${dish.area} · ${when(dish.at, data.week.timezone)}`,
        ),
      ),
      tag(
        `${dish.points} ${dish.points === 1 ? "point" : "points"}${dispute?.state === "open" ? " · paused" : ""}`,
        dispute?.state === "open" ? "review" : "",
      ),
    ),
    settle
      ? photo(showing, `${dish.title} evidence`, Boolean(dish.filmstrip_url), false, mediaSize(dish, Boolean(dish.filmstrip_url)))
      : h(
          "details",
          { class: "record-photos" },
          h("summary", {}, "See the photos"),
          photo(showing, `${dish.title} evidence`, Boolean(dish.filmstrip_url), true, mediaSize(dish, Boolean(dish.filmstrip_url))),
        ),
    dish.filmstrip_url && dish.photo_url
      ? h(
          "details",
          { class: "later-details" },
          h("summary", {}, "See the later photo"),
          photo(dish.photo_url, `${dish.title}, later`, false, true, mediaSize(dish, false)),
        )
      : null,
    watchFootage(dish, context),
    h(
      "p",
      { class: "help attribution" },
      dish.named_by ? `Named by ${dish.named_by.name}` : "No housemate on the record.",
    ),
    dish.verdict ? h("p", { class: "review-attribution" }, verdictText(dish)) : null,
    dispute
      ? h(
          "div",
          { class: "dispute-note" },
          tag(
            dispute.state === "open"
              ? "Disputed · open"
              : dispute.state === "upheld"
                ? "Settled · it was them"
                : "Settled · not them",
            "review",
          ),
          h("p", {}, h("strong", {}, `${dispute.by.name}: `), h("q", {}, dispute.note)),
          dispute.settled_by
            ? h("p", { class: "help" }, `Settled by ${dispute.settled_by.name}`)
            : !settle
              ? h("p", { class: "help" }, "These points don’t count until another housemate settles it.")
              : null,
        )
      : null,
    actions,
    h("p", { class: "action-error", role: "alert" }),
  );
}
