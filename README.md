# Dish watch

A local dashboard for a shared kitchen. It records dish deposits, starts a two-hour cleanup deadline, saves evidence, supports ownership and cleanup review, and keeps corrected history in SQLite. No hosted database, model API, or messaging service is required.

Camera proposals start with **human confirmation**, and the camera only names owners on its own once its suggestions are measured accurate (see *Accountability* below). A dish detector cannot establish that a plate is dirty or that a particular person owns it merely because they are nearby. A transition through the sink into the clean zone is a cleanup proposal; it does not automatically resolve the record. Deadlines can pass without supporting a confirmed overdue incident. Unknown outcomes are excluded from the overdue rate.

## Current status

The camera, local recording, GPU inference, and evidence downloads work on the tested Jetson. Automatic four-stage evidence for every dish is **not yet reliable**. A 762-frame saved-footage replay at 1280-pixel inference resolution still fragmented dish tracks and produced no complete share-ready sequences. Tests establish software behavior; they do not establish household accuracy. Do not use nearby-person detections as proof of responsibility.

This repository contains application code, tests, reusable scripts, and documentation. Household footage, face data, credentials, databases, model weights, screenshots, and machine-specific bootstrap artifacts stay outside Git.

## Try it locally

Python 3.10 or later is required; Python 3.12 is the tested Mac environment. Setup constrains core dependencies to the tested versions in `requirements-core.lock`. No Node build is needed. From this directory:

```sh
scripts/setup-local.sh
scripts/run-local.sh --demo
```

Open [the dashboard](http://127.0.0.1:8787) and use access key `demo`. The demo contains fictional housemates and clearly labeled synthetic evidence. Its entire database, recordings, and identity data are separate from live mode. `Open Dish Watch.command` launches the demo on macOS.

To start live mode:

```sh
scripts/run-local.sh
```

In another terminal, retrieve the generated household key:

```sh
WASHDISHES_DATA_DIR="$PWD/.data" .venv/bin/python -m wash_dishes access-key
```

The default run script stores data in `.data/live` and `.data/demo`. The installed CLI defaults to `~/.local/share/washdishes/live`; set `WASHDISHES_DATA_DIR` consistently when changing that location. An optional `WASHDISHES_ACCESS_KEY` environment variable replaces the generated key. Do not commit it. Sessions expire after twelve hours and require login again after a service restart.

## Use the dashboard

Overview shows pending deadlines, past-due ledger entries, and events requiring review. Add a manual deposit, inspect its evidence, confirm or correct responsibility, or mark it clean and record the cleaner. A resolved event can be corrected with an earlier cleanup time. Disputes and dismissed records are excluded from named statistics. Reassignment preserves the original observation and updates the derived totals.

Each incident opens an evidence timeline: handling, left unattended (or first seen for backlog), past the deadline, and outcome. Full camera frames stay primary, with a linked handler detail crop and expandable original event clips. Captures carry dates, seconds, and the browser's time zone. A human photo identification is separate from confirmed responsibility. Missing or expired captures, observation gaps, and human cleanup decisions remain explicit; a passed timer or vanished dish never becomes camera proof of not washing. The activity log and all attached originals remain available below the timeline.

Stable, unassigned dishes also get one after-deadline photo and a clip request per timer, before ownership review. Named overdue statistics still require confirmed ownership. The saved photo establishes presence at that observation; it does not establish continuous visibility or washing quality. Reviewed gaps pause these captures until continuity is settled. Clip export remains subject to completed camera segments and retention.

History filters by housemate, status, and deposit date. The server uses `America/Los_Angeles` for date boundaries by default; set `WASHDISHES_TIMEZONE` to another IANA timezone if needed. Housemates manages active residents and their local face samples. Archiving a resident removes their enrollment and preserves historical records.

Setup controls the deadline, camera, zones, retention, and recording budget. Changing the deadline affects new deposits; existing recorded deadlines remain as they were. The default grace period is 120 minutes. Camera candidates start at the first stable unattended observation after nearby activity ends; this is an observed lower bound, not proof of when the dish became dirty. Taking a dish from storage does not start a timer.

Someone standing near an already tracked dish does not restart its two-hour photo timer. Earlier-photo recovery retries when new retained originals arrive and checks the activity journal before sparse training captures. Ambiguous neighboring dishes are not interchangeable matches. The downloaded panel says **Activity** because a nearby person does not establish handling; a person's reviewed name stays attached to that specific photo when later recovery replaces the panel.

## Connect the Tapo C113

Complete onboarding in Tapo, then create the separate local **Camera Account** under Advanced Settings. In Setup, enter the camera's private IP or local hostname, the Camera Account username/password, and `stream1`. These are not the TP-Link account credentials. Credentials remain in the private local database and are excluded from API responses and activity logs.

FFmpeg and go2rtc must be installed. go2rtc (v1.9.14, in the service's private tools directory) holds the only camera connection; its config contains the camera credentials and is written with mode 0600, so they never appear on a command line. The recorder reads the relay over loopback, copies H.264 into ten-second recording segments, and produces a full-resolution JPEG analysis stream at `analysis_fps` (default 4). The browser's live video is the relay's fragmented MP4, proxied through the authenticated dashboard. Audio is not recorded. The portable preview decoder runs on the CPU; hardware decoding and higher analysis rates must be benchmarked against the actual JetPack build before enabling a different decoder. Compressed recording does not reencode the source video. The Jetson service uses full Ubuntu FFmpeg 6.1.1 binaries in `<checkout>/.tools/bin`, with their three missing shared libraries installed from Ubuntu. NVIDIA's system FFmpeg 8.0.1 is preserved; that build disables networking, RTSP, segmentation, and software scaling, so it cannot run this camera pipeline. The service places its private tools first in `PATH`.

Use a fixed camera view that shows faces, the dirty landing area, sink, and clean destination. Calibrate named rectangles in Setup for every visible landing area, sink, clean rack, and storage cabinet. Clean/storage rectangles override sink/dirty rectangles where they overlap. A rectangle can also carry an `outline`, a polygon of 3–24 points in the same coordinates: a dish then belongs to the area only if the point it rests on is inside the outline too. The counter's front edge runs diagonally across this camera's view, so the counter rectangles are trimmed to the counter top and don't claim cabinet doors or an open cabinet. Outlines are set in the settings data and follow camera shifts with their rectangle; redrawing a rectangle in Setup drops its outline. Closed cabinets and off-camera areas remain unobservable. Mask unrelated areas in Tapo and verify those masks in the actual feed. Reconnects and service starts are recorded as observation gaps. Neither a disappearance nor a gap automatically clears dishes or creates an accusation.

Small camera shifts (a bump, a draft) are followed automatically. Every two seconds, when nobody is in view, the frame's edge image is compared with a reference view by phase correlation. People are skipped because they drag the reading with them: one evening of footage had a person standing at the counter lock a false 3% shift, and kept readings unusable for 20 minutes at a time. When the reference stops matching (after dark, say), a recent empty view saved with its confirmed offset (`view-reference-anchor.npz`) stands in for it, so tracking survives a restart at night. Once five readings agree, zones and tracked dishes move by the measured offset; item tracking pauses only while such a shift is unconfirmed. An unreadable view never pauses tracking: after ten minutes of unreadable empty views the current view becomes the anchor at the current offset, and a `camera_view_changed` event asks you to check Setup in case the camera really moved. Readings that agree on a shift beyond 10% of the frame mark the camera as moved. The dashboard then warns, and item tracking stays paused until the zones are saved again in Setup or `POST /api/view/confirm` accepts the current view. Saving zones always resets the reference to the current picture.

## Prepare local vision

The API and manual dashboard work without the optional vision dependencies. On the tested Mac/Python 3.12 environment:

```sh
.venv/bin/python -m pip install -r requirements-model-setup.txt
WASHDISHES_DATA_DIR="$PWD/.data" .venv/bin/python -m wash_dishes models all
```

The explicit setup command downloads checksum-verified OpenCV YuNet/SFace, YOLOWorld, and a setup-only CLIP text encoder. It caches a nine-class vocabulary (person, plate, bowl, cup, fork, knife, spoon, pot, pan), removes the text encoder from the saved detector, and verifies the saved graph. After preparation, runtime loads local files only and does not install packages or download models. The runtime assets are `dishes-world.pt`, `face_detection_yunet_2023mar.onnx`, and `face_recognition_sface_2021dec.onnx` beneath the selected live data root's `models` directory. CLIP and the original World checkpoint can be removed after setup if storage is limited; retain the three runtime assets.

On Jetson, install Torch/torchvision versions matching the installed JetPack and GPU first, then verify actual CUDA convolution and inference. Do not blindly install Mac Torch wheels or replace a working CUDA Torch with a CPU wheel. The deployed Orin Nano uses JetPack 7.2.1 / CUDA 13.2 with official PyTorch `torch==2.14.1` and `torchvision==0.29.1` from `https://download.pytorch.org/whl/cu132`. Its virtual environment inherits the system packages, including TensorRT, and uses the pinned portable vision dependencies above. CUDA convolution, CUDA NMS, and the prepared detector ran successfully on the GPU without network calls. CUDA 13.2 adds unified Arm SBSA support for Orin; do not substitute earlier CUDA wheels based solely on the architecture name. See [NVIDIA's CUDA 13.2 announcement](https://developer.nvidia.com/blog/cuda-13-2-introduces-enhanced-cuda-tile-support-and-new-python-features/) and [PyTorch installation guidance](https://pytorch.org/get-started/previous-versions/).

Local detector callbacks cap CPU inference at two threads after predictor setup, including lazily loaded crop and teacher models. This leaves capacity for recording and the dashboard without changing image size or detection thresholds. The live latency covers detection plus inventory/detail checks; `detector_latency_ms` is available separately. A CUDA-enabled installation falling back to CPU reports the unavailable GPU in camera status. On October 17, the Jetson's GPU was unavailable following an ACR firmware `-40` boot failure; earlier successful CUDA checks do not establish the current boot's GPU health. After approval, `firmware-path.service` was installed to select `/usr/lib/firmware` before GPU initialization. One reboot restored CUDA; GPU allocation/readback, actual detector inference, live recording, and the authenticated HTTPS collage download passed. The service now runs on `cuda:0`; warmed 1280-pixel detector inference measured about 61 ms and live analysis about 4 fps. The startup unit is versioned at `scripts/jetson/firmware-path.service`; repair receipts and comparisons remain private. This fixes the current boot and persists the workaround; it does not establish all-dish handling accuracy.


Prepare the portable checkpoint on this Mac and copy the three runtime assets to the Jetson, or run the setup command on a compatible local environment. TensorRT `.engine` files are supported but must be built on the matching deployment GPU/runtime and retain the required class names. A CUDA `.pt` model is a valid initial path.

Enable local vision in Setup after installing the models. Use the default model path or select trusted custom `.pt`/`.engine` weights with the nine required classes.

**How dishes are tracked.** Dishes only move when someone handles them, so the inventory works from changes rather than re-identifying every dish in every frame. Each frame, the detector only needs to find people. An area is *busy* while a person overlaps it or something in it is moving. It is *settled* three seconds after that stops. When an area settles after being busy, its edge picture is compared with the last settled one. The dish detector examines each changed area with context, merging duplicate boxes across views. A dish found where the picture changed was put down. A known dish missing where the picture changed was picked up. Detector mistakes in parts nobody touched (a missed dish, a phantom, a pot renamed a bowl) are ignored. Pickups are processed before arrivals across all settling areas, so area order does not break transfers. A dish picked up in one area and put down in another within 30 minutes can keep its history, preferring the same kind and the same person. Different tracked handlers do not establish a handoff, and missing track IDs do not establish a shared handler. So counter → sink → rack carries one item through to a cleanup proposal. A sudden change over most of an area (lights switched) only refreshes positions. Walking past without moving anything adds no detector pass; settled areas are rechecked once per minute.

A dish left in a landing area or the sink becomes a review candidate once its area has stayed settled for 30 seconds, timed from when the person left. After a start or reconnect, each area's first settled look is reconciled with the saved inventory. Dishes that were already out become backlog with unknown owner and deposit time, no deadline and no named statistics. Review never asks about them, since no clip shows who left them; once one is gone it closes itself, and its full record can still start a timer. Other saved dishes that are gone are flagged for review, never counted as washed. Which of two similar dishes carried at once is which is asked only when one of them already has an owner. Review leads with the clip of the dish being put down. Every dish the live inventory sees put down or picked up is logged, so `evaluate` scores the running system against the same hand-checked truth as a replay. While an area stays settled, its open items receive continuing evidence only when a fresh detection matches the saved dish. Unchanged pixels alone do not verify a deadline. A clean dish taken from a rack or cabinet and left out is a new use, with a photo of where it came from.

Disappearance never means washed. Moving through the sink into a rack or storage only proposes cleanup; a person must confirm it. Two similar dishes picked up together and one put down cannot be told apart, so the affected items are flagged for review. Cleanliness, washing quality and ownership cannot be established from presence alone. Inventory is bounded at 128 dishes.

Enroll each consenting resident with several clear **JPEG** views or capture a face from the connected camera. Exactly one usable face must be visible. Up to twenty numeric embedding samples per resident are stored locally; source enrollment photographs are not retained. Validate recognition, false dish proposals, and cleanup transitions in this kitchen before relying on the history. Similarity scores are not calibrated probabilities.

## Live view, owner suggestions, and training

**Live** (the default page) plays the camera stream with boxes drawn on top: dishes as the inventory holds them, coloured by area (amber counters, blue sink, green racks, violet storage), people as white brackets with walking trails, open items with countdowns, per-area counts and busy/settled state, and a one-hour trend.

**Sink fullness.** In Setup, open each zone while it is empty and press *This area is empty now*. The analyzer compares the zone's texture with that reference (people are masked out), so a pile of plates registers even when individual plates can't be separated.

**Who left it.** When a dish is left, the person last in that area is recorded: a zoomed photo of them, the scene, a short clip of the put-down, and a photo of where the dish was taken from if that was a rack or cabinet. Each event card asks *Whose was it?* with one-tap housemate chips. Every sighting stores identity cues, and each confirmation labels them:

- **Face**: looked for in the upper half of the person's photo, at least 40 px wide, and compared with enrolled faces and with faces from named sightings. With `models/face_recognition_arcface_r50.onnx` present (InsightFace buffalo_l `w600k_r50`, non-commercial licence) faces are ArcFace embeddings; otherwise SFace. On this kitchen's ceiling camera SFace could not tell housemates apart; ArcFace can, weakly. `python -m wash_dishes add-faces` adds faces to saved sightings from their stored photos.
- **Head and hair from above**: this lasts across days.
- **Outfit**: strong evidence only against sightings within 16 hours of the event, because clothes change daily. A repeated outfit on another day is a small hint, and a different one counts for nothing.
- **Stature**: how high the head was when standing at the same area.

The cues are added up per housemate. A suggestion such as *Looks like Alex from face, today's outfit* appears only when one housemate clearly leads. A strong face match also names that sighting's outfit provisionally, so the rest of the day's sightings in the same clothes can be recognised without a face. Cue weights are uncalibrated starting points; the suggestion accuracy shown under Housemates is the real measure. Several dishes left by the same person within three minutes become one event. Confirming or correcting a person also refreshes suggestions for waiting camera events with saved appearance cues, including older footage. Dismissing or disputing a teaching example removes suggestions it no longer supports. These updates do not confirm ownership, start backlog timers, or count anyone overdue.

**Teaching the detector this kitchen.** The analyzer keeps a bounded set of frames (default 3 GB) when counts change, when events open, and every half hour. A large offline teacher (YOLO-World-X, too slow for live use) pre-boxes them, so labelling is mostly correcting. On the Training page, fix boxes (drag to draw, 1–9 to change kind, Delete to remove, Enter to save). Frames from evaluation windows are marked as held out and never used for training. On the Jetson:

```sh
cd ~/dishwatch
.venv/bin/python -m wash_dishes models teacher       # once, needs the setup requirements (CLIP)
.venv/bin/python -m wash_dishes teach --footage DIR --skip START/END   # sample frames from footage, pre-box all unlabelled frames
.venv/bin/python -m wash_dishes train [--with-teacher]               # fine-tune YOLO26s
```

`train` fine-tunes a checksum-pinned YOLO26s on the labelled frames, scores both the tuned and the current model on held-out frames, and prints both. `--with-teacher` also trains on unreviewed teacher boxes. That distils the teacher into a fast closed-set model, but the student will not be more accurate than the teacher; corrected labels are what improve accuracy. Training needs several GB of memory and cannot run beside live analysis on the 8 GB Orin Nano. Train on another machine, or pause monitoring.

The tuned model is only used for the per-area pass when an area settles; people and full frames stay with the stock detector, because the kitchen labels contain few people. Score it with `evaluate --dish-detector PATH`, and to use it, copy it (or its TensorRT export) to `models/dishes-kitchen.pt` or `models/dishes-kitchen.engine` and restart.

**Measuring accuracy.** `evaluate` replays recorded footage through the current pipeline in a scratch database and scores it against hand-labelled truth: which dishes were put down or picked up, where and when.

```sh
.venv/bin/python -m wash_dishes evaluate truth.json --footage DIR [--detector PATH] [--dish-detector PATH] [--out report.json]
```

It reports precision and recall for every put-down and pick-up, the same for dishes left in landing areas and the sink, and the latter for what the deployed system actually reported in that window. `scripts/truth_sheets.py` renders before/after sheets of every period of activity in each area, found by frame differencing rather than the detector, to make labelling the truth practical.

## Accountability: clearing, camera naming, alerts and proof

**Taken away in time.** A dish whose last movement is a pickup before its deadline, with no put-down in view for 30 minutes, is closed as *cleared*: not late, not washed, excluded from rates. Reopening it keeps it open. A dish carried into a spot the detector misses (a stacked sink) is cleared too, so this errs toward not accusing anyone.

**Camera naming.** Once the last 50 human-checked suggestions include at least 30 and are at least 90% right, a new suggestion on a camera record less than a day old names the owner directly (`attribution = 'camera'`). Every fifth record stays in Review as a spot check, because corrections of camera-named records alone would record only mistakes; camera-named records are left out of the accuracy measure, and their appearance samples are not used for learning until a person confirms them. If accuracy drops, naming stops. It can be turned off under Settings → Owners and alerts. Camera-named owners count in statistics, show "by camera", and can be disputed.

**Alerts.** With an ntfy topic set under Settings → Owners and alerts, the service sends one push when a confirmed dish is seen still out after its deadline, and a per-person summary on Sunday at 18:00 household time. Messages carry names, dishes, places and times, never photos. Anyone who knows a public ntfy.sh topic can read it; use a long random topic or a self-hosted server. Alerting starts from the moment a topic is saved; progress is kept in `notify-state.json`.

**Proof.** Every record has *Download proof* (and the Now page lists late dishes with a *Proof* button): a ZIP with a readable page, the original photos and clips, the full activity log, what the evidence does and does not establish, and `record.json` with a SHA-256 for each file.

## Trust and self-improvement

**Live investigation.** Open **Live**, choose a dish in the picture or current-dish list, and use **Find earlier use**. The search checks completed recordings still in the rolling buffer, samples at most 360 views, and returns up to 12 windows with changes in the selected area. A before/after change can be a person, lighting, or camera motion; it does not establish a dish match or responsibility. Review the clip, then **Keep candidate** to copy its snapshots and video into the selected record. Ownership and deposit time are unchanged. Missing footage and gaps are explicit; recording times are estimates, with the original camera overlay retained. Temporary search files are bounded to eight searches / 256 MB / seven days; kept incident evidence uses the existing evidence retention rules. Searches use one low-priority CPU worker and do not start another GPU model.

**Correcting misses and false outlines.** In Live, choose **Use for training** or **Reserve for testing**, then **Freeze & correct**. The editor opens the exact analyzed frame, not a later camera snapshot. Add missed dishes, delete false outlines, correct kinds, and check every visible box before saving. This saves labels, not an ownership or cleanup decision. A testing capture reserves the surrounding 30 minutes, including existing and future neighboring samples and verifier-review examples, from training. Original detector predictions remain unchanged. Live's **Detection checks** reports missed dishes and extra outlines against completed reserved stills at IoU 0.5; kind accuracy is scored separately. These are detector measurements, not ownership or washing accuracy. The offline improvement command also scores current and candidate models on the same reserved stills and refuses promotion if the candidate adds misses or false outlines, even when its recorded-event replay improves. No retraining runs alongside live inference.

Run `scripts/check-js.sh` for browser-module syntax checks and the JavaScript behavior tests; it forces ES-module parsing rather than relying on Node's automatic `.js` detection.

**Current inventory.** Now counts dishes currently tracked on counters, the stove, and in the sink, including dishes whose owner is unknown. Older unresolved incidents remain in History and do not stand in for the current count. An empty detection result means nothing is tracked, not that the kitchen is clean. Settled areas are re-detected every minute to recover initial misses; newly recovered dishes have unknown deposit time and owner. Continuing deadline evidence requires a matching detection, not just unchanged pixels. Per-area contextual crops retain full-frame detections; when the optional local `models/dishes-teacher.pt` exists, sinks also get a tighter 640-pixel detail pass. Live tracking, kitchen checks, and replays use the same area adapter and existing confidence/verifier gates. Nested and occluded dishes can still be missed.

**How well it's working.** People → *How well it's working* (`GET /api/scoreboard`) shows, week by week: camera records, how many were not dishes, how often its owner guess was right when a person checked, how many owners it named itself and how many of those were corrected, disputes and how many changed the outcome, and late dishes. It also shows the last detector retraining and identity refit. Housemates see the same table.

**Personal links.** The key icon on a person under People makes a link `/me#<token>` for that person. It shows only their own confirmed records with the photos behind each one, and lets them dispute one ("This isn't right"); a dispute stops it counting until someone reviews it. The token stays after `#`, is sent only as the `X-Personal-Token` header, and only its hash is stored. Making a new link revokes the old one. The link works only where the dashboard is reachable, so a housemate needs tailnet access. The same sheet sets that person's own ntfy topic: they get "The camera named you", their own late dishes, and their own weekly line.

**Review answers become training data.** Confirming, reassigning or resolving a camera record saves its "Dish left unattended" photo with the dish's box; dismissing it as not a dish saves it with no boxes. Each record is saved once, these pictures come first under Train the detector, and they are kept ahead of plain samples when the dataset is over budget.

**Identity weights.** `python -m wash_dishes fit-identity` relearns how much face, head, outfit and height count from sightings people named, by confirming an owner or answering who someone is (at least 40). Every run also records how often the top guess is right today (`accuracy_now`), shown under *How well it's working*. It adopts the new weights (`identity-weights.json`) only if they are more accurate on 5-fold held-out sightings than the weights in use. Each run is recorded in `identity-fit-last.json`.

**Detector retraining.** `python -m wash_dishes improve --truth TRUTH.json --footage DIR` (repeat the pair for each recorded period; scores are pooled) trains a kitchen detector from the labelled pictures (when at least 20 are new, or with `--force`). It replays the recorded footage through the candidate and the detector in use. It switches to `models/dishes-kitchen.pt` only if the candidate finds at least as many left-out dishes and has a higher F1. The previous model is kept, and `python -m wash_dishes rollback-detector` restores it. Runs are logged to `models/improve-history.jsonl`. Live analysis must be stopped while it trains. The truth files are the gate, so a small one makes a weak gate; label more periods to make it stricter.

**Second look at new dishes.** The detector's confidence barely separates real dishes from its mistakes in this kitchen, and neither does CLIP asked zero-shot. A linear probe on CLIP image features, trained on this kitchen's labels, does: on the first run it kept 95% of dishes and dropped about half of the mistakes on examples it hadn't seen. Every brand-new box (not one seen being carried from elsewhere) is scored; one below the probe's threshold opens nothing, isn't logged as a change, and is remembered where it sits so it isn't "new" again at the next look. `python -m wash_dishes train-verifier [--holdout TRUTH.json ...]` retrains it from the labelled frames (the detector runs on each: boxes a person also drew are dishes, ones nowhere near any are mistakes), Review answers (named owner, cleanup or "can't tell whose" mean a dish; "not a dish" a mistake) and kitchen check answers. It writes `models/dish-probe.json` only with at least 60 of each and a held-out AUC of 0.7 or more, and sets the threshold to keep 95% of held-out dishes. Replays use it too, so `evaluate` and the detector gate measure the whole pipeline; `evaluate --verify-threshold 0` scores every change without dropping any (each change carries its `dish_score`), and `--no-verify` leaves it out. Setup on the Jetson, without letting pip touch Torch: `.venv/bin/pip install --no-deps -r requirements-second-look.txt`, then `python -m wash_dishes models verifier` (checksum-verified LAION ViT-B/32 weights, 605 MB). Without the model or a probe, nothing changes.

**Kitchen checks.** At 23:00 and 07:00 (local time, `WASHDISHES_TIMEZONE`), once every area has been still with nobody in it for a minute, the camera looks at every counter and the sink. Whatever is out then wasn't washed, however it got there. Each dish found is linked to an open record where one matches; that record gets the photo as "Still out at the 11 PM kitchen check". Review shows the check first: tap any outline that isn't a dish, add how many it missed, save. The answers label the second look, and a check with nothing missed becomes a fully labelled training frame. Now shows the latest check's count and how many of those dishes had no record (what tracking missed). A check that finds no quiet moment within three hours is recorded as missed.

On the Jetson, `scripts/install-improvement-timers.sh TRUTH_JSON FOOTAGE_DIR [TRUTH_JSON FOOTAGE_DIR ...]` installs three user timers: identity refit daily at 04:30 (low priority, alongside live analysis), second-look retraining daily at 04:45 (holding out the truth windows), and retraining on Sunday at 02:00, which stops `dish-watch` and always starts it again afterwards. The Jetson's clock can boot at 1970 and jump forward once synced; the retraining unit's `ExecCondition` skips any run outside early Sunday so that jump can't start it. Monitoring pauses for the run, typically a few hours.

## Access from your laptop through Tailscale

Keep the server bound to `127.0.0.1`. Install/sign into Tailscale on the Jetson and laptop, and allow the laptop to access the Jetson under your tailnet policy. Start live mode, then run on the Jetson:

```sh
scripts/tailscale-serve.sh
```

The script uses `tailscale serve --bg http://127.0.0.1:8787`; it prints the private HTTPS address. Open that address on your laptop and log in with the household key. This uses **Serve**, not public Funnel. No router port forwarding is needed. Tailscale account enrollment and tailnet policy remain operator-controlled. [Official Serve documentation](https://tailscale.com/kb/1242/tailscale-serve)

For a Linux service, first install this checkout and its virtual environment on the Jetson, then run `scripts/install-jetson-service.sh` there. It validates the user systemd unit, starts live mode, and waits for the health endpoint before reporting success. Use `systemctl --user status dish-watch`, `systemctl --user restart dish-watch`, and `systemctl --user stop dish-watch` to manage it. Logs on this Jetson are available with `sudo journalctl _SYSTEMD_USER_UNIT=dish-watch.service`; the unprivileged user journal is unavailable on its current OS image. User lingering is needed for unattended startup without login; configure that explicitly on the Jetson. This service installer and Tailscale sharing are not run during local development.

Deploy the checkout under the service user's home directory. Private runtime data defaults to `~/.local/share/washdishes/live`. Use `scripts/tailscale-serve.sh` to obtain your own private HTTPS address and the CLI's `access-key` command to retrieve the household key. Keep deployment inventories, network addresses, keys, and household media outside Git. The tested setup monitors visible counters, stove, sink, drying racks, and storage with a 120-minute grace period; end-to-end household attribution accuracy remains unverified.

## Data and retention

Defaults are a fifty-hour raw buffer, seven days of ordinary evidence clips, ninety days of photos (small, and the main proof of who and when), thirty days of review/overdue evidence, a 20 GB media budget, and a separate 3 GB training-frame budget. Live defaults to searching the last twelve hours. Increasing the buffer preserves more footage going forward; it cannot restore footage already removed. The ledger — who left how many dishes, and whether they were washed — is kept indefinitely. Confirmed overdue/disputed events promote their existing retained evidence. Cleanup runs every minute and can be triggered in Setup. Raw footage is removed first under quota pressure; retained evidence is removed if necessary to respect the budget. Active segments have a thirty-second grace period, so usage can temporarily exceed the budget between cleanup passes. Expired evidence is labeled unavailable while the ledger remains.

The budget covers raw recordings and evidence, not the installed model assets or the small database. Leave space for the operating system and models. Completed segments are copied into event clips before buffer removal, so there are no pinned raw links to invalidate. Segment export preserves H.264 and follows keyframe boundaries. The first ten seconds after camera start may have only a snapshot because no complete recording segment exists yet. Put-down clips are about 25 seconds and export on a background worker, so they never pause detection. Pending clip requests are bounded and held in memory; a service restart can lose a pending clip, while saved snapshots remain. Failed clip export leaves the snapshot available. Recent activity links a retained snapshot directly to its event. Current automatic evidence includes the first unattended observation, available nearby-person context, and supported overdue/cleanup observations; clip boundaries are approximate and footage is not proof of events outside the view.

SQLite history and audit records persist across restarts. The rate denominator includes confirmed resolved outcomes or confirmed continuing overdue observations. Expired unverified deadlines are displayed separately and never treated as successful on-time outcomes. Compact audit events for closed records age out after a year; obligation summaries remain for historical totals. Back up the database using SQLite's backup API, not a plain copy of a running WAL database. Protect the data directory and household key.

## Check the build

```sh
.venv/bin/pytest -q
.venv/bin/ruff check wash_dishes tests
for f in wash_dishes/static/app.js wash_dishes/static/js/*.js wash_dishes/static/js/pages/*.js; do node --check "$f" || exit 1; done
WASHDISHES_DATA_DIR="$PWD/.data" .venv/bin/python -m wash_dishes check
```

Tests cover attribution corrections, the 89/91-minute cleanup boundary, restart persistence, unknown outcomes, enrollment consensus, authentication/CSRF, expired evidence, quota isolation, observation gaps, and real FFmpeg export when FFmpeg is available. Public-image inference smoke tests establish executable local models, not household accuracy. The dashboard, GPU runtime, and Tapo stream have been deployed and checked on the Jetson. Observed deposit/cleanup accuracy and household attribution remain physical validation gates. Review mode can operate without enrollment by retaining unknown ownership and evidence.

The Jetson's headless setup is complete: JetPack 7.2.1, L4T 39.2.1, Ubuntu 24.04.5, and the M.2 boot drive are verified. Both firmware slots were updated and successfully booted. CUDA memory operations and a TensorRT GPU calculation passed; the cuDNN library loaded successfully. Machine-specific setup receipts and recovery launchers are retained privately and are not included in this repository.

The installed SDK was trimmed to the JetPack runtime with CUDA compiler and libraries retained. Removing profilers, samples, TensorRT static development archives, and completed setup files reclaimed about 8.8 GB. CUDA operations, TensorRT engine building and GPU execution, cuDNN, and OpenCV passed checks after package removal. Cleanup receipts, recovery backups, and archived setup logs are retained privately outside version control.

**Automatic four-stage images.** **Evidence** collects a saved JPEG for each camera record: Before → Use / handling candidate → After departure → Later view. The media worker updates drafts in bounded batches about every thirty seconds, outside frame analysis. New captures preserve the source frame's actual timestamp and stage-specific box; a person-context box is explicitly different from a detected dish box. Legacy captures without coordinates are not given invented item boxes. Missing, corrupt or expired originals stay visibly missing. Four different, correctly ordered frames are necessary, but actual use, departure, and the same item later still need human review. Baseline backlog never invents a before/use sequence. A later photo before the grace period ends does not enter the issue-review count.

The **Evidence** badge reports open records with an after-deadline photo waiting for review, including records with unknown ownership. **Recent saved drafts** shows up to 200 recent records, including incomplete ones; older records remain accessible from History and their individual record links. **Reviewed sequences** contains only four-stage sequences whose four questions a person answered Yes. Review defaults to **Cannot tell** and does not change ownership, washing outcomes, named statistics or training labels. Changed source images or record facts invalidate an old review. **Download image** saves the collage; **Originals + clips** exports the original evidence, source hashes, capture metadata and sequence manifest. Collages use the existing photo retention and shared media budget; expired images are unavailable while metadata persists. This local collection does not configure or send external notifications.
