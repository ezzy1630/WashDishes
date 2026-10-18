// How well it's working: the measures everyone in the house can check, week by week.
import { h, pct } from "./core.js";

const share = (n, d) => d ? `${n} of ${d}` : "—";
const sum = (weeks, key) => weeks.reduce((n, w) => n + (w[key] || 0), 0);
const day = iso => new Date(`${iso}T12:00:00`).toLocaleDateString([], { month: "short", day: "numeric" });

// One sentence over the recent weeks, then the table, then what the self-improvement jobs did.
export function scoreboard(board, { recent = 4, admin = true } = {}) {
  const weeks = board?.weeks || [];
  const last = weeks.slice(-recent);
  const checked = sum(last, "identity_checked"), right = sum(last, "identity_correct");
  const alarms = sum(last, "false_alarms"), real = sum(last, "real");
  const disputes = sum(last, "disputes"), overturned = sum(last, "disputes_overturned");
  const named = sum(last, "camera_named"), corrected = sum(last, "camera_corrected");
  const lines = [
    checked ? `Its guess at who left a dish was right ${right} of ${checked} times a person checked (${pct(right / checked)}).` : "Nobody has checked its guesses at who left a dish yet.",
    alarms + real ? `${pct(alarms / (alarms + real))} of the dishes it flagged turned out not to be dishes (${alarms} of ${alarms + real}).` : "None of its flagged dishes have been checked yet.",
    named ? `It named ${named} owners itself; ${corrected} of those were corrected by a person.` : null,
    disputes ? `${disputes} ${disputes === 1 ? "dispute" : "disputes"} raised; ${overturned} changed the outcome.` : "No disputes raised.",
  ].filter(Boolean);
  const auto = board?.auto;
  const autoLine = !auto ? null : !auto.enabled ? "Naming by camera is switched off." : auto.active
    ? `It names owners itself, because its last ${auto.checked} checked guesses were ${pct(auto.accuracy)} right. Every fifth dish still goes to a person.`
    : `A person confirms every owner until ${auto.min_checked} guesses have been checked at ${pct(auto.min_accuracy)} or better (${auto.checked} so far).`;
  const rows = [...weeks].reverse().filter(w => w.camera_records || w.disputes);
  return h("div", { class: "score" },
    h("div", { class: "score-lede" }, h("p", {}, `Last ${recent} weeks: `, lines.join(" ")), autoLine ? h("p", { class: "tx2" }, autoLine) : null),
    rows.length ? h("div", { class: "score-table-wrap" }, h("table", { class: "score-table" },
      h("thead", {}, h("tr", {}, ["Week of", "Flagged", "Not a dish", "Guess right", "Named by camera", "Corrected", "Disputes", "Late"].map(t => h("th", { scope: "col" }, t)))),
      h("tbody", {}, rows.map(w => h("tr", {},
        h("th", { scope: "row" }, day(w.week)), h("td", {}, String(w.camera_records)),
        h("td", {}, w.false_alarm_rate == null ? "—" : `${pct(w.false_alarm_rate)} (${w.false_alarms})`),
        h("td", {}, w.identity_checked ? `${pct(w.identity_accuracy)} (${share(w.identity_correct, w.identity_checked)})` : "—"),
        h("td", {}, String(w.camera_named)), h("td", {}, String(w.camera_corrected)),
        h("td", {}, w.disputes ? `${w.disputes} (${w.disputes_overturned} changed)` : "0"), h("td", {}, String(w.late_verified)))))))
      : h("p", { class: "tx3" }, "No camera records in these weeks yet."),
    admin ? improvement(board) : null);
}

function improvement(board) {
  const run = board?.detector_runs?.[0], fit = board?.identity_fit;
  const when = at => new Date(at).toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
  const score = s => s ? `found ${share(s.matched, s.truth)}, ${pct(s.precision)} of its finds right` : "";
  return h("div", { class: "score-improve" },
    h("h3", {}, "Self-improvement"),
    h("p", {}, h("b", {}, "Dish detector. "), run
      ? [`Last run ${when(run.at)}: ${run.promoted ? "switched to the new detector" : "kept the current one"}. ${run.reason}`,
         run.candidate ? ` New: ${score(run.candidate)}; previous: ${score(run.current)}.` : ""].join("")
      : "Retrains every Sunday at 2am once 20 new pictures are labelled. It hasn't run yet."),
    h("p", {}, h("b", {}, "Recognising people. "), fit
      ? `Last checked ${when(fit.at)}: ${fit.accuracy_now == null ? "" : `its top guess was right for ${pct(fit.accuracy_now)} of ${fit.cases} sightings people named. `}`
        + (fit.adopted ? `Relearned from them, now right ${pct(fit.held_out_accuracy)} on names it didn't learn from (was ${pct(fit.current_accuracy)}).` : fit.reason)
      : "Relearns how much faces, hair, clothes and height count, every night, once 40 sightings have been named. It hasn't run yet."));
}
