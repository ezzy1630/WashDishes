---
version: 1
slug: "wash-dishes-static-index-html"
primary_target: "wash_dishes/static/index.html"
related_targets: []
---

# Dish Watch dashboard

Scope: the authenticated dashboard (Send, People, Live, Settings, Housemates, and the older Photos, Checks and History pages) plus sign-in. Mode: Operate. Send and People are used on an iPhone over Tailscale in sessions of under a minute; setup and Live on a laptop.

## Direction contract

THESIS: A kitchen accountability console. A dish left out is an incident with evidence, a clock when known, and a responsibility decision when supported. Refuses the generic NVR camera grid and the cream editorial "household" look.

OWN-WORLD: Graphite surfaces (#0c0e11 to #21262c), 12–14px radii, one-pixel inset rules. Mona Sans (wide numerals) with Geist Mono for times and IDs. Area colours amber/blue/green/violet mean place; resident accents mean person; red means past grace. Square-ish monograms, transit-line dish flow, dwell bars.

STORY: Open the dashboard, see the proof picture of each dish left out past two hours, tap who put it down if known, press Send and pick the house chat. Click into a person to see every picture named as theirs. Corrections (Not a dish, Wrong photos) are quiet and undoable. Identity and responsibility remain separate: a guess is outlined, never counted until someone taps the name.

FIRST VIEWPORT (Send, 390×844): top bar with Send, People and Live; "N dishes left out" and one line on what is still coming; the first proof picture full width; the pale Send button. Names and corrections follow just below the fold. With nothing to send, one panel says how many dishes are being checked or are still under two hours. People: a ranked, ruled list of named totals; one tap into a person's pictures. The older Photos feed and record pages remain on secondary routes.

FORM: Hybrid of mockups E/G/H refined to J (user-directed, after seed ebbd449a); Needs you moved to its own page by the user.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
