// People: who's been leaving dishes and washing them late, plus the household list itself.
import { fill, h, icon, api, state, emit, avatar, activeResidents, toast, field, form, openSheet, closeSheet, duration, empty, busy, placeholder, pct, segmented, head } from "../core.js";
import { accountability, RANGES } from "../metrics.js";
import { scoreboard } from "../scoreboard.js";

export function peoplePage(main) {
  let range = localStorage.getItem("dw-people-range") === "7d" ? "7d" : "30d";
  const lede = h("p", {});
  const grid = h("div", { class: "people" });
  const guesses = h("div");
  const archived = h("div");
  fill(main, h("div", { class: "page" },
    h("header", { class: "page-head" }, h("div", {}, h("h1", { id: "page-title", tabindex: "-1" }, "Housemates"), lede),
      h("div", { class: "page-tools" }, segmented(Object.entries(RANGES), range, r => { range = r; localStorage.setItem("dw-people-range", r); render(); }, "Period"),
        h("button", { type: "button", class: "btn", onclick: addPerson }, icon("plus", 15), h("span", {}, "Add person")))),
    grid, guesses, archived));

  let sig = "", board = null;
  async function loadBoard() {
    try { board = await api("/api/scoreboard"); sig = ""; render(); } catch { /* the rest of the page still works */ }
  }

  function render() {
    const people = activeResidents(), stats = new Map((state.data?.statistics || []).map(s => [s.resident_id, s]));
    const acc = state.history ? accountability(range) : null, l = state.data?.learning || {};
    const next = JSON.stringify([range, board, people.map(r => [r.id, r.name, r.enrolled_samples, r.has_link, r.notify_url]), acc?.rows.map(x => [x.r.id, x.incidents, x.decided, x.lateCount, x.median, x.helped, x.out, x.lateNow, x.days]), l, (state.data?.residents || []).filter(r => !r.active).map(r => r.id)]);
    if (next === sig) return; sig = next;
    const period = range === "7d" ? "this week" : "this month";

    if (!people.length) {
      lede.textContent = "Add everyone who uses the kitchen.";
      fill(grid, h("div", { class: "card" }, empty("No one here yet", "Add each person who uses the kitchen. Review then asks whose a dish is with one tap per name, and each answer teaches the camera what they look like.", h("button", { type: "button", class: "btn primary", onclick: addPerson }, "Add person"))));
    } else if (!acc) {
      lede.textContent = "Loading…";
      fill(grid, h("div", { class: "card" }, placeholder(4)));
    } else {
      lede.textContent = verdict(acc.rows, period);
      const rows = new Map(acc.rows.map(x => [x.r.id, x]));
      const maxDay = Math.max(1, ...acc.rows.flatMap(x => x.days.map(z => z.n)));
      const ordered = [...people].sort((a, b) => (rows.get(b.id)?.lateCount || 0) - (rows.get(a.id)?.lateCount || 0) || (rows.get(b.id)?.incidents || 0) - (rows.get(a.id)?.incidents || 0) || a.name.localeCompare(b.name));
      fill(grid, ordered.map(r => personCard(r, rows.get(r.id), stats.get(r.id) || {}, maxDay, period)));
    }

    fill(guesses, h("section", { class: "card guesses" }, head("How well it's working", "measured from people's own answers; housemates see this too"),
      h("div", { class: "card-body stack" }, board ? scoreboard(board) : placeholder(3),
        h("p", { class: "tx3" }, `Appearance learned from ${l.labeled_samples ?? 0} confirmed sightings${l.unlabeled_samples ? `; ${l.unlabeled_samples} more waiting for an answer` : ""}.`))));

    const gone = (state.data?.residents || []).filter(r => !r.active);
    fill(archived, gone.length ? h("p", { class: "quiet-note" }, `Archived: ${gone.map(r => r.name).join(", ")}. Their history is kept; face samples were removed.`) : null);
  }

  function personCard(r, x, s, maxDay, period) {
    const stat = (value, label, tone = "") => h("div", { class: "stat" }, h("b", { class: tone }, value), h("span", {}, label));
    return h("article", { class: "card person" },
      h("header", { class: "person-head" }, avatar(r.id, r.name, "lg"),
        h("div", { class: "person-name" }, h("h2", {}, r.name), h("p", {}, s.dishes_unwashed ? h("span", { class: s.dishes_late_now ? "late" : "out" }, `${s.dishes_unwashed} ${s.dishes_unwashed === 1 ? "dish" : "dishes"} out now${s.dishes_late_now ? `, ${s.dishes_late_now} late` : ""}`) : "Nothing out now")),
        h("div", { class: "person-tools" },
          h("button", { type: "button", class: `icon-btn ${r.has_link ? "" : "tx3"}`, title: "Personal link and alerts", "aria-label": `Personal link and alerts for ${r.name}`, onclick: () => personal(r) }, icon("key", 15)),
          h("button", { type: "button", class: "icon-btn", title: "Face samples", "aria-label": `Face samples for ${r.name}`, onclick: () => faces(r) }, icon("camera", 15)),
          h("button", { type: "button", class: "icon-btn", title: "Archive", "aria-label": `Archive ${r.name}`, onclick: () => archive(r) }, icon("trash", 15)))),
      h("div", { class: "stats" },
        stat(String(x?.lateCount ?? 0), x?.decided ? `late of ${x.decided}` : "late", x?.lateCount ? "late" : ""),
        stat(String(x?.incidents ?? 0), `left out ${period}`),
        stat(x?.median != null ? duration(x.median) : "—", "usual time to wash")),
      h("div", { class: "spark-days", role: "img", "aria-label": `Dishes left each day for the last 14 days: ${(x?.days || []).map(z => z.n).join(", ")}` },
        (x?.days || []).map(z => h("i", { class: z.late ? "late" : z.n ? "some" : "", style: { height: `${z.n ? 20 + z.n / maxDay * 80 : 6}%` } }))),
      h("footer", { class: "person-foot" }, h("span", {}, "Last 14 days", x?.helped ? ` · washed ${x.helped} for others` : ""), h("a", { class: "link", href: `#/incidents?person=${r.id}` }, "History", icon("arrowRight", 13))));
  }

  function addPerson() {
    const f = form(async fd => { await api("/api/residents", { method: "POST", body: { name: String(fd.get("name")).trim() } }); closeSheet(); toast("Added."); emit("changed"); }, { submit: "Add person" });
    f.append(field("Name", h("input", { name: "name", required: true, maxlength: 60, autocomplete: "off", placeholder: "First name or nickname" })));
    f.finish(); openSheet("Add person", f);
  }

  // Each housemate's own page: their records, the proof, and a Dispute button; plus their own alerts.
  function personal(r) {
    const linkBox = h("div", { class: "stack" });
    const showLink = token => {
      const url = `${location.origin}/me#${token}`;
      const input = h("input", { value: url, readonly: true, "aria-label": "Personal link", onfocus: e => e.target.select() });
      fill(linkBox, field("Send this to " + r.name, input, "It opens only their own records. Anyone with the link can see them, so send it to them directly. Making a new link turns this one off."),
        h("div", { class: "row-actions" }, h("button", { type: "button", class: "btn sm", onclick: async () => { try { await navigator.clipboard.writeText(url); toast("Link copied."); } catch { input.select(); } } }, "Copy link")));
    };
    fill(linkBox, h("p", { class: "tx2" }, r.has_link ? `${r.name} has a link. Making a new one turns the old one off.` : `${r.name} doesn't have a link yet.`),
      h("div", { class: "row-actions" }, h("button", { type: "button", class: "btn", onclick: e => busy(e.currentTarget, async () => { const { token } = await api(`/api/residents/${r.id}/link`, { method: "POST", body: {} }); showLink(token); emit("changed"); }) }, r.has_link ? "Make a new link" : "Make a link")));
    const alerts = form(async fd => { await api(`/api/residents/${r.id}`, { method: "PATCH", body: { notify_url: String(fd.get("notify_url") || "").trim() } }); toast("Saved."); emit("changed"); }, { submit: "Save" });
    alerts.append(field(`${r.name}'s ntfy topic`, h("input", { type: "url", name: "notify_url", value: r.notify_url || "", placeholder: "https://ntfy.sh/a-long-private-topic", autocomplete: "off" }),
      "They get a push when their dish is late and whenever the camera names them, so they can dispute it straight away, plus their own weekly line."));
    alerts.finish(h("button", { type: "button", class: "btn", onclick: e => busy(e.currentTarget, async () => { await api("/api/notify/test", { method: "POST", body: { resident_id: r.id } }); toast("Test sent."); }) }, "Send a test"));
    openSheet(`${r.name} · personal link and alerts`, h("div", { class: "stack" },
      h("p", { class: "callout" }, icon("info", 15), h("span", {}, "The link works on devices that can reach the Jetson, which means the household Tailscale network.")),
      linkBox, h("div", { class: "divider" }), alerts));
  }

  function archive(r) {
    const f = form(async () => { await api(`/api/residents/${r.id}`, { method: "DELETE" }); closeSheet(); toast(`${r.name} archived.`); emit("changed"); }, { submit: `Archive ${r.name}`, kind: "danger" });
    f.append(h("p", { class: "tx2" }, `${r.name} won't appear in Review any more and their face samples are deleted. Their history stays.`));
    f.finish(); openSheet(`Archive ${r.name}?`, f);
  }

  function faces(r) {
    const demo = state.data?.mode === "demo", cam = state.data?.camera?.connected;
    const upload = form(async fd => {
      const file = fd.get("image");
      if (!file?.size) throw new Error("Choose a photo first.");
      if (file.size > 8 * 1024 * 1024) throw new Error("Photos must be smaller than 8 MB.");
      await api(`/api/residents/${r.id}/enroll`, { method: "POST", body: fd }); closeSheet(); toast("Face sample saved."); emit("changed");
    }, { submit: "Save from photo" });
    upload.append(field("Photo", h("input", { type: "file", name: "image", accept: "image/jpeg,image/png,image/webp", required: true }), "One clear, well-lit face. Only a numeric sample is kept, not the photo."));
    upload.finish();
    const capture = h("button", { type: "button", class: "btn", disabled: !cam || demo, onclick: e => busy(e.currentTarget, async () => { await api(`/api/residents/${r.id}/enroll-camera`, { method: "POST", body: {} }); closeSheet(); toast("Face sample saved from the camera."); emit("changed"); }) }, icon("camera", 15), h("span", {}, "Capture from the kitchen camera"));
    const body = h("div", { class: "stack" },
      h("p", { class: "callout" }, icon("info", 15), h("span", {}, `Only with ${r.name}'s consent. Samples stay on the Jetson. The overhead camera rarely sees faces well, so guesses mostly rely on clothes and height.`)),
      h("p", { class: "tx2" }, `${r.enrolled_samples || 0} face ${r.enrolled_samples === 1 ? "sample" : "samples"} saved.`),
      upload, h("div", { class: "divider" }),
      h("h3", {}, "From the camera"), h("p", { class: "help" }, `Ask ${r.name} to stand alone in view and look up at the camera.`), capture,
      !cam && h("p", { class: "help" }, demo ? "The demo has no camera." : "Connect the camera in Settings first."),
      r.enrolled_samples ? h("div", { class: "stack" }, h("div", { class: "divider" }), h("button", { type: "button", class: "btn danger", onclick: e => busy(e.currentTarget, async () => { await api(`/api/residents/${r.id}/enrollment`, { method: "DELETE" }); closeSheet(); toast("Face samples removed."); emit("changed"); }) }, icon("trash", 15), h("span", {}, `Remove all ${r.enrolled_samples} samples`))) : null);
    openSheet(`Face samples · ${r.name}`, body);
  }

  render(); loadBoard();
  const tick = setInterval(() => { if (!document.hidden) loadBoard(); }, 60000);
  return { update(kind) { if (kind === "data" || kind === "history") render(); }, destroy() { clearInterval(tick); } };
}

// The one-line answer to "who isn't washing their dishes?"
function verdict(rows, period) {
  if (!rows.some(x => x.incidents)) return `Nobody left dishes out ${period}.`;
  const late = rows.filter(x => x.lateCount).sort((a, b) => b.lateCount - a.lateCount || (b.latePct ?? 0) - (a.latePct ?? 0));
  if (!late.length) return `Everyone washed up on time ${period}.`;
  const [a, b] = late;
  if (b && b.lateCount === a.lateCount) return `${a.r.name} and ${b.r.name} were each late ${a.lateCount} ${a.lateCount === 1 ? "time" : "times"} ${period}.`;
  return `${a.r.name} was late most often ${period}: ${a.lateCount} of ${a.decided} ${a.decided === 1 ? "time" : "times"}.`;
}
