---
name: Dish Watch
description: Photo-led kitchen incidents and evidence review.
colors:
  background: "#0b0c0e"
  surface: "#111316"
  control: "#171a1e"
  raised: "#1e2227"
  divider: "#1f2328"
  border: "#2a2f35"
  border-strong: "#3a4048"
  text: "#e8eaed"
  text-secondary: "#a3aab2"
  text-muted: "#919aa4"
  action: "#eceef1"
  action-hover: "#ffffff"
  waiting: "#e0a548"
  late: "#ef5f55"
  confirmed-washed: "#4cb782"
  needs-review: "#7aa7ff"
  needs-review-text: "#a9c4ff"
  proof-mark: "#ffd400"
typography:
  headline:
    fontFamily: '"Mona Sans", system-ui, -apple-system, "Segoe UI", sans-serif'
    fontSize: "27px"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "-0.025em"
  title:
    fontFamily: '"Mona Sans", system-ui, -apple-system, "Segoe UI", sans-serif'
    fontSize: "19px"
    fontWeight: 600
    lineHeight: 1.35
    letterSpacing: "-0.01em"
  section:
    fontFamily: '"Mona Sans", system-ui, -apple-system, "Segoe UI", sans-serif'
    fontSize: "17px"
    fontWeight: 600
    lineHeight: 1.5
    letterSpacing: "-0.01em"
  body:
    fontFamily: '"Mona Sans", system-ui, -apple-system, "Segoe UI", sans-serif'
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.5
  field:
    fontFamily: '"Mona Sans", system-ui, -apple-system, "Segoe UI", sans-serif'
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: '"Mona Sans", system-ui, -apple-system, "Segoe UI", sans-serif'
    fontSize: "12px"
    fontWeight: 400
    lineHeight: 1.5
  headline-mobile:
    fontFamily: '"Mona Sans", system-ui, -apple-system, "Segoe UI", sans-serif'
    fontSize: "24px"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "-0.025em"
  intro:
    fontFamily: '"Mona Sans", system-ui, -apple-system, "Segoe UI", sans-serif'
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.5
  count:
    fontFamily: '"Mona Sans", system-ui, -apple-system, "Segoe UI", sans-serif'
    fontSize: "26px"
    fontWeight: 650
    lineHeight: 1.2
    letterSpacing: "-0.02em"
  mono:
    fontFamily: '"Geist Mono", ui-monospace, "SF Mono", Menlo, monospace'
rounded:
  field: "5px"
  media: "6px"
  panel: "8px"
  button: "9px"
  send: "12px"
  chip: "19px"
  incident: "10px"
  card: "14px"
spacing:
  small: "8px"
  compact: "12px"
  medium: "16px"
  content: "20px"
  grid: "24px"
  page: "28px"
  columns: "32px"
---

# Design System: Dish Watch

## Overview

Dish Watch uses a restrained graphite interface with real camera evidence as its dominant visual material. Mona Sans, compact controls, thin borders and quiet metadata support short kitchen-review sessions. The interface keeps uncertain observations visibly distinct from confirmed responsibility.

This record covers Send (the home page), People, and the older incident feed and case/review page, with shared controls from the static app. Send and People are designed for an iPhone first: weekly duty and visit review lead, with pale actions reserved for sharing results. It records the built palette rather than the slightly lighter range in the direction brief. Surface composition remains in `.impeccable/surfaces/wash-dishes-static-index-html.md`; product rules remain in `PRODUCT.md`.

**Key Characteristics:**

- Real full-scene evidence before attribution.
- Graphite layers, restrained borders and compact native controls.
- Readable muted metadata and explicit unknown states.
- One visit at a time, honest naming and quick alternative answers; finished results stay shareable.

## Colors

Neutral graphite carries the interface; pale actions and semantic accents identify decisions and state.

### Primary

- **Pale action:** Primary saves use the action background with dark background-colored text; hover becomes white.
- **State accents:** Amber denotes waiting, red denotes lateness, green denotes confirmed washing, and blue denotes review or attention in shared controls. State text accompanies color. The incident feed's ordinary state labels remain neutral; confirmed-late labels use red.

### Neutral

- **Background / surface / control / raised:** Ordered dark surfaces separate the page, containers, fields and hover states.
- **Divider / border / strong border:** One-pixel boundaries organize adjacent content and interactive controls.
- **Text / secondary / muted:** Main content, supporting information and compact metadata form a restrained hierarchy. The muted token is the scoped incident-page value, not the darker legacy root value.

**The Readable Metadata Rule.** Incident hints, timestamps, missing-media messages and inactive status tabs use the scoped muted text color. Do not inherit the legacy root muted value into these surfaces.

## Typography

**Body and headings:** Locally served Mona Sans with system sans-serif fallbacks. **Code and keyboard labels:** Locally served Geist Mono with monospace fallbacks.

The hierarchy is compact: a page headline, medium incident titles, smaller review section headings, then body and metadata. There is no oversized display tier. Headlines use tighter tracking; body copy keeps normal tracking. The implementation does not currently apply the mono font to incident IDs merely because they carry a `mono` class.

- **Headline:** Page titles use the headline role.
- **Title:** Incident cards use the title role, increasing to (21px) at the mobile breakpoint.
- **Section:** Review and video-context headings use the section role.
- **Body:** General interface text uses the body role; page introductions use the intro role and a maximum measure of (72ch).
- **Headline (mobile):** Send and People headlines drop to the mobile headline role below 760px.
- **Count:** People's per-person totals use the count role, proportional figures (Mona Sans's tabular figures have a slashed zero and slab one that read as code).
- **Field and label:** Review labels and explanatory copy use the field role; card metadata and field hints use the label role. Preserve sentence case.

## Layout

The operator home is up to 1080px, with 16px mobile gutters. A compact, ruled weekly summary precedes To review. One visit is expanded by default; on a phone its filmstrip spans the full content width with naming and four answers underneath. At widths above 760px, evidence and controls sit side by side. Advancing or undoing returns to the review heading below the sticky navigation. Ready to send follows, with weekly sharing and finished collages in two desktop columns, stacked on mobile. Week’s record keeps the complete review history in a disclosure; quiet camera health closes the page, with faults promoted above the weekly summary. People remains a 720px ruled all-time list; individual proof history retains the 1240px proof grid. The older Photos and record pages keep their earlier layouts on secondary routes.

## Elevation & Depth

Feed cards and the review panel are flat: tonal separation and thin borders provide structure without card shadows. Shared menus and the command palette use soft black shadows to distinguish overlays. Those shadows are not a card treatment.

Controls have short background and border transitions (120ms); the reduced-motion preference disables transitions and animations. There is no continuous decorative motion on the recorded surfaces.

## Shapes

Corners are gently rounded and functional. Proof pictures and the Send button use the send radius; name chips are full pills (chip radius). Shared cards use the card radius; incident cards use the smaller incident radius; the review panel uses the panel radius. Native review fields use the field radius and full collages use the media radius. Photographs remain rectangular. Chat exports use one consistent crop across four source frames; untouched full scenes remain available in the record. Incomplete evidence stays reachable in records and Live; it does not occupy the home. Unavailable tracking is never described as a clean kitchen.

## Components

### Weekly kitchen duty

Send begins with This week: a native week select, current duty, compact top-three standings, any additional scorers, a zero-points line and the next pick/threshold sentence. Open weeks remain provisional. Closed weeks say that points are frozen. Duty in the dashboard describes the current assignment; a shared closed result retains that week’s duty outcome.

Visit review leads with “N clear ones to name” and “Unnamed visits simply don’t count.” A quiet, 44px-tall “+M with weak evidence” disclosure reveals optional weak visits after strong/fair ones. The card labels its evidence tier and explains weak evidence in plain words. Closed weeks show history, without a pending weak backlog. Visit review uses arrival filmstrips or a saved-photo fallback. Name chips remain alphabetical and at least 44px tall; the camera suggestion is outlined and its reason is a complete sentence. A guess never scores by itself. The question is “Who used or left these dishes?” Missing arrivals say “Arrival not recorded. Only name someone if you know.” Settlement buttons say It was them and Not them. Not a dish, It's clean, Same as another (with a labeled original-visit select), and Can't tell are a two-column grid of 44px minimum targets. Can't tell explains its private hide/no-label behavior.

Each answer advances immediately with a five-second inline Undo receipt; several quick answers can be pending independently. Sharing pauses until they save. Errors restore the affected visit and name the failed save. Week’s record retains named visits, disputes, excluded visits, actor/time and operator Undo answer for open weeks. Full-size photos and each dish’s record remain one tap away. A caught-up state never claims that the kitchen is clean.

The weekly share image is a 1080px JPEG, drawn server-side with the collage typography and graphite palette. Its height follows content. It shows the week, standings, next-duty outcome/reason, thresholds, the house site URL and a visible provisional label on open weeks. The dashboard, image and house site use the API's structured pick: one name in the heading, followed by the name-free reason. They never rebuild the decision from standings. With no finished proofs, the entire proof block disappears; Inspect dishes in Live stays in the health line. The image preview loads on request; iOS file sharing preloads the JPEG so the tap can open the share sheet. Unsupported browsers download it. Demo/snapshot exports are marked.

The home uses readable metadata (`#919aa4`), 24px page title, 19px section headings, 16px visit headings, 13–14px controls/notes and existing graphite surfaces. No shadows or continuous animations. Only the app’s existing dark theme ships.
Setup adds Kitchen duty with enabled, pan/pot/other weights, minimum points/lead
and a separate house-vote duty form. Housemates shows account status and Make
sign-in link inline, then a read-only copy field with seven-day/single-use wording.

### Buttons

Compact, sentence-case actions. Standard and primary buttons are (34px) high with horizontal padding of (13px), medium weight and the button radius. Standard buttons use control-colored fills and outlined borders; hover raises the fill and strengthens the border. Primary saves use the pale action color. Quiet buttons have transparent resting backgrounds. Disabled actions reduce opacity to (0.45). A visible keyboard outline remains available.

### Cards / Containers

A Send card is the proof picture (it carries its own title, date and times, so the card adds no caption), a 52px pale Send button (full width on a phone, at most 320px otherwise), name chips and two quiet corrections. Chips are 38px outlined pills in alphabetical order; the camera's guess has a blue outline and fill with a small "guess" tag, and its reason sits under the chips in the review-text blue; the chosen name is a pale filled chip. A named card shows only that chip, Change, and an underlined link to the person's record. Picture boxes reserve the picture's aspect ratio so nothing jumps while it loads; the loading state is a blank card of that shape, never a spinner. On People, a row is a 40px monogram, the name with the day of the latest proof, the count, and a chevron. Times are 12-hour local, matching the picture.

### Inputs / Fields

Native selects, date-time inputs and textareas pair with visible labels. Review fields use the background fill, a thin divider-colored border, the field radius and (10px) padding. Selects and inputs have a minimum height of (42px) with automatic height and line height (1.4), so their text remains vertically legible. Labels and hints have an (8px) gap. Keyboard focus uses a visible pale outline.

### Navigation

The sticky header exposes Send, People and Live, which fit an iPhone's width; search is hidden there. The menu holds Settings, Housemates (adding people, face samples, personal links), Log dishes by hand and Sign out. Photos, Detection checks and Archive are reachable from search and old links only. Send and People fetch their own data and compact operator status once a minute; the full dashboard/history loads when another page or search needs it.

### Evidence and correction

The normal photo page opens with no review form. Something wrong? exposes one correction choice and, only when identifying a person, a person selector. Original photos & video exposes retained originals, clips and a Detailed record link. Advanced review preserves the separate identity, responsibility, condition and outcome fields and stale-revision protection.

**The Evidence Boundary Rule.** Keep missing stages, uncertain attribution and washing-unknown outcomes explicit. A photograph, a nearby person or a disappearing dish is not a visual substitute for confirmed responsibility or washing.

### House site (housemates' phones)

The Vercel site at highest-house.vercel.app is a separate surface with its own
look, chosen 2026-11-07: it should feel like a native iPhone app. It uses the
system font stack (SF on Apple devices), iOS system backgrounds and grouped
inset lists, 12px card radius, 0.5px separators, one blue tint (#0a66d6 fill;
#0056c2 light-mode text and #4da3ff dark-mode text for contrast), and follows the
device's light/dark setting. A translucent tab bar holds This week, Dishes (with
a count badge), Footage and Me; the large page title collapses into a centered
bar title on scroll. Secondary answers, the duplicate picker, disputes and How it
works open as bottom sheets with a grabber. Naming keeps the photo first, with a
docked name grid and Can’t tell / Something’s off above the tab bar. Pressed
states scale slightly; motion is limited to page pushes, answer slides, sheet
rise and the Undo pill, all disabled under reduced motion. Copy avoids system
words (visit, evidence tier, chunk, operator) in favour of plain ones. The
graphite tokens above remain the operator dashboard's system.

## Do's and Don'ts

### Do:

- **Do** keep original full scenes accessible, and use a consistent, clearly contextual crop for the chat image.
- **Do** use the scoped readable muted token for incident metadata and field hints.
- **Do** pair semantic state color with explicit text.
- **Do** retain native labeled controls, visible focus and reduced-motion behavior.
- **Do** stack evidence before review on narrow screens.

### Don't:

- **Don't** invent or substitute imagery for missing household evidence.
- **Don't** turn unknown observations into named responsibility or confirmed washing.
- **Don't** promote the legacy root muted color or ornamental eyebrows into new surfaces.
- **Don't** add card shadows or continuous decorative animation to these recorded surfaces.

Proof picture (2026-10-22): a 1600 px JPEG under 600 KB, made to be read as a small image in a group chat. Dark canvas (#101214), white title at 56–84 px ("Sam left this pan out", or "Pan left out"; a long name is shortened, never the part saying what was left), date and place at 44 px, then a 2×2 grid of 4:3 photos with 50 px bold captions and 46 px muted times below each: Before, Putting it down (or Leaving it), Left there, and Still there N hours later in the proof-mark yellow. All four photos share one crop around the dish (always inside) and the head and shoulders of whoever put it down. The dish is marked in proof-mark yellow over a black outline, at least 64 px: dashed in Before, corner brackets while it is being put down, solid after. Setup notes such as "(visible portion)" are dropped from place names. Cached pictures carry the design version in their file name.


Arrival review (2026-11-03): a visit gets one JPEG, 1200 px wide and below 600 KB, with six chronological panels in a 2 columns and 3 rows. One contextual crop includes every member dish and nearby people's heads and torsos; yellow corner marks locate all dishes in every panel. The header names the dish, date and area; a time sits under each photo. Metadata frames follow the displayed row-major order. Occluded spot views are useful context here. A missing arrival says “Already there at [time] — arrival not recorded” on the image itself; the nearest visitor is never substituted for the placer. This is human review material, independent of the strict Send picture and its thresholds.

The shared image is one ordinary `evidence` row (`kind='filmstrip'`, `label='arrival'`, seven-day expiry). `arrival_visits` stores its visit id, evidence id and metadata; `arrival_visit_members` links each obligation to that visit. Every member's formatted `evidence` includes that same row, and `arrival_visit_id` links to `Store.arrival_visit()`. That method returns current consensus `suggested_resident_id` and the existing per-member `suggestion_basis`; no consensus means no guess. Metadata contains `visit_id`, `obligation_ids`, `canonical_obligation_ids`, `duplicate_of`, `dish_boxes`, `window`, and `frames: [{at, person_boxes}]`. Coexisting overlapping whole/rim/sliver records share a physical-dish slot; retain all aliases and count only canonical ids after the usual dirty/owner review. Brief overlap from a transfer and separate non-overlapping spots do not deduplicate. The visited person must remain continuous for separate physical dishes to share a naming unit. `window.arrival_recorded=false` and `first_visible_at` explicitly mark already-present dishes. Consumers using SQL join members → visits → evidence; select only `expired=0 AND expires_at>now`. Filmstrip generation never names a person or changes a late verdict. A failed attempt stores a plain reason in `filmstrip_jobs` without fabricating an evidence image.

`left_out_at` is an observed presence/follow-up crossing, independent of ownership or dirty-condition review. It does not itself unlock named statistics. A passed timer, a late disappearance or a post-gap sighting cannot manufacture that observation. `repair-left-out --since YYYY-MM-DD --dry-run` reports automatic-dismissal restorations by deposit date in the configured local timezone, plus stale opens of every age; omit `--dry-run` to apply on the explicitly selected data root. Human dismissals remain untouched. The default Python repair function is dry-run, and repeating the same repair is idempotent.

Camera closures persist `closure_kind` (`removed` or `missing`) separately from editable reason text. Confirming a person with a new note cannot turn an unknown outcome into washing or an on-time success. Human washing confirmation clears that machine closure.

Legacy geometric aliases are conservative review metadata: their observed intervals must overlap for at least 30 minutes, their record starts must be within one hour, and their spots must overlap. Complete nested vessels are protected from rim/sliver matching. Wider time gaps and ambiguous geometry remain separate for review; Lane G's source tracking remains necessary. These aliases do not certify dirty condition or responsibility. Segment-mtime plus decoded PTS provides approximate wall-clock timestamps (`timestamp_estimated=true`); original camera overlays remain the exact-clock reference.
