import {
  getLearning,
  answerLearning,
  undoLearning,
  feedbackLearning,
} from "../api.js";
import { announce, button, fill, h, heading, notify, row } from "../ui.js";

/** @typedef {import('../types.js').ScreenContext} ScreenContext */
/** @typedef {Awaited<ReturnType<typeof getLearning>>} LearningFeed */
/** @typedef {LearningFeed['questions'][number]} LearningCard */
/** @typedef {import('../learning-contracts.js').LearningBox} LearningBox */
/** @typedef {{actor:string|number|undefined, data:LearningFeed|null, error:string, loading:boolean, pending:boolean, receipt:(Awaited<ReturnType<typeof answerLearning>> & {card:LearningCard})|null, box:LearningBox|null}} LearningSession */
/** @type {WeakMap<ScreenContext, LearningSession>} */
const sessions = new WeakMap();
const prompts = {
  present: "Is this a dish?",
  kind: "What is it?",
  missed: "Did the camera miss a dish?",
  same: "Is this the same dish?",
  identity: "Is this you?",
};
/** @type {Record<import("../learning-contracts.js").LearningTask, [string,string][]>} */
const choices = {
  missed: [],
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

/** @param {ScreenContext} context */
export function helpPage(context) {
  let session = sessions.get(context);
  if (session && session.actor !== context.state.me?.resident.id) {
    sessions.delete(context);
    session = undefined;
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
  const current = session;
  const host = h("div", { class: "help-camera" });
  const title = heading("Help the camera", "Quick questions that teach it to spot dishes better. They never change anyone’s points.");
  const content = h("div", { class: "learning-body" });
  host.append(title, content);
  const paint = () => {
    if (!host.isConnected) return;
    const data = current.data,
      card = data?.questions[0];
    const progress = h(
      "section",
      { class: "learning-progress", "aria-label": "Your contribution" },
      h(
        "p",
        {},
        data
          ? `You’ve answered ${data.personal_labels} ${data.personal_labels === 1 ? "question" : "questions"}.`
          : "Your answers stay in the house.",
      ),
      h(
        "p",
        { class: "help" },
        data?.last_learned
          ? `Last learned: ${new Date(data.last_learned).toLocaleDateString(undefined, { month: "short", day: "numeric" })}`
          : "It learns once a few of you agree on an answer.",
      ),
    );
    const metrics = data?.metrics[0];
    if (metrics)
      progress.append(
        h(
          "p",
          { class: "help" },
          `${metrics.model === "identity" ? "Recognizing people" : "Spotting dishes"} went from ${Math.round(metrics.before_accuracy * 100)}% to ${Math.round(metrics.after_accuracy * 100)}% right in testing.`,
        ),
      );
    const receipt = h(
      "div",
      { class: "learning-receipt", role: "status" },
      current.receipt
        ? [
            h(
              "span",
              {},
              current.receipt.state === "trusted"
                ? "Saved. It’ll learn from that."
                : current.receipt.state === "conflict"
                  ? "Saved. People answered differently, so Ezzy will take a look."
                  : "Saved. Another housemate will check it.",
            ),
            button("Undo", () => void undo(), "quiet"),
          ]
        : null,
    );
    receipt.hidden = !current.receipt;
    /** @type {HTMLElement} */
    let body;
    if (current.loading && !data)
      body = h(
        "section",
        { class: "learning-card learning-message", "aria-busy": "true" },
        h("h2", {}, "Finding a question…"),
      );
    else if (current.error && !card)
      body = h(
        "section",
        { class: "learning-card learning-message" },
        h("h2", {}, "Couldn’t load your questions"),
        h("p", {}, current.error),
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
          "Thanks for helping. There’ll be more next time.",
        ),
        h(
          "p",
          { class: "help" },
          "Your answers never change anyone’s points.",
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
      /** @param {HTMLImageElement} unavailable @param {HTMLElement} target */
      const missingPhoto = (unavailable, target) => {
        card.imageMissing = true;
        unavailable.hidden = true;
        body
          ?.querySelectorAll(".learning-choice,.primary,select,input")
          .forEach((el) => {
            if (
              el instanceof HTMLButtonElement ||
              el instanceof HTMLSelectElement ||
              el instanceof HTMLInputElement
            )
              el.disabled = true;
          });
        target.replaceChildren(
          h(
            "p",
            { class: "learning-photo-missing" },
            "This photo is gone. Skip to the next question.",
          ),
        );
      };
      image.addEventListener("error", () => missingPhoto(image, photo));
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
      /** @type {HTMLInputElement[]} */
      const coordinateInputs = [];
      const draw = () => {
        const drawnBox = current.box;
        if (drawnBox)
          coordinateInputs.forEach((input, i) => {
            input.value = String(Math.round(drawnBox[i] * 100));
          });
        const rect = photo.getBoundingClientRect();
        overlay.width = Math.round(rect.width * 2);
        overlay.height = Math.round(rect.height * 2);
        const ctx = overlay.getContext("2d"),
          box = current.box || card.box,
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
      /** @type {[number,number]|null} */
      let start = null;
      if (card.task === "missed") {
        photo.tabIndex = 0;
        photo.setAttribute("role", "group");
        photo.setAttribute(
          "aria-label",
          "Draw around the missed dish. Keyboard users can use the coordinate fields below.",
        );
        /** @param {PointerEvent} e @returns {[number,number]} */
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
          current.box = [
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
            current.box = [
              Math.min(0.85, Math.max(0, end[0] - 0.075)),
              Math.min(0.85, Math.max(0, end[1] - 0.075)),
              0.15,
              0.15,
            ];
          start = null;
          draw();
        });
      } else {
        /** @type {number|null} */
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
          h("summary", {}, "Adjust the box by number"),
        );
        const fields = h("div", { class: "learning-box-fields" });
        ["Left", "Top", "Width", "Height"].forEach((label, i) => {
          const input = h("input", {
            type: "number",
            min: 0,
            max: 100,
            value: Math.round(
              (current.box?.[i] ?? (i < 2 ? 0.25 : 0.15)) * 100,
            ),
            step: 1,
            "aria-label": `${label} percent`,
          });
          coordinateInputs.push(input);
          input.addEventListener("input", () => {
            current.box ||= [0.25, 0.25, 0.15, 0.15];
            current.box[i] = Number(input.value) / 100;
            draw();
          });
          fields.append(h("label", {}, label, input));
        });
        coords.append(fields);
        actions.append(
          select,
          button(
            "Save",
            () =>
              current.box
                ? void send("box", { box: current.box, kind: select.value })
                : announce("Tap the dish or drag a box around it first."),
            "primary",
          ),
          coords,
        );
      } else
        for (const [answer, label] of choices[card.task])
          actions.append(
            button(label, () => void send(answer), "learning-choice"),
          );
      actions.append(
        button("Not sure, skip", () => void send("unsure"), "quiet"),
      );
      let media = photo;
      if (card.other_image_url) {
        const otherImage = h("img", {
          src: card.other_image_url,
          alt: "Second dish to compare",
        });
        const otherPhoto = h("div", { class: "learning-photo" }, otherImage);
        otherImage.addEventListener("error", () =>
          missingPhoto(otherImage, otherPhoto),
        );
        media = h("div", { class: "learning-pair" }, photo, otherPhoto);
      }
      body = h(
        "section",
        { class: "learning-card", "aria-busy": String(current.pending) },
        h(
          "div",
          { class: "learning-question" },
          h("h2", { tabindex: "-1" }, prompts[card.task]),
          h(
            "p",
            { class: "help" },
            card.task === "missed"
              ? "Tap the dish, or drag a box around it."
              : `Only answer what you can see.`,
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
    if (current.error && card)
      body.append(
        h("p", { class: "learning-error", role: "alert" }, current.error),
      );
    body.querySelectorAll("button,select,input").forEach((el) => {
      if (
        el instanceof HTMLButtonElement ||
        el instanceof HTMLSelectElement ||
        el instanceof HTMLInputElement
      )
        el.disabled =
          current.pending ||
          Boolean(card?.imageMissing && !el.classList.contains("quiet"));
    });
    fill(
      content,
      progress,
      receipt,
      body,
    );
  };
  async function load() {
    if (current.loading) return;
    current.loading = true;
    current.error = "";
    paint();
    try {
      current.data = await getLearning();
    } catch (e) {
      current.error =
        (e instanceof Error ? e.message : "") ||
        "Reconnect to the kitchen and try again.";
    } finally {
      current.loading = false;
      paint();
    }
  }
  /** @param {string} answer @param {{box:LearningBox,kind:string}|null} [value] */
  async function send(answer, value = null) {
    if (
      current.pending ||
      !current.data?.questions.length ||
      (current.data.questions[0].imageMissing && answer !== "unsure")
    )
      return;
    current.pending = true;
    current.error = "";
    const card = current.data.questions[0];
    current.data.questions.shift();
    current.box = null;
    current.receipt = null;
    paint();
    try {
      const saved = await answerLearning(card.id, answer, value);
      current.receipt = { ...saved, card };
      if (answer !== "unsure") current.data.personal_labels++;
      current.box = null;
      announce("Answer saved.");
    } catch (e) {
      current.data.questions.unshift(card);
      current.error =
        (e instanceof Error ? e.message : "") || "That didn’t save. Try again.";
    } finally {
      current.pending = false;
      paint();
      content.querySelector("h2")?.focus({ preventScroll: true });
      if (!current.data?.questions.length && !current.error) void load();
    }
  }
  async function undo() {
    if (current.pending || !current.receipt) return;
    current.pending = true;
    paint();
    try {
      await undoLearning(current.receipt.id);
      current.data?.questions.unshift(current.receipt.card);
      current.receipt = null;
      current.box = null;
      void load();
      announce("Answer undone.");
    } catch (e) {
      current.error =
        (e instanceof Error ? e.message : "") || "Couldn’t undo. Try again.";
    } finally {
      current.pending = false;
      paint();
    }
  }
  requestAnimationFrame(() => {
    paint();
    if (!current.data && !current.loading) void load();
  });
  return host;
}

/** One read per few minutes: the row appears on pages that redraw often. */
let questions = { count: 0, read: 0 };
/** @param {ScreenContext} context @param {boolean} [always] Show the row even with nothing waiting. */
export function learningRow(context, always = false) {
  const open = row({
    label: "Help the camera",
    leading: "spark",
    href: context.href("/help"),
    run: (e) => {
      e.preventDefault();
      context.navigate("/help");
    },
  });
  open.classList.add("learning-prompt");
  const show = () => {
    open.hidden = !always && !questions.count;
  };
  show();
  if (Date.now() - questions.read > 300000) {
    questions.read = Date.now();
    void getLearning()
      .then((data) => {
        questions.count = data.questions.length;
        show();
      })
      .catch(() => {});
  }
  return open;
}

/** @param {ScreenContext} context @param {import("../types.js").Visit} visit */
export function correctionLink(context, visit) {
  return button(
    "The yellow box is in the wrong spot",
    async () => {
      try {
        const result = await feedbackLearning(visit.id);
        if (result.independent_review)
          notify("Thanks. Another housemate will check that.");
        else {
          sessions.delete(context);
          context.navigate("/help");
        }
      } catch (e) {
        notify((e instanceof Error ? e.message : "") || "Couldn’t send that. Try again.");
      }
    },
    "quiet correction-link",
  );
}
