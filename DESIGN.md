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
  mono:
    fontFamily: '"Geist Mono", ui-monospace, "SF Mono", Menlo, monospace'
rounded:
  field: "5px"
  media: "6px"
  panel: "8px"
  button: "9px"
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

This record covers the implemented incident feed and case/review page, with shared controls from the static app. It records the built palette rather than the slightly lighter range in the direction brief. Surface composition remains in `.impeccable/surfaces/wash-dishes-static-index-html.md`; product rules remain in `PRODUCT.md`.

**Key Characteristics:**

- Real full-scene evidence before attribution.
- Graphite layers, restrained borders and compact native controls.
- Readable muted metadata and explicit unknown states.
- A photo and Download action, with corrections and originals in closed disclosures.

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
- **Body:** General interface text uses the body role; page introductions use (15px) and a maximum measure of (72ch).
- **Field and label:** Review labels and explanatory copy use the field role; card metadata and field hints use the label role. Preserve sentence case.

## Layout

The Photos page has centered content up to 1240px in two columns: Out now (current cropped camera picture with numbered boxes, then the numbered dish list) at about 60%, and Ready to share (finished photo cards) beside it. At 900px it becomes one column with Out now first. Photo detail is up to 1040px, with Download beside its heading, the image, then closed correction and original-media disclosures. Incomplete detail places the cropped saved view beside the status and stage list, stacking below 900px. A correction form is at most 460px wide. Mobile gutters are 16px. The advanced record remains a secondary route with the earlier two-column review layout.

## Elevation & Depth

Feed cards and the review panel are flat: tonal separation and thin borders provide structure without card shadows. Shared menus and the command palette use soft black shadows to distinguish overlays. Those shadows are not a card treatment.

Controls have short background and border transitions (120ms); the reduced-motion preference disables transitions and animations. There is no continuous decorative motion on the recorded surfaces.

## Shapes

Corners are gently rounded and functional. Shared cards use the card radius; incident cards use the smaller incident radius; the review panel uses the panel radius. Native review fields use the field radius and full collages use the media radius. Photographs remain rectangular. Chat exports use one consistent crop across four source frames; untouched full scenes remain available in the record. Incomplete records use text and one localized saved view, never a placeholder collage.

## Components

### Buttons

Compact, sentence-case actions. Standard and primary buttons are (34px) high with horizontal padding of (13px), medium weight and the button radius. Standard buttons use control-colored fills and outlined borders; hover raises the fill and strengthens the border. Primary saves use the pale action color. Quiet buttons have transparent resting backgrounds. Disabled actions reduce opacity to (0.45). A visible keyboard outline remains available.

### Cards / Containers

Photo cards are the complete 1200px export (it carries its own title and times) followed by Details and Download; the card heading is screen-reader only. The image keeps its intrinsic aspect ratio. Active records are numbered rows under Out now, not image cards. Numbers are yellow when the dish is boxed in the current picture, outlined when it is not detected there, and neutral when no current picture exists. Photos-surface times are 12-hour local, matching the export. The detail page presents the same image with a single primary Download action. The old review panel exists only on the explicit Detailed record route.

### Inputs / Fields

Native selects, date-time inputs and textareas pair with visible labels. Review fields use the background fill, a thin divider-colored border, the field radius and (10px) padding. Selects and inputs have a minimum height of (42px) with automatic height and line height (1.4), so their text remains vertically legible. Labels and hints have an (8px) gap. Keyboard focus uses a visible pale outline.

### Navigation

The sticky header exposes Photos, Live and Settings. People, detection checks and Archive are in the secondary menu. No attention-count badge is attached to Photos: incomplete observations must not look like finished results.

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

Current share workflow (2026-10-17): only four available, ordered, distinct, localized views with an actual two-hour interval and matching left/later positions enter Photos. Rejected, stale, disputed, clean, archived and continuity-uncertain records are excluded from the share endpoint as well as the grid. Stages that are not attached remain visible in tracking, worded as not attached rather than missing footage; this presentation gate does not recover footage or prove responsibility. Complete exports are 1200px JPEGs with a dish/place header, the left-out-to-later interval, four on-photo labels with source times, dish boxes and one brief observation-limit line. Long dish lists are shortened before the place is. People are named only from recorded identification.
