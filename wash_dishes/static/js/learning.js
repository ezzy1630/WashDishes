import { api, h, fill, toast } from "./core.js";

// A self-contained additive panel; Setup owns its placement.
export function learningPanel() {
  const panel = h(
    "section",
    { class: "setup-sec card learning-panel", id: "s-learning" },
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
          e.currentTarget.disabled = true;
          try {
            await fn();
            await load();
          } catch (error) {
            toast(error.message, { error: true });
          } finally {
            e.currentTarget.disabled = false;
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
              `${new Date(run.started_at).toLocaleString()} · ${run.status.replaceAll("_", " ")}`,
            ),
            h(
              "p",
              { class: "tx2" },
              `${run.details.train_count || 0} training · ${run.details.test_count || 0} reserved checks`,
            ),
            ...models.map(([name, m]) =>
              h(
                "p",
                { class: "tx2" },
                `${name}: ${m.status} · ${Number.isFinite(m.before_accuracy) ? Math.round(m.before_accuracy * 100) + "% → " + Math.round(m.after_accuracy * 100) + "% · " : ""}${m.reason || ""}`,
              ),
            ),
          ),
        );
      }
      const trends = h(
        "table",
        { class: "learning-trends" },
        h("caption", {}, "Reserved-check accuracy by run"),
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
                h("td", {}, model),
                h("td", {}, `${Math.round(m.before_accuracy * 100)}%`),
                h("td", {}, `${Math.round(m.after_accuracy * 100)}%`),
              ),
            );
      trends.append(tbody);
      const conflicts = h(
        "div",
        { class: "stack" },
        h("h3", {}, `${data.conflicts.length} label conflicts`),
      );
      for (const q of data.conflicts) {
        const options = [
          ...new Map(q.labels.map((l) => [l.value, l])).values(),
        ];
        const entry = h(
          "div",
          {},
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
          `${data.house_truth} independent teaching examples. Only a measured improvement is adopted.`,
        ),
        runs.children.length
          ? runs
          : h("p", { class: "tx2" }, "No learning runs yet."),
        tbody.children.length ? trends : null,
        conflicts,
        h(
          "div",
          { class: "row-actions" },
          data.models.map((model) =>
            action(`Roll back ${model}`, () =>
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
