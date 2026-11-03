// Local-only API + static server. Never deploy this directory or private photos.
import http from "node:http";
import { execFileSync } from "node:child_process";
import { readFile, stat } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
const site = fileURLToPath(new URL("../", import.meta.url));
const port = Number(process.env.PORT || 4317);
const scratch =
  process.env.DISHWATCH_SCRATCH ||
  "/Volumes/Neural/dishwatch-scratch/houseleague/laneC";
const fixtureRoot = path.join(scratch, "review3/fixtures");
const fixtureInfo = JSON.parse(
  execFileSync(
    process.env.PYTHON || "/Volumes/Neural/WashDishes/.venv/bin/python",
    [path.join(site, "dev/fixtures.py"), "--output", fixtureRoot],
    { encoding: "utf8" },
  ),
);
const slugs = [
  "ezzy",
  "jadon",
  "sutton",
  "henry",
  "calder",
  "andrew",
  "elliot",
  "pedro",
  "daniel",
  "jacob",
];
const residents = slugs.map((slug, i) => ({
  id: i + 1,
  slug,
  name: slug[0].toUpperCase() + slug.slice(1),
}));
const at = "2026-11-03T01:00:00-08:00";
let closesAt = null,
  dutyMode = "carry",
  offline = false,
  malformed = false,
  delay = 0;
let dishes = [];
let confirmed = new Set();
let passwords = new Map();
let invites = new Set();
const lookup = (id) => {
  const r = residents.find((r) => r.id === id);
  return r ? { id: r.id, name: r.name } : null;
};
function item(id, kind, owner = null, dispute = null) {
  const fixture = fixtureInfo[String(id)] || fixtureInfo["101"];
  return {
    id,
    kind,
    title:
      kind === "pan"
        ? "Pan by the stove"
        : kind === "pot"
          ? "Pot on the counter"
          : kind === "plate"
            ? "Dinner plate"
            : kind === "cup"
              ? "Cup on the counter"
              : "Bowl by the sink",
    location: fixture.area,
    out_since:
      id >= 200
        ? id < 300
          ? "2026-10-27T14:17:22-08:00"
          : "2026-10-20T14:17:22-07:00"
        : fixture.at,
    due_at: new Date(Date.parse(fixture.at) + 2 * 3600000).toISOString(),
    gone_at: null,
    points: ["pan", "pot"].includes(kind) ? 2 : 1,
    resident: lookup(owner),
    named_by: owner ? lookup(6) : null,
    named_at: owner ? at : null,
    dispute,
    filmstrip_url:
      id >= 200 ? null : `http://127.0.0.1:${port}/dev/photos/${fixture.strip}`,
    photo_url:
      id >= 200 ? null : `http://127.0.0.1:${port}/dev/photos/${fixture.photo}`,
    filmstrip_width: id >= 200 ? null : fixture.width,
    filmstrip_height: id >= 200 ? null : fixture.height,
    photo_width: id >= 200 ? null : fixture.photo_width,
    photo_height: id >= 200 ? null : fixture.photo_height,
    arrival_seen: fixture.arrival_seen,
    evidence: id === 113 ? "weak" : id === 101 ? "strong" : "fair",
    reason: id === 113 ? "Arrival not recorded · No one in the photos" : null,
    verdict: null,
    can_name: false,
    can_dispute: false,
    can_settle: false,
  };
}
function reset() {
  confirmed.clear();
  passwords = new Map(slugs.map((s) => [s, "house-demo"]));
  invites = new Set(["demo-invite", "demo-reset"]);
  dishes = [
    item(101, "pan"),
    item(102, "pot"),
    item(103, "plate"),
    item(104, "pan", 1),
    item(105, "pot", 3, {
      state: "open",
      by: lookup(3),
      note: "I was making tea. That pot was already there when I came in.",
      at,
      settled_by: null,
    }),
    item(106, "pan", 2),
    item(107, "pot", 2),
    item(108, "pan", 2),
    item(109, "plate", 2),
    item(110, "pot", 4),
    item(111, "bowl", 4),
    {
      ...item(112, "bowl"),
      title: "Object near the stove",
      verdict: { kind: "not_dish", by: lookup(9), at },
    },
  ];
  closesAt = null;
  dutyMode = "carry";
  offline = false;
  malformed = false;
  delay = 0;
}
reset();
function permissions(d, user) {
  return {
    ...d,
    can_name: !d.resident && (!d.verdict || d.verdict.kind === "cant_tell"),
    can_dispute: d.resident?.id === user.id && !d.dispute,
    can_settle:
      d.dispute?.state === "open" &&
      d.dispute.by.id !== user.id &&
      d.resident?.id !== user.id &&
      d.named_by?.id !== user.id,
  };
}
function visits(records, user) {
  const groups = [
    [101, 102],
    [103],
    [104],
    [105],
    [106, 107, 108, 109],
    [110, 111],
  ];
  const units = groups
    .map((ids) => records.filter((d) => ids.includes(d.id)))
    .filter((list) => list.length);
  const grouped = new Set(groups.flat());
  units.push(...records.filter((d) => !grouped.has(d.id)).map((d) => [d]));
  return units
    .map((list) => {
      const first = list[0],
        v = permissions(first, user),
        title =
          list.length === 4
            ? "2 pans, pot and plate"
            : list.length === 2
              ? list[0].kind === "pan"
                ? "Pan and pot"
                : "Pot and bowl"
              : first.title;
      return {
        id: `visit-${first.id}`,
        dish_ids: list.map((d) => d.id),
        title,
        area: fixtureInfo[String(first.id)]?.area || first.location,
        at: first.out_since,
        points: list.reduce((n, d) => n + d.points, 0),
        filmstrip_url: first.filmstrip_url,
        photo_url: first.photo_url,
        filmstrip_width: first.filmstrip_width,
        filmstrip_height: first.filmstrip_height,
        photo_width: first.photo_width,
        photo_height: first.photo_height,
        arrival_seen: first.arrival_seen,
        evidence: first.evidence,
        reason: first.reason,
        resident: v.resident,
        named_by: v.named_by,
        named_at: v.named_at,
        verdict: first.verdict,
        guess:
          !first.resident && first.id === 101 && !confirmed.has(first.id)
            ? {
                resident_id: 2,
                name: "Jadon",
                basis:
                  "Similar outfit in the arrival photos. This is a suggestion, not a confirmed name.",
              }
            : null,
        dispute: v.dispute,
        can_name: v.can_name,
        can_dispute: v.can_dispute,
        can_settle: v.can_settle,
      };
    })
    .sort((a, b) => Date.parse(b.at) - Date.parse(a.at))
    .sort(
      (a, b) =>
        ({ strong: 0, fair: 1, weak: 2 })[a.evidence] -
        { strong: 0, fair: 1, weak: 2 }[b.evidence],
    );
}
function week(start, user) {
  const old = start === "2026-10-26",
    older = start === "2026-10-19";
  const records = old
    ? [item(201, "pot", 2), item(202, "plate", 4)]
    : older
      ? [item(301, "pan", 1), item(302, "pot", 1), item(303, "plate", 1)]
      : dishes;
  const standings = residents
    .map((r) => {
      const owned = records.filter(
        (d) =>
          d.resident?.id === r.id &&
          !["open", "overturned"].includes(d.dispute?.state) &&
          !["not_dish", "clean", "duplicate"].includes(d.verdict?.kind),
      );
      return {
        resident_id: r.id,
        name: r.name,
        points: owned.reduce((n, d) => n + d.points, 0),
        dishes: owned.length,
      };
    })
    .sort((a, b) => b.points - a.points || a.name.localeCompare(b.name));
  const lead = standings[0].points - standings[1].points;
  const points = standings[0].points;
  const state =
    dutyMode === "disabled"
      ? "disabled"
      : points < 4
        ? "below_minimum"
        : !lead
          ? "tie"
          : lead < 2
            ? "too_close"
            : "pick";
  const pick = {
    resident_id: state === "pick" ? standings[0].resident_id : null,
    name: state === "pick" ? standings[0].name : null,
    state,
    points,
    lead,
    needed_points: state === "disabled" ? 0 : Math.max(0, 4 - points),
    needed_lead: state === "disabled" ? 0 : Math.max(0, 2 - lead),
    reason:
      state === "disabled"
        ? "Standings still count."
        : state === "pick"
          ? `${points} points · ${lead} ahead`
          : state === "tie"
            ? "Tied for first · needs a 2-point lead"
            : state === "too_close"
              ? `Too close — ${lead} point ahead, needs 2`
              : `${points ? `${points} ${points === 1 ? "point" : "points"}` : "No named points"} · needs 4`,
  };
  return {
    start_week: "2026-10-19",
    week: {
      start: old ? "2026-10-26" : older ? "2026-10-19" : "2026-11-02",
      end: old ? "2026-11-02" : older ? "2026-10-26" : "2026-11-09",
      closes_at:
        closesAt ||
        (old
          ? "2026-11-02T12:00:00-08:00"
          : older
            ? "2026-10-26T12:00:00-07:00"
            : "2026-11-09T12:00:00-08:00"),
      closed: old || older,
      timezone: "America/Los_Angeles",
    },
    rules: {
      enabled: dutyMode !== "disabled",
      weights: { pan: 2, pot: 2, default: 1 },
      min_points: 4,
      min_lead: 2,
    },
    standings,
    pick,
    on_duty:
      dutyMode === "disabled"
        ? null
        : {
            resident_id: 1,
            name: "Ezzy",
            since:
              dutyMode === "carry" ? "2026-10-26" : "2026-11-02T12:00:00-08:00",
            why:
              dutyMode === "override"
                ? "Set by the operator after a house vote."
                : "Weekly pick: 5 points · 3 ahead.",
          },
    dishes: records.map((d) => permissions(d, user)),
    visits: visits(records, user).filter(
      (v) => !["not_dish", "clean", "duplicate"].includes(v.verdict?.kind),
    ),
    verdicts: visits(records, user)
      .filter((v) =>
        ["not_dish", "clean", "duplicate"].includes(v.verdict?.kind),
      )
      .map((v) => ({
        visit_id: v.id,
        title: v.title,
        ...v.verdict,
        of_visit_id: null,
      })),
    unnamed_count: records.filter(
      (d) => !d.resident && (!d.verdict || d.verdict.kind === "cant_tell"),
    ).length,
    clear_count: visits(records, user).filter(
      (v) =>
        !v.resident &&
        v.evidence !== "weak" &&
        (!v.verdict || v.verdict.kind === "cant_tell"),
    ).length,
    weak_count:
      old || older
        ? 0
        : visits(records, user).filter(
            (v) => !v.resident && v.evidence === "weak" && !v.verdict,
          ).length,
    unreviewed_count: visits(records, user).filter(
      (v) => !v.resident && (!v.verdict || v.verdict.kind === "cant_tell"),
    ).length,
    weeks: [
      {
        start: "2026-11-02",
        pick_name: old || older ? "Jadon" : pick.name,
        closed: false,
      },
      { start: "2026-10-26", pick_name: null, closed: true },
      { start: "2026-10-19", pick_name: "Ezzy", closed: true },
    ],
  };
}
const headers = JSON.parse(
  await readFile(path.join(site, "vercel.json"), "utf8"),
).headers[0].headers;
function json(res, status, data) {
  res.writeHead(status, {
    "Content-Type": "application/json",
    "Cache-Control": "no-store",
  });
  res.end(JSON.stringify(data));
}
async function body(req) {
  let text = "";
  for await (const chunk of req) {
    text += chunk;
    if (text.length > 8192) throw new Error("Request too large");
  }
  return JSON.parse(text || "{}");
}
const server = http.createServer(async (req, res) => {
  try {
    // Mirror deployment CSP while allowing loopback HTTP for the test server.
    for (const { key, value } of headers)
      if (key !== "Strict-Transport-Security")
        res.setHeader(
          key,
          key === "Content-Security-Policy"
            ? value.replace("; upgrade-insecure-requests", "")
            : value,
        );
    const url = new URL(req.url, `http://127.0.0.1:${port}`),
      pathname = url.pathname;
    if (pathname === "/dev/control" && req.method === "POST") {
      const b = await body(req);
      if (b.reset) reset();
      if (b.evidenceQueue) dishes.push(item(113, "cup"));
      if ("closesAt" in b) closesAt = b.closesAt;
      if (["pick", "carry", "override", "disabled"].includes(b.dutyMode))
        dutyMode = b.dutyMode;
      if ("offline" in b) offline = !!b.offline;
      if ("malformed" in b) malformed = !!b.malformed;
      if ("delay" in b) delay = Number(b.delay);
      if (b.empty) dishes = [];
      if (b.missingPhotos)
        dishes = dishes.map((d) => ({
          ...d,
          filmstrip_url: null,
          photo_url: null,
        }));
      json(res, 200, { offline, malformed, delay });
      return;
    }
    if (pathname === "/dev/sign-in" && req.method === "POST") {
      const b = await body(req);
      json(
        res,
        residents.some((r) => r.slug === b.slug) &&
          b.password === passwords.get(b.slug)
          ? 200
          : 400,
        { ok: true },
      );
      return;
    }
    if (pathname.startsWith("/house/api")) {
      if (delay) await new Promise((resolve) => setTimeout(resolve, delay));
      if (offline) {
        json(res, 503, { detail: "Fixture offline" });
        return;
      }
      const route = pathname.slice("/house/api".length);
      if (route === "/join" && req.method === "POST") {
        const b = await body(req);
        const ok =
          invites.has(b.token) &&
          typeof b.password === "string" &&
          b.password.length >= 8;
        if (ok) {
          passwords.set("ezzy", b.password);
          invites.delete(b.token);
        }
        json(
          res,
          ok ? 200 : 400,
          ok
            ? { login: "ezzy" }
            : {
                detail:
                  "This house link has expired. Ask Ezzy for a fresh one.",
              },
        );
        return;
      }
      const user = residents.find(
        (r) => req.headers.authorization === `Bearer mock:${r.slug}`,
      );
      if (!user) {
        json(res, 401, { detail: "Please sign in." });
        return;
      }
      if (route === "/me") {
        json(res, 200, { resident: lookup(user.id), house: "Highest House" });
        return;
      }
      if (route === "/week") {
        json(
          res,
          200,
          malformed
            ? { week: "broken" }
            : week(url.searchParams.get("start"), user),
        );
        return;
      }
      const match = route.match(
        /^\/visits\/(visit-\d+)\/(name|dispute|settle|verdict)$/,
      );
      if (match && req.method === "POST") {
        const v = visits(dishes, user).find((v) => v.id === match[1]),
          d = dishes.find((d) => d.id === v?.dish_ids[0]),
          b = await body(req);
        if (!d) {
          json(res, 404, { detail: "That dish is no longer here." });
          return;
        }
        const can = permissions(d, user);
        if (
          match[2] === "verdict" &&
          can.can_name &&
          ["not_dish", "clean", "duplicate", "cant_tell"].includes(b.verdict) &&
          (b.verdict !== "duplicate" ||
            (b.of_visit_id !== v.id &&
              visits(dishes, user).some((x) => x.id === b.of_visit_id)))
        ) {
          d.verdict = { kind: b.verdict, by: lookup(user.id), at };
        } else if (
          match[2] === "name" &&
          can.can_name &&
          lookup(b.resident_id)
        ) {
          d.verdict = null;
          d.resident = lookup(b.resident_id);
          d.named_by = lookup(user.id);
          d.named_at = at;
        } else if (
          match[2] === "dispute" &&
          can.can_dispute &&
          typeof b.note === "string" &&
          b.note.trim()
        )
          d.dispute = {
            state: "open",
            by: lookup(user.id),
            note: b.note,
            at,
            settled_by: null,
          };
        else if (
          match[2] === "settle" &&
          can.can_settle &&
          ["them", "not_them"].includes(b.decision)
        )
          d.dispute = {
            ...d.dispute,
            state: b.decision === "them" ? "upheld" : "overturned",
            settled_by: lookup(user.id),
          };
        else {
          json(res, 403, { detail: "This action isn’t available to you." });
          return;
        }
        for (const other of dishes.filter((x) => v.dish_ids.includes(x.id))) {
          other.verdict = d.verdict;
          other.resident = d.resident;
          other.named_by = d.named_by;
          other.named_at = d.named_at;
          other.dispute = d.dispute;
        }
        confirmed.add(d.id);
        json(
          res,
          200,
          visits(dishes, user).find((x) => x.id === v.id),
        );
        return;
      }
      json(res, 404, { detail: "Not found." });
      return;
    }
    const photo = pathname.match(
      /^\/dev\/photos\/(101|103|104|105|106|110|112|113)-(strip|later)\.jpg$/,
    );
    let filename;
    if (photo) filename = path.join(fixtureRoot, `${photo[1]}-${photo[2]}.jpg`);
    else {
      const requested = path.resolve(site, `.${decodeURIComponent(pathname)}`);
      if (
        !(requested === site.slice(0, -1) || requested.startsWith(site)) ||
        pathname.includes("..") ||
        pathname.startsWith("/dev/")
      ) {
        res.writeHead(404);
        res.end();
        return;
      }
      try {
        filename = (await stat(requested)).isFile()
          ? requested
          : path.join(site, "index.html");
      } catch {
        filename = path.join(site, "index.html");
      }
    }
    const types = {
      ".html": "text/html",
      ".js": "text/javascript",
      ".css": "text/css",
      ".svg": "image/svg+xml",
      ".woff2": "font/woff2",
      ".jpg": "image/jpeg",
      ".txt": "text/plain",
      ".json": "application/json",
    };
    res.writeHead(200, {
      "Content-Type":
        types[path.extname(filename)] || "application/octet-stream",
    });
    res.end(await readFile(filename));
  } catch (error) {
    console.error(error.message);
    if (!res.headersSent)
      json(res, 500, { detail: "The local fixture could not be loaded." });
    else res.end();
  }
});
server.listen(port, "127.0.0.1", () =>
  console.log(
    `Lane C mock: http://127.0.0.1:${port}/?mock=1\nCheckout: ${site}\nInvite: /join?mock=1#demo-invite\nPassword: house-demo`,
  ),
);
