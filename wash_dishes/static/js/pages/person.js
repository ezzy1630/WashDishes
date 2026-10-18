// One person: every time they left dishes out, with the photos to check it, and the camera's
// guesses about them waiting apart until someone confirms or corrects them.
import { fill, h, icon, act, state, emit, now, person, avatar, dishText, when, toast, busy, empty, placeholder, segmented } from "../core.js";
import { openIncident, outcomePill, rankEvidence } from "../incident.js";
import { ledger, wentLate, RANGES } from "../metrics.js";

export function personPage(main, route) {
  const id = Number(route.sub);
  let range = route.query?.range in RANGES ? route.query.range : localStorage.getItem("dw-range") === "30d" ? "30d" : "7d";
  const title = h("h1", { id: "page-title", tabindex: "-1" });
  const lede = h("p", {});
  const body = h("div", { class: "cases-wrap" });
  fill(main, h("div", { class: "page narrow" },
    h("a", { class: "crumb", href: "#/kitchen" }, icon("arrowLeft", 13), "Everyone"),
    h("header", { class: "page-head" }, h("div", { class: "person-head" }, h("span", { class: "person-avatar" }), h("div", {}, title, lede)),
      segmented(Object.entries(RANGES), range, r => { range = r; localStorage.setItem("dw-range", r); history.replaceState(null, "", `#/person/${id}?range=${r}`); sig = ""; render(); }, "Period")),
    body));

  let sig = "";
  function render() {
    const r = person(id);
    if (!r) { fill(body, h("div", { class: "card" }, empty("No one by that name", "They may have been archived.", h("a", { class: "btn", href: "#/people" }, "See everyone")))); return; }
    if (!state.history) { fill(body, h("div", { class: "card" }, placeholder(6))); return; }
    const row = ledger(range).rows.find(x => x.r.id === id) || { mine: [], guesses: [], late: 0, out: 0 };
    const next = JSON.stringify([range, r.name, [...row.mine, ...row.guesses].map(o => [o.id, o.status, o.confidence, o.resident_id, o.suggested_resident_id, o.overdue_verified, (o.evidence || []).length]), Math.floor(now() / 60000)]);
    if (next === sig) return; sig = next;
    const period = range === "7d" ? "this week" : "this month";
    document.querySelector(".person-avatar")?.replaceChildren(avatar(r.id, r.name, "lg"));
    title.textContent = r.name;
    const n = row.mine.length;
    lede.textContent = n
      ? `Left dishes out ${n} ${n === 1 ? "time" : "times"} ${period}: ${row.late ? `${row.late} went past the grace period` : "none went past the grace period"}${row.out ? `, ${row.out} still out now` : ""}.`
      : `No confirmed dishes left out ${period}.`;
    const late = row.mine.filter(wentLate), rest = row.mine.filter(o => !wentLate(o));
    fill(body,
      late.length ? section(`Not washed in time (${late.length})`, "Still out after the grace period, seen by the camera.", late, r) : null,
      rest.length ? section(`Left out (${rest.length})`, "Washed in time, or still inside the grace period.", rest, r) : null,
      row.guesses.length ? section(`The camera thinks these were ${r.name}'s (${row.guesses.length})`, "Not counted until you check them. Look at the photos, then confirm or pick who it was.", row.guesses, r, true) : null,
      !n && !row.guesses.length ? h("div", { class: "card" }, empty(`Nothing for ${r.name} ${period}`, "When the camera sees them leave a dish, it shows up here.")) : null);
  }

  function section(heading, sub, list, r, guess = false) {
    return h("section", { class: "cases-section" },
      h("h2", {}, heading), h("p", { class: "tx3" }, sub),
      h("div", { class: "cases" }, list.map(o => caseCard(o, r, guess))));
  }

  function caseCard(o, r, guess) {
    const photos = rankEvidence(o.evidence).filter(e => e.kind !== "clip");
    const where = photos.find(e => /^Dish left|^Already present|^Possible/.test(e.label)) || photos.find(e => !/^Who handled|^Person/.test(e.label)) || photos[0];
    const them = photos.find(e => /^Who handled/.test(e.label)) || photos.find(e => /^Person/.test(e.label));
    const clip = (o.evidence || []).some(e => e.kind === "clip" && !e.expired && e.url);
    return h("article", { class: `case${guess ? " unconfirmed" : ""}` },
      h("button", { type: "button", class: "case-photos", onclick: () => openIncident(o.id), "aria-label": `Open ${dishText(o)} from ${when(o.created_at)}` },
        where ? h("img", { src: where.url, alt: "Where it was left", loading: "lazy" }) : h("span", { class: "case-none" }, icon("dish", 20), "No photo kept"),
        them ? h("img", { class: "case-who", src: them.url, alt: "Who handled it", loading: "lazy" }) : null),
      h("div", { class: "case-info" },
        h("div", { class: "case-title" }, h("b", {}, dishText(o, { short: true })), outcomePill(o)),
        h("p", { class: "tx3" }, `${when(o.created_at)}${o.location ? ` · ${o.location}` : ""}`),
        h("div", { class: "case-actions" },
          guess ? h("button", { type: "button", class: "btn sm primary", onclick: e => busy(e.currentTarget, async () => { await act(o.id, { action: "confirm", resident_id: r.id }); toast(`Counted as ${r.name}'s.`); emit("changed"); }) }, `Yes, ${r.name}'s`) : null,
          h("button", { type: "button", class: "btn sm", onclick: () => openIncident(o.id) }, clip ? icon("play", 13) : null, guess ? "Someone else" : clip ? "Watch clip" : "Details"))));
  }

  render();
  return { update(kind) { if (kind === "data" || kind === "history") render(); }, destroy() {} };
}
