import { api, h, fill, toast } from "./core.js";

// Plain words for the operator; raw codes stay in the API.
const MODEL = {
  probe: "Dish check",
  identity: "Who it was",
  detector: "Dish finder",
};
const STATUS = {
  adopted: "improved — now in use",
  rejected: "tried, not better — kept the old one",
  rolled_back: "got worse — went back to the previous one",
  insufficient_truth: "not enough checked answers yet",
  skipped_busy: "skipped — the camera was busy",
  failed: "failed — kept the old one",
  running: "running",
  locked: "another run was in progress",
  already_ran: "already ran tonight",
};
const words = (map, key) => map[key] || String(key).replaceAll("_", " ");
const when = (stamp) =>
  new Date(stamp).toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });

// A self-contained additive panel; Setup owns its placement.
export function learningPanel() {
  const panel = h(
    "section",
    {
      class: "setup-sec card learning-panel",
      id: "s-learning",
      "data-id": "learning",
    },
    h("div", { class: "card-head" }, h("h2", {}, "Learning")),
  );
  const body = h("div", { class: "card-body stack" });
  panel.append(body);
  const action = (label, fn) =>
    h(
      "button",
      {
        class: "btn",
        type: "button",
        onclick: async (e) => {
          const control = e.currentTarget;
          control.disabled = true;
          try {
            await fn();
            await load();
          } catch (error) {
            toast(error.message, { error: true });
          } finally {
            control.disabled = false;
          }
        },
      },
      label,
    );
  async function load() {
    try {
      const data = await api("/api/learning");
      if (!panel.isConnected) return;
      const runs = h("div", { class: "stack" });
      for (const run of data.runs.slice(0, 8)) {
        const models = Object.entries(run.details.models || {});
        runs.append(
          h(
            "div",
            {},
            h(
              "strong",
              {},
              `${when(run.started_at)} · ${words(STATUS, run.status)}`,
            ),
            h(
              "p",
              { class: "tx2" },
              `Learned from ${run.details.train_count || 0} answers · tested on ${run.details.test_count || 0} it never saw`,
            ),
            ...models.map(([name, m]) =>
              h(
                "p",
                { class: "tx2" },
                `${words(MODEL, name)}: ${words(STATUS, m.status)}${Number.isFinite(m.before_accuracy) ? " · " + Math.round(m.before_accuracy * 100) + "% → " + Math.round(m.after_accuracy * 100) + "%" : ""}`,
              ),
            ),
          ),
        );
      }
      const trends = h(
        "table",
        { class: "learning-trends" },
        h("caption", {}, "Accuracy on answers it never trained on"),
        h(
          "thead",
          {},
          h(
            "tr",
            {},
            h("th", {}, "Run"),
            h("th", {}, "Model"),
            h("th", {}, "Before"),
            h("th", {}, "Candidate"),
          ),
        ),
      );
      const tbody = h("tbody");
      for (const run of [...data.runs].reverse())
        for (const [model, m] of Object.entries(run.details.models || {}))
          if (Number.isFinite(m.before_accuracy))
            tbody.append(
              h(
                "tr",
                {},
                h("td", {}, new Date(run.started_at).toLocaleDateString()),
                h("td", {}, words(MODEL, model)),
                h("td", {}, `${Math.round(m.before_accuracy * 100)}%`),
                h("td", {}, `${Math.round(m.after_accuracy * 100)}%`),
              ),
            );
      trends.append(tbody);
      const conflicts = h(
        "div",
        { class: "stack learning-conflicts" },
        h(
          "h3",
          {},
          data.conflicts.length === 1
            ? "1 answer disagreement to settle"
            : `${data.conflicts.length} answer disagreements to settle`,
        ),
      );
      for (const q of data.conflicts) {
        const options = [
          ...new Map(q.labels.map((l) => [l.value, l])).values(),
        ];
        const entry = h(
          "div",
          { class: "learning-conflict" },
          h("p", {}, `${q.task} · teaching question ${q.id}`),
          h(
            "a",
            {
              href: `/api/evidence/${encodeURIComponent(q.evidence_id)}`,
              target: "_blank",
              rel: "noreferrer",
            },
            "Inspect original photo",
          ),
        );
        for (const l of options)
          entry.append(
            action(
              `Use ${l.answer} · ${l.actor_id === null ? "operator" : "resident " + l.actor_id}`,
              () =>
                api(`/api/learning/${q.id}/resolve`, {
                  method: "POST",
                  body: {
                    answer: l.answer,
                    value:
                      JSON.parse(l.value)?.resident_id ?? JSON.parse(l.value),
                  },
                }),
            ),
          );
        conflicts.append(entry);
      }
      fill(
        body,
        h(
          "p",
          { class: "tx2" },
          `${data.house_truth} checked answers so far. A new version is only used if it tests better.`,
        ),
        runs.children.length
          ? runs
          : h("p", { class: "tx2" }, "No learning runs yet."),
        tbody.children.length
          ? h("div", { class: "learning-table" }, trends)
          : null,
        conflicts,
        h(
          "div",
          { class: "row-actions" },
          data.models.map((model) =>
            action(
              `Undo last ${words(MODEL, model).toLowerCase()} update`,
              () =>
                api(`/api/learning/rollback/${model}`, {
                  method: "POST",
                  body: {},
                }),
            ),
          ),
        ),
      );
    } catch (error) {
      fill(
        body,
        h("p", { role: "alert" }, error.message),
        action("Retry", async () => {}),
      );
    }
  }
  requestAnimationFrame(() => void load());
  return panel;
}
