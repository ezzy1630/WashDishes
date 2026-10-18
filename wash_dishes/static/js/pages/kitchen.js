// Now: the camera, what's out right now and whose it is, and who has been leaving dishes lately.
import { fill, h, icon, state, emit, now, person, avatar, dishText, duration, remaining, decision, needsYou, empty, placeholder, head, segmented, ago, when } from "../core.js";
import { LiveView } from "../live.js";
import { allIncidents, counted, kitchenNow, ledger, rangeStart, RANGES } from "../metrics.js";
import { openIncident, outcomePill, thumb, describeEvent } from "../incident.js";

const LAYERS = [["zones", "Areas"], ["dishes", "Dishes"], ["people", "People"]];

export function kitchenPage(main) {
  let range = localStorage.getItem("dw-range") === "30d" ? "30d" : "7d";
  const live = new LiveView({ onSelect: (key, d) => { if (d?.obligation_id) openIncident(d.obligation_id); }, onUpdate: next => onLive(next) });

  const camState = h("p", { class: "cam-state" });
  const layers = h("div", { class: "chips", role: "group", "aria-label": "Show on the picture" }, LAYERS.map(([key, label]) =>
    h("button", { type: "button", "aria-pressed": String(live.layers[key]), onclick: e => { const on = e.currentTarget.getAttribute("aria-pressed") !== "true"; e.currentTarget.setAttribute("aria-pressed", String(on)); live.setLayer(key, on); } }, label)));
  layers.append(h("a", { class: "btn quiet sm", href: "#/live" }, "Open Live", icon("arrowRight", 14)));
  const caption = h("p", { class: "cam-caption" });
  const camCard = h("section", { class: "card cam", "aria-label": "Kitchen camera" }, head("Kitchen", camState, layers), live.el, caption);
  const nowBody = h("div");
  const whoBody = h("div");
  const recentBody = h("div");
  const lateBody = h("div");
  const lateHead = h("div");

  fill(main, h("div", { class: "page" }, h("h1", { class: "sr-only", id: "page-title", tabindex: "-1" }, "Now"),
    h("div", { class: "now-grid" },
      h("div", { class: "col" },
        camCard,
        h("section", { class: "card who-card" }, head("Who's leaving dishes", "tap a name to see each one", segmented(Object.entries(RANGES), range, r => { range = r; localStorage.setItem("dw-range", r); renderWho(); renderLate(); }, "Period")), whoBody)),
      h("div", { class: "col" },
        h("section", { class: "card now", "aria-live": "polite" }, nowBody),
        h("section", { class: "card" }, lateHead, lateBody),
        h("section", { class: "card" }, head("Recent"), recentBody)))));
  live.start();

  // ---- Camera
  function onLive(next) {
    state.live = next ? { ...next, video: live.video.ok, mode: live.mode } : null; emit("live");
    renderCam(next);
    renderNow();
  }

  let camSig = "";
  function renderCam(next = live.data) {
    const lv = next?.live, moved = (next?.camera || state.data?.camera)?.view?.state === "moved";
    const picture = Boolean(live.video.ok || live.imageURL);
    const sig = JSON.stringify([live.mode, (lv?.detections || []).filter(d => d.counted).map(d => [d.kind, d.role]), lv ? Math.round(lv.fps || 0) : 0, moved, picture]);
    if (sig === camSig) return; camSig = sig;
    // Without a picture the placeholder explains itself; a full-height black box would only waste space.
    camCard.classList.toggle("no-picture", !picture);
    layers.hidden = caption.hidden = !picture;
    const [text, tone] = {
      live: [`Live${lv?.fps ? ` · ${lv.fps.toFixed(0)} fps` : ""}`, "ok"], demo: ["Demo · no camera", ""], lost: ["Reconnecting", "late"],
      offline: ["Offline", "late"], waiting: ["Connecting", ""], noVision: ["Live · detection off", "out"],
    }[live.mode] || ["", ""];
    camState.className = `cam-state ${moved ? "late" : tone}`;
    fill(camState, h("i"), moved ? "Camera moved · new incidents paused" : text);
    caption.textContent = describe(lv);
  }

  // What the camera sees, as one sentence.
  function describe(lv) {
    if (!lv) return {
      demo: "This is a simulated household, so there's no picture. Everything else on this page is generated.",
      lost: "Lost contact with the kitchen. Trying again…", waiting: "Waiting for the first picture…",
      offline: state.data?.camera?.configured ? "The camera isn't responding." : "No camera connected yet. Add it in Settings.",
      noVision: "Detection is off, so nothing is being counted.",
    }[live.mode] || "";
    // Describe what is outlined on this frame; the server's settled counts lag behind the boxes.
    const c = { dirty: 0, sink: 0, clean: 0, people: 0 };
    for (const d of lv.detections || []) if (d.counted) { if (d.kind === "person") c.people++; else if (d.role in c) c[d.role]++; }
    const spots = [[c.dirty, "on counters or stove"], [c.sink, "in the sink"], [c.clean, "on the rack"]].filter(([n]) => n);
    const dishes = spots.length ? spots.map(([n, where], i) => i ? `${n} ${where}` : `${n} ${n === 1 ? "dish" : "dishes"} ${where}`).join(", ") : "No dishes currently tracked in this view";
    return `${dishes}${c.people ? ` · ${c.people} ${c.people === 1 ? "person" : "people"} in view` : ""}.`;
  }

  // ---- What's out right now: the answer this page exists for.
  let nowSig = "";
  function renderNow() {
    const current = kitchenNow(state.live, state.data?.obligations);
    const open = current.present.sort((a, b) => (remaining(a) ?? Infinity) - (remaining(b) ?? Infinity));
    const asks = needsYou().length;
    const check = (state.data?.checks || []).find(c => c.status !== "missed");
    const sig = JSON.stringify([current.available, current.dishes, current.unresolved, open.map(o => [o.id, o.status, o.resident_id, o.confidence, o.suggested_resident_id, (o.evidence || []).length]), asks, check && [check.id, check.status, check.out], Math.floor(now() / 30000)]);
    if (sig === nowSig) return; nowSig = sig;
    const dishes = current.dishes, late = open.filter(o => o.overdue_verified);
    const grace = state.data?.settings?.deadline_minutes ?? 120, graceText = grace % 60 ? `${grace}-minute` : `${grace / 60}-hour`;
    const title = !current.available ? "Waiting for a current count" : dishes ? `${dishes} ${dishes === 1 ? "dish" : "dishes"} tracked` : "No dishes currently tracked";
    const sub = !current.available ? "The current camera count is unavailable. Incident history remains below."
      : late.length ? h("span", { class: "late" }, `${late.length} seen again after the ${graceText} grace period.`)
        : "Current estimate on counters, stove, or in the sink. Check the outlines; stacked items can be missed.";
    fill(nowBody,
      h("div", { class: "now-head" }, h("h2", { class: late.length ? "late" : "" }, title), h("p", {}, sub)),
      current.unresolved ? h("a", { class: "ask-row", href: "#/incidents?status=open" }, h("span", { class: "ask-n" }, String(current.unresolved)),
        h("span", {}, "unresolved incidents outside the current view"), h("span", { class: "go" }, "History", icon("arrowRight", 14))) : null,
      check ? h("a", { class: "ask-row", href: "#/review" }, h("span", { class: "ask-n" }, String(check.out)),
        // Untracked: still out at the check but with no record, which is what tracking missed.
        h("span", {}, `${check.out === 1 ? "dish" : "dishes"} out at the ${check.label} check${check.unlinked ? ` · ${check.unlinked} untracked` : ""}`),
        h("span", { class: "go" }, check.status === "pending" ? "Confirm" : "See", icon("arrowRight", 14))) : null,
      asks ? h("a", { class: "ask-row", href: "#/review" }, h("span", { class: "ask-n" }, String(asks)), h("span", {}, asks === 1 ? "dish needs a quick decision" : "dishes need a quick decision"), h("span", { class: "go" }, "Review", icon("arrowRight", 14))) : null,
      open.length ? h("div", { class: "rows" }, open.slice(0, 8).map(outRow), open.length > 8 ? h("a", { class: "more", href: "#/incidents?status=open" }, `${open.length - 8} more in History`) : null) : null);
  }

  function outRow(o) {
    const dec = decision(o), owner = o.confidence === "confirmed" ? person(o.resident_id) : null, guess = person(o.suggested_resident_id);
    const total = o.timing_known ? Date.parse(o.due_at) - Date.parse(o.created_at) : null, gone = now() - Date.parse(o.created_at);
    const frac = total ? Math.min(1, gone / total) : null;
    const attrs = dec ? { href: `#/review?id=${o.id}` } : { type: "button", onclick: () => openIncident(o.id) };
    return h(attrs.href ? "a" : "button", { class: "out-row", ...attrs },
      thumb(o),
      h("span", { class: "out-main" },
        h("span", { class: "out-title" }, dishText(o, { short: true })),
        h("span", { class: "out-meta" }, owner ? h("span", { class: "who" }, avatar(owner.id, owner.name, "xs"), owner.name, o.attribution === "camera" ? h("span", { class: "tx3" }, "· by camera") : null) : h("span", { class: "tx3" }, guess ? `Maybe ${guess.name}` : "Owner unknown"),
          h("span", { class: "tx3" }, o.timing_known ? `out ${duration(gone)}` : "Time left unknown"))),
      outcomePill(o),
      frac !== null && frac < 1 ? h("span", { class: "out-bar", "aria-hidden": "true" }, h("i", { style: { width: `${frac * 100}%` } })) : null);
  }

  // ---- Who's leaving dishes: confirmed dishes per person, late ones first. The camera's guesses
  // are shown beside each name but never added in until someone confirms them.
  let whoSig = "";
  function renderWho() {
    if (!state.history) { fill(whoBody, placeholder(4)); return; }
    const { rows, nobody } = ledger(range);
    const sig = JSON.stringify([range, rows.map(x => [x.r.id, x.r.name, x.mine.length, x.late, x.out, x.guesses.length]), nobody.length]);
    if (sig === whoSig) return; whoSig = sig;
    if (!rows.length) { fill(whoBody, empty("No one added yet", "Add the people who use the kitchen. Then every dish can be put down to someone.", h("a", { class: "btn", href: "#/people" }, "Add people"))); return; }
    const max = Math.max(1, ...rows.map(x => x.mine.length + x.guesses.length));
    const period = range === "7d" ? "this week" : "this month";
    const asks = needsYou().length;
    fill(whoBody, h("div", { class: "who-list" }, rows.map(x => h("a", { class: "who-row", href: `#/person/${x.r.id}` },
      avatar(x.r.id, x.r.name),
      h("span", { class: "who-main" },
        h("span", { class: "who-name" }, x.r.name, x.out ? h("span", { class: "pill" }, `${x.out} unresolved`) : null),
        h("span", { class: "who-bar", title: `${x.mine.length} confirmed ${period} (${x.late} seen after the deadline), ${x.guesses.length} more guessed` },
          h("i", { style: { width: `${(x.mine.length + x.guesses.length) / max * 100}%` } },
            h("b", { class: "late", style: { width: `${x.late / Math.max(1, x.mine.length + x.guesses.length) * 100}%` } }),
            h("b", { class: "ok", style: { width: `${(x.mine.length - x.late) / Math.max(1, x.mine.length + x.guesses.length) * 100}%` } }))),
        h("span", { class: "who-sub" }, x.mine.length ? `Left dishes ${x.mine.length} ${x.mine.length === 1 ? "time" : "times"}` : `Nothing confirmed ${period}`,
          x.guesses.length ? h("span", { class: "guess-note" }, ` · ${x.guesses.length} more ${x.guesses.length === 1 ? "looks" : "look"} like ${x.r.name}`) : null)),
      h("span", { class: "who-score" }, h("b", { class: x.late ? "late" : "" }, String(x.late)), h("small", { title: "Recorded after the grace period; check the evidence" }, "late"))))),
      h("p", { class: "foot" }, asks ? h("a", { class: "link", href: "#/review" }, `${asks} ${asks === 1 ? "dish needs" : "dishes need"} you to say whose it was`) : null,
        asks && nobody.length ? " · " : null, nobody.length ? `${nobody.length} ${period} the camera couldn't place` : null));
  }

  // ---- Late, with proof: dishes the camera saw still out after the grace period, each exportable.
  let lateSig = "";
  function renderLate() {
    if (!state.history) { fill(lateBody, placeholder(2)); return; }
    const period = range === "7d" ? "this week" : "this month";
    const list = allIncidents().filter(o => o.overdue_verified && counted(o) && Date.parse(o.created_at) >= rangeStart(range));
    const sig = JSON.stringify([range, list.map(o => [o.id, o.status, o.resident_id, o.attribution])]);
    if (sig === lateSig) return; lateSig = sig;
    fill(lateHead, head("After-deadline evidence", `recorded after the grace period ${period}; check dish continuity and dirty status`));
    fill(lateBody, list.length ? h("div", { class: "rows late-rows" }, list.slice(0, 8).map(o => {
      const owner = person(o.resident_id);
      return h("div", { class: "out-row late-row" },
        h("button", { type: "button", class: "late-open", onclick: () => openIncident(o.id), "aria-label": `Open ${dishText(o)}` }, thumb(o)),
        h("span", { class: "out-main" },
          h("span", { class: "out-title" }, `${owner?.name || "Someone"} · ${dishText(o, { short: true })}`),
          h("span", { class: "out-meta" }, h("span", { class: "tx3" }, `${when(o.created_at)}${o.location ? ` · ${o.location}` : ""}${o.attribution === "camera" ? " · named by camera" : ""}`))),
        h("a", { class: "btn sm", href: `/api/obligations/${o.id}/proof`, download: "" }, "Proof"));
    }), list.length > 8 ? h("a", { class: "more", href: "#/incidents?status=late" }, `${list.length - 8} more in History`) : null)
      : h("p", { class: "quiet-note" }, `No confirmed after-deadline records ${period}.`));
  }

  // ---- Recent: the household's own log, in its own words.
  let recentSig = "";
  function renderRecent() {
    const list = (state.data?.activity || []).filter(a => a.obligation_id || /^camera_(moved|restored)$/.test(a.event)).slice(0, 7);
    const sig = JSON.stringify([list.map(a => a.id), Math.floor(now() / 60000)]);
    if (sig === recentSig) return; recentSig = sig;
    fill(recentBody, list.length ? h("ul", { class: "recent" }, list.map(a => h("li", {}, a.obligation_id
      ? h("button", { type: "button", onclick: () => openIncident(a.obligation_id) }, h("span", {}, describeEvent(a, allIncidents().find(x => x.id === a.obligation_id))), h("time", {}, ago(a.created_at)))
      : h("div", {}, h("span", {}, describeEvent(a)), h("time", {}, ago(a.created_at))))))
      : h("p", { class: "quiet-note" }, "Nothing has happened yet."));
  }

  const renderAll = () => { renderNow(); renderWho(); renderLate(); renderRecent(); };
  renderAll(); onLive(null);
  const tick = setInterval(() => { if (!document.hidden) { renderNow(); renderRecent(); } }, 15000);
  return {
    update(kind) { if (kind === "data" || kind === "history") renderAll(); },
    destroy() { live.stop(); state.live = null; emit("live"); clearInterval(tick); },
  };
}
