import { button, date, field, h, photo, tag, mediaSize } from "../ui.js";
import { watchFootage } from "./footage.js";
/** @typedef {import("../types.js").Week} Week */
/** @typedef {import("../types.js").Visit} Visit */
/** @param {{verdict: Visit["verdict"]}} visit */
export function verdictText(visit) {
  if (!visit.verdict) return "";
  const labels = {
    not_dish: "Marked not a dish",
    clean: "Marked clean",
    duplicate: "Marked same as another visit",
    cant_tell: "Couldn’t tell",
  };
  return `${labels[visit.verdict.kind]} by ${visit.verdict.by?.name || "the operator"}`;
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
        h(
          "p",
          { class: "help settlement-help" },
          "“It was them” restores their points. “Not them” leaves the visit unnamed.",
        ),
      ),
    );
  else if (dish.can_dispute && dispute?.state !== "open")
    actions.append(
      button(
        "That wasn’t me",
        () => {
          const note = h("textarea", {
            name: `note-${dish.id}`,
            rows: 3,
            maxlength: 1000,
            required: true,
            placeholder: "Tell the house what happened.",
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
                void mutate(
                  dish,
                  "dispute",
                  { note: note.value.trim() },
                  "Dispute sent. Another housemate can settle it.",
                );
              },
            },
            field("What happened?", note),
            h(
              "p",
              { class: "help" },
              "Your points for this visit pause while a third housemate checks it. You and the person who named you can’t settle it.",
            ),
            error,
            h(
              "div",
              { class: "form-actions" },
              h("button", { type: "submit", class: "primary" }, "Send dispute"),
              button(
                "Cancel",
                () => {
                  actions.replaceChildren(disputeButton);
                },
                "quiet",
              ),
            ),
          );
          const disputeButton = button(
            "That wasn’t me",
            () => {
              actions.replaceChildren(form);
              note.focus();
            },
            "secondary",
          );
          actions.replaceChildren(form);
          note.focus();
        },
        "secondary",
      ),
    );
  const named = dish.named_by
    ? `Named by ${dish.named_by.name}${dish.named_at ? ` · ${date(dish.named_at, data.week.timezone, true)}` : ""}`
    : "No housemate named on the record.";
  return h(
    "article",
    { class: "dish-record", "data-dish-id": dish.id },
    h(
      "header",
      { class: "record-heading" },
      h(
        "div",
        {},
        h(
          "h3",
          {},
          dish.title,
          settle && dish.resident ? ` · ${dish.resident.name}` : "",
        ),
        h(
          "p",
          { class: "help" },
          `${dish.area} · ${date(dish.at, data.week.timezone, true)}`,
        ),
      ),
      tag(
        `${dish.points} ${dish.points === 1 ? "point" : "points"}${dispute?.state === "open" ? " · paused" : ""}`,
        dispute?.state === "open" ? "review" : "",
      ),
    ),
    photo(
      dish.filmstrip_url || dish.photo_url,
      `${dish.title} evidence`,
      Boolean(dish.filmstrip_url),
      false,
      mediaSize(dish, Boolean(dish.filmstrip_url)),
    ),
    watchFootage(dish, context),
    dish.filmstrip_url && dish.photo_url
      ? h(
          "details",
          { class: "later-details" },
          h("summary", {}, "See the later photo"),
          photo(
            dish.photo_url,
            `${dish.title}, later`,
            false,
            true,
            mediaSize(dish, false),
          ),
        )
      : null,
    !dish.arrival_seen
      ? h(
          "p",
          { class: "help" },
          "Arrival not recorded. People nearby are candidates. Only name someone if you know they used these dishes.",
        )
      : null,
    h("p", { class: "help attribution" }, named),
    dish.verdict
      ? h("p", { class: "review-attribution" }, verdictText(dish))
      : null,
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
          h(
            "p",
            {},
            h("strong", {}, `${dispute.by.name}: `),
            h("q", {}, dispute.note),
          ),
          dispute.settled_by
            ? h("p", { class: "help" }, `Settled by ${dispute.settled_by.name}`)
            : !settle
              ? h(
                  "p",
                  { class: "help" },
                  "These points don’t count while you wait for a third housemate to settle it.",
                )
              : null,
        )
      : null,
    actions,
    h("p", { class: "action-error", role: "alert" }),
  );
}
