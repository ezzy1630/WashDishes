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
/** @param {'house'|'arrow'|'dish'|'expand'|'close'} name */
export function icon(name) {
  const paths = {
    house: ["m3 10 9-7 9 7", "M6 8v13h12V8", "M9 21v-8h6v8"],
    arrow: ["M4 12h16", "m14 6 6 6-6 6"],
    dish: [
      "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18",
      "M12 7a5 5 0 1 0 0 10 5 5 0 0 0 0-10",
    ],
    expand: ["M8 3H3v5", "M16 3h5v5", "M8 21H3v-5", "M16 21h5v-5"],
    close: ["m6 6 12 12", "M18 6 6 18"],
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
/** @param {string|null} url @param {string} label @param {boolean} [wide] @param {boolean} [lazy] */
export function photo(url, label, wide = false, lazy = false) {
  const src = imageURL(url),
    box = h("div", { class: `photo ${wide ? "filmstrip" : ""}` });
  if (!src) {
    box.append(h("p", {}, `${label} isn’t available.`));
    return box;
  }
  const img = h("img", {
    src,
    alt: label,
    loading: lazy ? "lazy" : "eager",
    decoding: "sync",
    referrerpolicy: "no-referrer",
  });
  img.onerror = () =>
    box.replaceChildren(h("p", {}, "Photo unavailable. Refresh to try again."));
  const zoom = button("", () => zoomPhoto(src, label), "photo-open");
  zoom.setAttribute("aria-label", `Enlarge ${label.toLowerCase()}`);
  zoom.append(
    img,
    h("span", { class: "zoom-hint" }, icon("expand"), "Tap to zoom"),
  );
  box.append(zoom);
  return box;
}
/** @param {string} src @param {string} label */
function zoomPhoto(src, label) {
  const dialog = document.querySelector("dialog");
  if (!dialog) return;
  const returnTo = document.activeElement;
  dialog.replaceChildren(
    h(
      "header",
      { class: "zoom-header" },
      h("h2", {}, label),
      button("Close", () => dialog.close(), "secondary"),
    ),
    h(
      "div",
      { class: "zoom-scroll" },
      h("img", { src, alt: label, referrerpolicy: "no-referrer" }),
    ),
  );
  dialog.onclose = () => {
    dialog.replaceChildren();
    if (returnTo instanceof HTMLElement) returnTo.focus();
  };
  dialog.showModal();
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
