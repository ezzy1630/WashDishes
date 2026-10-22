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

This record covers Send (the home page), People, and the older incident feed and case/review page, with shared controls from the static app. Send and People are designed for an iPhone first: the proof picture is the page, and one pale Send button is the only loud control. It records the built palette rather than the slightly lighter range in the direction brief. Surface composition remains in `.impeccable/surfaces/wash-dishes-static-index-html.md`; product rules remain in `PRODUCT.md`.

**Key Characteristics:**

- Real full-scene evidence before attribution.
- Graphite layers, restrained borders and compact native controls.
- Readable muted metadata and explicit unknown states.
- One finished picture and one Send action; naming and corrections are quiet and below it.

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

Send is up to 1240px: a headline that counts the dishes ("2 dishes left out") with one line on what is still coming, then proof cards in a grid of columns at least 520px wide (one column on a phone, two on a laptop). Each card stacks the picture, Send, "Who put it down?" and the quiet corrections; cards are 48px apart. People is up to 720px: a ruled list of people with named proofs, then one muted line naming everyone else. A person's page reuses the Send grid with their pictures, then an Earlier list for entries whose photos have expired. Mobile gutters are 16px. The older Photos and record pages keep their earlier layouts on secondary routes.

## Elevation & Depth

Feed cards and the review panel are flat: tonal separation and thin borders provide structure without card shadows. Shared menus and the command palette use soft black shadows to distinguish overlays. Those shadows are not a card treatment.

Controls have short background and border transitions (120ms); the reduced-motion preference disables transitions and animations. There is no continuous decorative motion on the recorded surfaces.

## Shapes

Corners are gently rounded and functional. Proof pictures and the Send button use the send radius; name chips are full pills (chip radius). Shared cards use the card radius; incident cards use the smaller incident radius; the review panel uses the panel radius. Native review fields use the field radius and full collages use the media radius. Photographs remain rectangular. Chat exports use one consistent crop across four source frames; untouched full scenes remain available in the record. Incomplete records use text and one localized saved view, never a placeholder collage.

## Components

### Buttons

Compact, sentence-case actions. Standard and primary buttons are (34px) high with horizontal padding of (13px), medium weight and the button radius. Standard buttons use control-colored fills and outlined borders; hover raises the fill and strengthens the border. Primary saves use the pale action color. Quiet buttons have transparent resting backgrounds. Disabled actions reduce opacity to (0.45). A visible keyboard outline remains available.

### Cards / Containers

A Send card is the proof picture (it carries its own title, date and times, so the card adds no caption), a 52px pale Send button (full width on a phone, at most 320px otherwise), name chips and two quiet corrections. Chips are 38px outlined pills in alphabetical order; the camera's guess has a blue outline and fill with a small "guess" tag, and its reason sits under the chips in the review-text blue; the chosen name is a pale filled chip. A named card shows only that chip, Change, and an underlined link to the person's record. Picture boxes reserve the picture's aspect ratio so nothing jumps while it loads; the loading state is a blank card of that shape, never a spinner. On People, a row is a 40px monogram, the name with the day of the latest proof, the count, and a chevron. Times are 12-hour local, matching the picture.

### Inputs / Fields

Native selects, date-time inputs and textareas pair with visible labels. Review fields use the background fill, a thin divider-colored border, the field radius and (10px) padding. Selects and inputs have a minimum height of (42px) with automatic height and line height (1.4), so their text remains vertically legible. Labels and hints have an (8px) gap. Keyboard focus uses a visible pale outline.

### Navigation

The sticky header exposes Send, People and Live, which fit an iPhone's width; search is hidden there. The menu holds Settings, Housemates (adding people, face samples, personal links), Log dishes by hand and Sign out. Photos, Detection checks and Archive are reachable from search and old links only. Send and People fetch only their own small data; the full history loads when another page or search needs it.

### Evidence and correction

The normal photo page opens with no review form. Something wrong? exposes one correction choice and, only when identifying a person, a person selector. Original photos & video exposes retained originals, clips and a Detailed record link. Advanced review preserves the separate identity, responsibility, condition and outcome fields and stale-revision protection.

**The Evidence Boundary Rule.** Keep missing stages, uncertain attribution and washing-unknown outcomes explicit. A photograph, a nearby person or a disappearing dish is not a visual substitute for confirmed responsibility or washing.

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
