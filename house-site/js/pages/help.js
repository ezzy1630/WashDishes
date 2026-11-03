import { learningRequest } from "../api.js";
import { announce, button, fill, h, heading } from "../ui.js";

const sessions = new WeakMap();
const prompts = {
  present: "Is this a dish?",
  kind: "What is it?",
  missed: "Did the camera miss a dish?",
  same: "Is this the same dish?",
  identity: "Is this you?",
};
const choices = {
  present: [
    ["yes", "Yes"],
    ["no", "No"],
  ],
  kind: [
    ["plate", "Plate"],
    ["bowl", "Bowl"],
    ["cup", "Cup"],
    ["pan", "Pan"],
    ["pot", "Pot"],
    ["utensil", "Utensil"],
    ["not_dish", "Not a dish"],
  ],
  same: [
    ["same", "Same dish"],
    ["different", "Different dishes"],
  ],
  identity: [
    ["me", "That’s me"],
    ["not_me", "That’s not me"],
  ],
};

export function helpPage(context) {
  let session = sessions.get(context);
  if (session && session.actor !== context.state.me?.resident.id) {
    sessions.delete(context);
    session = null;
  }
  if (!session) {
    session = {
      actor: context.state.me?.resident.id,
      data: null,
      error: "",
      loading: false,
      pending: false,
      receipt: null,
      box: null,
    };
    sessions.set(context, session);
  }
  const host = h("div", { class: "help-camera" });
  const title = heading("Help the camera", "Teach it what you can see.");
  const content = h("div", { class: "learning-body" });
  host.append(title, content);
  const paint = () => {
    if (!host.isConnected) return;
    const data = session.data,
      card = data?.questions[0];
    const progress = h(
      "section",
      { class: "learning-progress", "aria-label": "Your contribution" },
      h(
        "p",
        {},
        data
          ? `You’ve taught it ${data.personal_labels} ${data.personal_labels === 1 ? "thing" : "things"}.`
          : "Your answers stay in the house.",
      ),
      h(
        "p",
        { class: "help" },
        data?.last_learned
          ? `Last learned: ${new Date(data.last_learned).toLocaleDateString(undefined, { month: "short", day: "numeric" })}`
          : "Learning starts when there are enough independent answers.",
      ),
    );
    const metrics = data?.metrics?.find(m => Number.isFinite(m.before_accuracy) && Number.isFinite(m.after_accuracy));
    if (metrics)
      progress.append(
        h(
          "p",
          { class: "help" },
          `${metrics.model === "probe" ? "Dish recognition" : metrics.model === "identity" ? "Person recognition" : "Dish detection"}: ${Math.round(metrics.before_accuracy * 100)}% → ${Math.round(metrics.after_accuracy * 100)}% on reserved checks.`,
        ),
      );
    const receipt = h(
      "div",
      { class: "learning-receipt", role: "status" },
      session.receipt
        ? [
            h(
              "span",
              {},
              session.receipt.state === "trusted"
                ? "Saved as teaching truth."
                : session.receipt.state === "conflict"
                  ? "Saved. The operator will check the disagreement."
                  : "Saved. Another housemate will check it.",
            ),
            button("Undo", () => void undo(), "quiet"),
          ]
        : h("span", {}, "Independent checks keep the league fair."),
    );
    let body;
    if (session.loading && !data)
      body = h(
        "section",
        { class: "learning-card learning-message", "aria-busy": "true" },
        h("h2", {}, "Finding useful questions…"),
        h("p", {}, "One clear question at a time."),
      );
    else if (session.error && !card)
      body = h(
        "section",
        { class: "learning-card learning-message" },
        h("h2", {}, "Couldn’t load your questions"),
        h("p", {}, session.error),
        button("Try again", () => void load(), "primary"),
      );
    else if (!card)
      body = h(
        "section",
        { class: "learning-card learning-message" },
        h("h2", {}, "That’s all for now."),
        h(
          "p",
          {},
          "Thanks for helping. We’ll save the next useful questions for another visit.",
        ),
        h(
          "p",
          { class: "help" },
          "No guessing needed. Your answers never change anyone’s points.",
        ),
      );
    else {
      const image = h("img", {
        src: card.image_url,
        alt:
          card.task === "identity"
            ? "Saved person crop to identify"
            : "Kitchen frame to check",
        draggable: "false",
      });
      image.addEventListener("error", () => {
        card.imageMissing = true;
        image.hidden = true;
        body
          ?.querySelectorAll(".learning-choice,.primary,select,input")
          .forEach((el) => (el.disabled = true));
        photo.replaceChildren(
          h(
            "p",
            { class: "learning-photo-missing" },
            "This photo is no longer available. Skip to the next question.",
          ),
        );
      });
      const overlay = h("canvas", {
        "aria-hidden": "true",
        class: "learning-overlay",
      });
      const photo = h(
        "div",
        {
          class: `learning-photo ${card.task === "missed" ? "learning-draw" : ""}`,
        },
        image,
        overlay,
      );
      const bounds = () => {
        const r = photo.getBoundingClientRect();
        const scale = Math.min(
          r.width / (image.naturalWidth || r.width),
          r.height / (image.naturalHeight || r.height),
        );
        const w = (image.naturalWidth || r.width) * scale,
          h = (image.naturalHeight || r.height) * scale;
        return {
          left: r.left + (r.width - w) / 2,
          top: r.top + (r.height - h) / 2,
          width: w,
          height: h,
          dx: (r.width - w) / 2,
          dy: (r.height - h) / 2,
        };
      };
      const draw = () => {
        const rect = photo.getBoundingClientRect();
        overlay.width = Math.round(rect.width * 2);
        overlay.height = Math.round(rect.height * 2);
        const ctx = overlay.getContext("2d"),
          box = session.box || card.box,
          boundsRect = bounds();
        if (ctx && box) {
          ctx.strokeStyle = "#ffd400";
          ctx.lineWidth = 5;
          ctx.strokeRect(
            (boundsRect.dx + box[0] * boundsRect.width) * 2,
            (boundsRect.dy + box[1] * boundsRect.height) * 2,
            box[2] * boundsRect.width * 2,
            box[3] * boundsRect.height * 2,
          );
        }
      };
      image.addEventListener("load", draw);
      let start = null;
      if (card.task === "missed") {
        photo.tabIndex = 0;
        photo.setAttribute("role", "group");
        photo.setAttribute(
          "aria-label",
          "Draw around the missed dish. Keyboard users can use the coordinate fields below.",
        );
        const point = (e) => {
          const r = bounds();
          return [
            Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)),
            Math.max(0, Math.min(1, (e.clientY - r.top) / r.height)),
          ];
        };
        photo.addEventListener("pointerdown", (e) => {
          start = point(e);
          photo.setPointerCapture(e.pointerId);
        });
        photo.addEventListener("pointermove", (e) => {
          if (!start) return;
          const end = point(e);
          session.box = [
            Math.min(start[0], end[0]),
            Math.min(start[1], end[1]),
            Math.abs(end[0] - start[0]),
            Math.abs(end[1] - start[1]),
          ];
          draw();
        });
        photo.addEventListener("pointerup", (e) => {
          if (!start) return;
          const end = point(e);
          if (
            Math.abs(end[0] - start[0]) < 0.01 &&
            Math.abs(end[1] - start[1]) < 0.01
          )
            session.box = [
              Math.min(0.85, Math.max(0, end[0] - 0.075)),
              Math.min(0.85, Math.max(0, end[1] - 0.075)),
              0.15,
              0.15,
            ];
          start = null;
          draw();
        });
      } else {
        let x = null;
        photo.addEventListener("pointerdown", (e) => {
          x = e.clientX;
        });
        photo.addEventListener("pointerup", (e) => {
          if (x !== null && Math.abs(e.clientX - x) > 100) void send("unsure");
          x = null;
        });
      }
      const actions = h("div", { class: "learning-actions" });
      const select = h(
        "select",
        { "aria-label": "Kind of missed dish" },
        ...choices.kind
          .slice(0, -1)
          .map(([value, label]) => h("option", { value }, label)),
      );
      if (card.task === "missed") {
        const coords = h(
          "details",
          { class: "learning-coordinates" },
          h("summary", {}, "Adjust box with keyboard"),
        );
        const fields = h("div", { class: "learning-box-fields" });
        ["Left", "Top", "Width", "Height"].forEach((label, i) => {
          const input = h("input", {
            type: "number",
            min: 0,
            max: 100,
            value: i < 2 ? 25 : 15,
            step: 1,
            "aria-label": `${label} percent`,
          });
          input.addEventListener("input", () => {
            session.box ||= [0.25, 0.25, 0.15, 0.15];
            session.box[i] = Number(input.value) / 100;
            draw();
          });
          fields.append(h("label", {}, label, input));
        });
        coords.append(fields);
        actions.append(
          select,
          button(
            "Save dish box",
            () =>
              session.box
                ? void send("box", { box: session.box, kind: select.value })
                : announce("Tap a dish or drag a box around it first."),
            "primary",
          ),
          coords,
        );
      } else
        for (const [answer, label] of choices[card.task] || [])
          actions.append(
            button(label, () => void send(answer), "learning-choice"),
          );
      actions.append(
        button("Not sure · Skip", () => void send("unsure"), "quiet"),
      );
      const media = card.other_image_url
        ? h(
            "div",
            { class: "learning-pair" },
            photo,
            h(
              "div",
              { class: "learning-photo" },
              h("img", {
                src: card.other_image_url,
                alt: "Second dish to compare",
              }),
            ),
          )
        : photo;
      body = h(
        "section",
        { class: "learning-card", "aria-busy": String(session.pending) },
        h(
          "div",
          { class: "learning-question" },
          h("h2", { tabindex: "-1" }, prompts[card.task]),
          h(
            "p",
            { class: "help" },
            card.task === "missed"
              ? "Tap to place a box, or drag around the whole dish."
              : `${card.reason} · Only answer what you can see.`,
          ),
        ),
        media,
        actions,
      );
      body.addEventListener("keydown", (e) => {
        if (
          e.target instanceof HTMLInputElement ||
          e.target instanceof HTMLSelectElement
        )
          return;
        if (e.key.toLowerCase() === "s") {
          e.preventDefault();
          void send("unsure");
        }
        const index = Number(e.key) - 1;
        const option = choices[card.task]?.[index];
        if (option) {
          e.preventDefault();
          void send(option[0]);
        }
      });
      requestAnimationFrame(draw);
    }
    if (session.error && card)
      body.append(
        h("p", { class: "learning-error", role: "alert" }, session.error),
      );
    body
      .querySelectorAll("button,select,input")
      .forEach(
        (el) =>
          (el.disabled =
            session.pending ||
            (card?.imageMissing && !el.classList.contains("quiet"))),
      );
    fill(
      content,
      progress,
      receipt,
      body,
      h(
        "p",
        { class: "learning-fairness" },
        "You won’t be asked to judge visits that could affect your points. Conflicting answers go to a person, not a model.",
      ),
    );
  };
  async function load() {
    if (session.loading) return;
    session.loading = true;
    session.error = "";
    paint();
    try {
      session.data = await learningRequest("/learning");
    } catch (e) {
      session.error = e.message || "Reconnect to the kitchen and try again.";
    } finally {
      session.loading = false;
      paint();
    }
  }
  async function send(answer, value = null) {
    if (
      session.pending ||
      !session.data?.questions.length ||
      (session.data.questions[0].imageMissing && answer !== "unsure")
    )
      return;
    session.pending = true;
    session.error = "";
    const card = session.data.questions.shift();
    session.box = null;
    session.receipt = null;
    paint();
    try {
      const saved = await learningRequest(`/learning/${card.id}/answer`, {
        answer,
        value,
      });
      session.receipt = { ...saved, card };
      if (answer !== "unsure") session.data.personal_labels++;
      session.box = null;
      announce("Answer saved.");
    } catch (e) {
      session.data.questions.unshift(card);
      session.error = e.message || "That didn’t save. Try again.";
    } finally {
      session.pending = false;
      paint();
      content.querySelector("h2")?.focus({ preventScroll: true });
      if (!session.data?.questions.length && !session.error) void load();
    }
  }
  async function undo() {
    if (session.pending || !session.receipt) return;
    session.pending = true;
    paint();
    try {
      await learningRequest(`/learning/labels/${session.receipt.id}/undo`, {});
      session.data.questions.unshift(session.receipt.card);
      session.receipt = null;
      session.box = null;
      void load();
      announce("Answer undone.");
    } catch (e) {
      session.error = e.message || "Couldn’t undo. Try again.";
    } finally {
      session.pending = false;
      paint();
    }
  }
  requestAnimationFrame(() => {
    paint();
    if (!session.data && !session.loading) void load();
  });
  return host;
}

export function learningPrompt(context) {
  const component = h("div", { class: "learning-prompt", hidden: true });
  void learningRequest("/learning")
    .then((data) => {
      if (!component.isConnected || !data.questions.length) return;
      component.hidden = false;
      component.append(
        h("p", {}, "A fresh pair of eyes would help."),
        h(
          "a",
          {
            href: context.href("/help"),
            onclick: (e) => {
              e.preventDefault();
              context.navigate("/help");
            },
          },
          "Help the camera",
        ),
      );
    })
    .catch(() => {});
  return component;
}

export function correctionLink(context, visit) {
  return button(
    "Box was wrong? Fix it",
    async () => {
      try {
        const result = await learningRequest(
          `/visits/${encodeURIComponent(visit.id)}/feedback`,
          { task: "missed" },
        );
        if (result.independent_review)
          announce("Flagged. Another housemate will check this visit.");
        else { sessions.delete(context); context.navigate("/help"); }
      } catch (e) {
        announce(e.message || "Couldn’t flag this box.");
      }
    },
    "quiet",
  );
}
