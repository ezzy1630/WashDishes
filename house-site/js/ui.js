/** @typedef {Node|string|number|null|undefined|false} Child */
/** @template {keyof HTMLElementTagNameMap} K @param {K} tag @param {Record<string, unknown>} [attrs] @param {...(Child|Child[]) } children @returns {HTMLElementTagNameMap[K]} */
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value == null || value === false) continue;
    if (key.startsWith("on") && typeof value === "function")
      el.addEventListener(key.slice(2), /** @type {EventListener} */ (value));
    else if (key === "class") el.className = String(value);
    else if (key === "text") el.textContent = String(value);
    else el.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children.flat())
    if (child != null && child !== false)
      el.append(
        child instanceof Node ? child : document.createTextNode(String(child)),
      );
  return el;
}
/** @typedef {'house'|'arrow'|'dish'|'expand'|'close'|'film'|'person'|'chevron'|'back'|'clock'|'spark'|'info'|'plus'|'share'|'alert'} IconName */
/** @param {IconName} name */
export function icon(name) {
  /** @type {Record<IconName,string[]>} */
  const paths = {
    house: ["m3 11 9-7.5L21 11", "M5.5 9v11.5h13V9", "M9.5 20.5V14h5v6.5"],
    arrow: ["M4 12h16", "m14 6 6 6-6 6"],
    dish: [
      "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18",
      "M12 7.5a4.5 4.5 0 1 0 0 9 4.5 4.5 0 0 0 0-9",
    ],
    expand: ["M8 3H3v5", "M16 3h5v5", "M8 21H3v-5", "M16 21h5v-5"],
    close: ["m6 6 12 12", "M18 6 6 18"],
    film: ["M4 5.5h16a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-11a1 1 0 0 1 1-1", "m10 9 5 3-5 3z"],
    person: ["M12 12a4.25 4.25 0 1 0 0-8.5 4.25 4.25 0 0 0 0 8.5", "M4 20.5c.8-3.9 4-6 8-6s7.2 2.1 8 6"],
    chevron: ["m9.5 5.5 6.5 6.5-6.5 6.5"],
    back: ["m14.5 5 -7 7 7 7"],
    clock: ["M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18", "M12 7v5l3.5 2"],
    spark: ["M12 3.5v4", "M12 16.5v4", "M3.5 12h4", "M16.5 12h4", "m6 6 2.8 2.8", "m15.2 15.2 2.8 2.8", "m18 6-2.8 2.8", "M8.8 15.2 6 18"],
    info: ["M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18", "M12 11v5.5", "M12 7.6v.1"],
    plus: ["M12 5v14", "M5 12h14"],
    share: ["M12 15V3.5", "m7.5 7.5 4.5-4.5 4.5 4.5", "M6 11H5v9.5h14V11h-1"],
    alert: ["M12 3.5 2.5 20h19z", "M12 10v4.5", "M12 17.3v.1"],
  };
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  for (const [k, v] of Object.entries({
    viewBox: "0 0 24 24",
    width: "20",
    height: "20",
    fill: "none",
    stroke: "currentColor",
    "stroke-width": "1.65",
    "stroke-linecap": "round",
    "stroke-linejoin": "round",
    "aria-hidden": "true",
  }))
    svg.setAttribute(k, v);
  for (const d of paths[name] || paths.dish) {
    const p = document.createElementNS(svg.namespaceURI, "path");
    p.setAttribute("d", d);
    svg.append(p);
  }
  return svg;
}
/** @param {string} label @param {(event:MouseEvent)=>void} run @param {string} [className] */
export const button = (label, run, className = "") =>
  h("button", { type: "button", class: className, onclick: run }, label);
/** @param {string} label @param {HTMLInputElement|HTMLSelectElement|HTMLTextAreaElement} input @param {string} [help] */
export function field(label, input, help = "") {
  const id = input.name || `field-${Math.random().toString(36).slice(2)}`;
  input.id = id;
  const hint = h("p", { class: "help", id: `${id}-help` }, help);
  if (help) input.setAttribute("aria-describedby", hint.id);
  return h(
    "div",
    { class: "field" },
    h("label", { for: id }, label),
    input,
    help ? hint : null,
  );
}
/** @param {string} title @param {string} text */
export const empty = (title, text) =>
  h("div", { class: "empty" }, h("h2", {}, title), h("p", {}, text));
/** @param {string} text */
export function announce(text) {
  const target = document.getElementById("announcement");
  if (target) target.textContent = text;
}
/** @param {string} value @param {string} timezone @param {boolean} [time] */
export function date(value, timezone, time = false) {
  if (!value || !Number.isFinite(Date.parse(value))) return "Time unavailable";
  // Calendar dates are formatted at UTC noon, independent of the viewer's zone.
  const calendar = /^\d{4}-\d{2}-\d{2}$/.test(value);
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    ...(time ? { hour: "numeric", minute: "2-digit" } : {}),
    timeZone: calendar ? "UTC" : timezone,
  }).format(new Date(calendar ? `${value}T12:00:00Z` : value));
}
/** @param {string|null} value */
export function imageURL(value) {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.origin === location.origin ||
      [new URL(HOUSE_API_ORIGIN).origin, new URL(SUPABASE_URL).origin].includes(
        url.origin,
      )
      ? url.href
      : null;
  } catch {
    return null;
  }
}
import { HOUSE_API_ORIGIN, SUPABASE_URL } from "../config.js";
/** @param {string|null} url @param {string} label @param {boolean} [wide] @param {boolean} [lazy] @param {{width:number|null,height:number|null}} [size] */
export function photo(url, label, wide = false, lazy = false, size) {
  const src = imageURL(url),
    box = h("div", { class: `photo ${wide ? "filmstrip" : ""}` });
  if (!src) {
    box.append(h("p", {}, "The photo for this one is gone."));
    return box;
  }
  if (size?.width && size.height)
    box.style.aspectRatio = `${size.width} / ${size.height}`;
  const preview = new URL(src, location.origin);
  if (!wide && preview.pathname.startsWith('/house/media/'))
    preview.searchParams.set('w', '640');
  const img = h("img", {
    width: size?.width,
    height: size?.height,
    src: preview.href,
    alt: label,
    loading: lazy ? "lazy" : "eager",
    decoding: "async",
    referrerpolicy: "no-referrer",
  });
  img.onload = () => {
    box.style.aspectRatio = `${img.naturalWidth} / ${img.naturalHeight}`;
  };
  img.onerror = () =>
    box.replaceChildren(h("p", {}, "This photo didn’t load. Reopen the page to try again."));
  const zoom = button("", () => zoomPhoto(src, label), "photo-open");
  zoom.setAttribute("aria-label", `Enlarge ${label.toLowerCase()}`);
  zoom.append(img);
  if (!wide)
    zoom.append(
      h("span", { class: "zoom-hint", "aria-hidden": "true" }, icon("expand")),
    );
  box.append(zoom);
  if (wide) {
    zoom.append(
      h("span", { class: "zoom-hint", "aria-hidden": "true" }, icon("expand")),
    );
    return h("div", { class: "arrival-photo" }, box);
  }
  return box;
}
/** @param {string} src @param {string} label */
function zoomPhoto(src, label) {
  const dialog = document.querySelector("#photo-dialog");
  if (!(dialog instanceof HTMLDialogElement)) return;
  const returnTo = document.activeElement;
  const image = h("img", {
    src,
    alt: label,
    referrerpolicy: "no-referrer",
    width: 1200,
  });
  const viewport = h(
    "div",
    {
      class: "zoom-scroll",
      tabindex: 0,
      "aria-label": "Photo. Scroll to move around; pinch to zoom.",
    },
    image,
  );
  let width = 1200;
  function resize(
    /** @type {number} */ next,
    /** @type {number} */ center = viewport.clientWidth / 2,
  ) {
    const prior = width;
    width = Math.max(320, Math.min(4800, next));
    image.width = Math.round(width);
    viewport.scrollLeft =
      ((viewport.scrollLeft + center) * width) / prior - center;
  }
  const tools = h(
    "div",
    { class: "zoom-tools" },
    button("−", () => resize(width / 1.5), "secondary"),
    button("+", () => resize(width * 1.5), "secondary"),
    button("Fit", () => resize(viewport.clientWidth), "secondary"),
  );
  tools.querySelectorAll("button")[0].setAttribute("aria-label", "Zoom out");
  tools.querySelectorAll("button")[1].setAttribute("aria-label", "Zoom in");
  /** @type {{distance:number,width:number}|null} */
  let pinch = null;
  const distance = (/** @type {TouchList} */ touches) =>
    Math.hypot(
      touches[0].clientX - touches[1].clientX,
      touches[0].clientY - touches[1].clientY,
    );
  viewport.addEventListener(
    "touchstart",
    (event) => {
      if (event.touches.length === 2) {
        event.preventDefault();
        pinch = { distance: distance(event.touches), width };
      }
    },
    { passive: false },
  );
  viewport.addEventListener(
    "touchmove",
    (event) => {
      if (pinch && event.touches.length === 2) {
        event.preventDefault();
        const center =
          (event.touches[0].clientX + event.touches[1].clientX) / 2 -
          viewport.getBoundingClientRect().left;
        resize(
          (pinch.width * distance(event.touches)) / pinch.distance,
          center,
        );
      }
    },
    { passive: false },
  );
  viewport.addEventListener("touchend", () => {
    pinch = null;
  });
  dialog.replaceChildren(
    h(
      "header",
      { class: "zoom-header" },
      button("Done", () => dialog.close(), "zoom-done"),
    ),
    tools,
    viewport,
  );
  dialog.onclose = () => {
    dialog.replaceChildren();
    if (returnTo instanceof HTMLElement) returnTo.focus();
  };
  dialog.showModal();
  // Open fitted to the screen; zooming in is the user's choice.
  resize(viewport.clientWidth, 0);
}

/** @param {Element} el @param {...(Child|Child[])} children */
export function fill(el, ...children) {
  el.replaceChildren(
    ...children
      .flat()
      .filter((child) => child != null && child !== false)
      .map((child) => (child instanceof Node ? child : String(child))),
  );
}

/** @param {string} title @param {string} [intro] */
export function heading(title, intro = "") {
  return h(
    "header",
    { class: "page-heading" },
    h("h1", { tabindex: "-1" }, title),
    intro ? h("p", {}, intro) : null,
  );
}
/** @param {string} text @param {string} [className] */
export function tag(text, className = "") {
  return h("span", { class: `tag ${className}` }, text);
}
/**
 * One row of a grouped list. Rows are links when `href` is given, buttons when `run` is.
 * @param {{label:string,detail?:string,value?:string,leading?:IconName,tone?:'danger',href?:string,run?:(event:MouseEvent)=>void,chevron?:boolean,thumb?:string|null}} options
 */
export function row({ label, detail, value, leading, tone, href, run, chevron = true, thumb }) {
  const body = [
    thumb
      ? h("img", { class: "row-thumb", src: thumb, alt: "", width: 44, height: 44, loading: "lazy", referrerpolicy: "no-referrer" })
      : leading
        ? h("span", { class: "row-icon" }, icon(leading))
        : null,
    h(
      "span",
      { class: "row-text" },
      h("span", { class: "row-label" }, label),
      detail ? h("span", { class: "row-detail" }, detail) : null,
    ),
    value ? h("span", { class: "row-value" }, value) : null,
    chevron ? h("span", { class: "row-chevron", "aria-hidden": "true" }, icon("chevron")) : null,
  ];
  const cls = `row${tone === "danger" ? " row-danger" : ""}`;
  return href
    ? h("a", { class: cls, href, onclick: run }, ...body)
    : h("button", { type: "button", class: cls, onclick: run }, ...body);
}
/** @param {string} title @param {...(Child|Child[])} rows */
export function group(title, ...rows) {
  return h(
    "section",
    { class: "group" },
    title ? h("h2", { class: "group-title" }, title) : null,
    h("div", { class: "group-rows" }, ...rows),
  );
}

/** @type {(()=>void)|null} */
let afterSheet = null;
// close() fires its event a task later. When one sheet replaces another, that
// late event belongs to the old sheet and must not tear down the new one.
let closing = false,
  replaced = false;
/**
 * A bottom sheet on the shared native dialog: Escape, focus trapping and the top
 * layer all come from the browser. Drag the grabber down or tap outside to dismiss.
 * @param {string} title @param {Child|Child[]} content @param {{onclose?:()=>void}} [options]
 */
export function openSheet(title, content, options = {}) {
  const dialog = document.getElementById("sheet");
  if (!(dialog instanceof HTMLDialogElement)) return;
  const returnTo = document.activeElement;
  if (closing) replaced = true;
  const panel = h(
    "div",
    { class: "sheet-panel" },
    h("div", { class: "sheet-grabber", "aria-hidden": "true" }),
    h(
      "header",
      { class: "sheet-header" },
      h("h2", { id: "sheet-title", tabindex: "-1" }, title),
      button("Close", () => closeSheet(), "sheet-close"),
    ),
    h("div", { class: "sheet-body" }, content),
  );
  dialog.replaceChildren(panel);
  afterSheet = options.onclose || null;
  dialog.onclose = () => {
    closing = false;
    if (replaced) {
      replaced = false;
      return;
    }
    dialog.replaceChildren();
    panel.style.transform = "";
    const done = afterSheet;
    afterSheet = null;
    if (returnTo instanceof HTMLElement && returnTo.isConnected) returnTo.focus();
    done?.();
  };
  dialog.onclick = (event) => {
    if (event.target === dialog) closeSheet();
  };
  const header = /** @type {HTMLElement} */ (panel.querySelector(".sheet-header")),
    grabber = /** @type {HTMLElement} */ (panel.querySelector(".sheet-grabber"));
  for (const handle of [header, grabber]) dragToDismiss(handle, panel);
  dialog.showModal();
  /** @type {HTMLElement|null} */ (panel.querySelector("h2"))?.focus({ preventScroll: true });
}
export function closeSheet() {
  const dialog = document.getElementById("sheet");
  if (dialog instanceof HTMLDialogElement && dialog.open) {
    closing = true;
    dialog.close();
  }
}
/** @param {HTMLElement} handle @param {HTMLElement} panel */
function dragToDismiss(handle, panel) {
  /** @type {number|null} */
  let start = null;
  handle.addEventListener("pointerdown", (event) => {
    if (event.target instanceof HTMLButtonElement) return;
    start = event.clientY;
    handle.setPointerCapture(event.pointerId);
    panel.style.transition = "none";
  });
  handle.addEventListener("pointermove", (event) => {
    if (start !== null)
      panel.style.transform = `translateY(${Math.max(0, event.clientY - start)}px)`;
  });
  const finish = (/** @type {PointerEvent} */ event) => {
    if (start === null) return;
    const moved = event.clientY - start;
    start = null;
    panel.style.transition = "";
    panel.style.transform = "";
    if (moved > 90) closeSheet();
  };
  handle.addEventListener("pointerup", finish);
  handle.addEventListener("pointercancel", finish);
}

/** @type {ReturnType<typeof setTimeout>|null} */
let noticeTimer = null;
/** A short visible confirmation; screen readers hear the same words. @param {string} text */
export function notify(text) {
  announce(text);
  const host = document.getElementById("notice");
  if (!host) return;
  host.textContent = text;
  host.hidden = false;
  if (noticeTimer) clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => {
    host.hidden = true;
  }, 3500);
}

/** Today / Yesterday / weekday for this week; a date beyond that. @param {string} value @param {string} timezone */
export function when(value, timezone) {
  if (!value || !Number.isFinite(Date.parse(value))) return "Time unknown";
  const stamp = new Date(value);
  const day = (/** @type {Date} */ d) =>
    new Intl.DateTimeFormat("en-CA", { timeZone: timezone }).format(d);
  const time = new Intl.DateTimeFormat("en-US", {
    hour: "numeric",
    minute: "2-digit",
    timeZone: timezone,
  }).format(stamp);
  const age = Math.round(
    (Date.parse(day(new Date())) - Date.parse(day(stamp))) / 86400000,
  );
  if (age === 0) return `Today ${time}`;
  if (age === 1) return `Yesterday ${time}`;
  const named = new Intl.DateTimeFormat("en-US", {
    ...(age > 1 && age < 7 ? { weekday: "long" } : { month: "short", day: "numeric" }),
    timeZone: timezone,
  }).format(stamp);
  return `${named} ${time}`;
}

/** @param {import('./types.js').Visit} visit @param {boolean} [filmstrip] */
export function mediaSize(visit, filmstrip = true) {
  return filmstrip
    ? { width: visit.filmstrip_width, height: visit.filmstrip_height }
    : { width: visit.photo_width, height: visit.photo_height };
}
