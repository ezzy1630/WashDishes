"""An hourly look through the footage for someone USING each new proof's dish, by a small local
vision-language model (Qwen3.5-2B).

With WASHDISHES_PROOF_REVIEW=1 the tracer saves a finished proof as 'review' instead of 'ready',
so it stays off the house page (the dish still shows as a plain still-out card) until this check
passes it to 'ready' or marks it 'vetoed'. Run as `python -m wash_dishes review-proofs` with live
analysis stopped: the model needs the Jetson's GPU memory. The recorder keeps running.

The tracer's middle picture is whoever put the dish down, and moving a dish is not using it: the
house rule is that the last person who used a dish is responsible for it. So this check looks for
use. From the "before" picture to the late photo it takes the journal's frames with a person within
reach of the dish's spot (up to three per visit, 40 in all), cuts each from the footage with the dish
outlined yellow and the person cyan, and asks whether that person is USING the dish (eating,
drinking, cooking, serving) rather than only handling it. A logistic head on the model's hidden
state answers. A proof passes only when enough frames at least 3 s apart clear the head's threshold
(the head's min_frames; 3 adopted); the best two become the collage's middle pictures and the
latest one's person is the one shown. Measured on 73 dishes from 2026-11-19..21, holding each dish's
frames out of training: with 3 frames at 0.9, 8 passed; in 4 both pictures show the dish being used,
in 1 one does and the other is unclear, 2 are unclear, and in 1 the person was handling a different
pan beside it. With 2 frames 14 passed but 4 had a wrong picture; a single frame was worse still.

On Apple GPUs this model returns NaN in every precision; it runs on CUDA or, slowly, on a CPU.
"""
from __future__ import annotations

import io
import json
import logging
from collections.abc import Callable, Sequence
from pathlib import Path

from .store import Store, stamp, utcnow

log = logging.getLogger(__name__)

HEAD_NAME = "proof-review-head.json"
USING = ("The yellow box outlines one dish. The cyan box outlines one person. What is that person doing with that dish at this moment?\n"
         "A) USING it: eating or drinking from it, cooking in it, stirring it, or serving or scooping food into or out of it.\n"
         "B) HANDLING it: carrying, moving, picking up, setting down, rinsing, washing or loading it, or not touching it at all.\n"
         "Answer with the single letter A or B.")


def crop(jpeg: bytes, dish: Sequence[float] | None, person: Sequence[float] | None, *, min_side: float = 0.45, long: int = 768):
    """The outlined region the model was tested on: dish yellow, person cyan, with surrounding context."""
    from PIL import Image, ImageDraw
    image = Image.open(io.BytesIO(jpeg)).convert("RGB")
    width, height = image.size
    boxes = [b for b in (dish, person) if b]
    if boxes:
        x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
        x1, y1 = max(b[0] + b[2] for b in boxes), max(b[1] + b[3] for b in boxes)
    else:
        x0, y0, x1, y1 = 0, 0, 1, 1
    cx, cy = (x0 + x1) / 2 * width, (y0 + y1) / 2 * height
    w = max((x1 - x0) * width * 1.5, min_side * width)
    h = max((y1 - y0) * height * 1.5, w * 9 / 16)
    w = min(max(w, h * 16 / 9 * 0.75), width)
    h = min(h, height)
    left, top = min(max(0, cx - w / 2), width - w), min(max(0, cy - h / 2), height - h)
    draw = ImageDraw.Draw(image)
    line = max(3, width // 400)
    for box, colour in ((person, (0, 230, 255)), (dish, (255, 230, 0))):
        if box:
            draw.rectangle([box[0] * width, box[1] * height, (box[0] + box[2]) * width, (box[1] + box[3]) * height], outline=colour, width=line)
    image = image.crop((int(left), int(top), int(left + w), int(top + h)))
    image.thumbnail((long, long))
    return image


class Judge:
    """Loads the model once; ask() returns P(answer A) between A and B, plus a hidden state on request."""

    def __init__(self, model_dir: Path, head: dict | None = None, *, device: str | None = None, dtype: str | None = None):
        import torch
        from transformers import AutoModelForImageTextToText, AutoProcessor
        self.torch = torch
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        if self.device == "mps":
            raise ValueError("Qwen3.5 returns NaN on Apple GPUs; use cuda or cpu.")
        dtype = dtype or ("bfloat16" if self.device == "cuda" else "float32")
        self.processor = AutoProcessor.from_pretrained(str(model_dir))
        self.model = AutoModelForImageTextToText.from_pretrained(str(model_dir), dtype=getattr(torch, dtype)).to(self.device).eval()
        tokenizer = self.processor.tokenizer
        self.answers = [tokenizer.encode(x, add_special_tokens=False)[0] for x in ("A", "B")]
        self.head = head

    def ask(self, images: list, prompt: str, *, layers: Sequence[int] = ()) -> tuple[float, list]:
        torch = self.torch
        content = [{"type": "image"} for _ in images] + [{"type": "text", "text": prompt}]
        text = self.processor.apply_chat_template([{"role": "user", "content": content}], add_generation_prompt=True,
                                                  tokenize=False, enable_thinking=False)
        inputs = self.processor(text=[text], images=images, return_tensors="pt").to(self.device)
        with torch.no_grad():
            # Only the last position's answer is read; the full-vocabulary logits for every token cost ~0.5 GB.
            out = self.model(**inputs, output_hidden_states=bool(layers), logits_to_keep=1)
        logits = out.logits[0, -1].float()
        p = torch.softmax(logits[self.answers], 0)[0].item()
        if p != p:  # NaN: the numerics broke; never let that pass or veto a proof by accident
            raise ValueError("The model returned NaN.")
        hidden = [out.hidden_states[i][0, -1].float().cpu().numpy() for i in layers]
        return p, hidden

    def using(self, image) -> float:
        _, (hidden,) = self.ask([image], USING, layers=[self.head["layer"]])
        return head_score(self.head, hidden)


def head_score(head: dict, hidden) -> float:
    import numpy as np
    z = (np.asarray(hidden, dtype=np.float64) - np.asarray(head["mean"])) / np.asarray(head["scale"])
    return float(1 / (1 + np.exp(-(z @ np.asarray(head["coef"]) + head["intercept"]))))


def load_head(root: Path) -> dict | None:
    try:
        return json.loads((Path(root) / "models" / HEAD_NAME).read_text())
    except (OSError, ValueError):
        return None


REACH = 0.02          # a person this close to the dish's spot (see putdown._reach) could be using it
VISIT_GAP = 10.0      # s; frames further apart are separate visits
MAX_FRAMES = 40
SPACING = 3.0         # s between the frames that count, so one moment is not counted twice
WINDOW = 3 * 3600     # s searched after the before picture at most
NOT_USED = "nobody was seen using the dish in the footage, only near it or moving it"
NOBODY = "nobody was near the dish in the footage"


def footage_moments(journal, buffer: Path) -> Callable[[list[float], float, float], list[dict]]:
    """Frames from the recording with a person within reach of `box`: {at, person_box, jpeg}, in time order."""
    import bisect

    from .putdown import _reach, decoded_photos, segments

    def moments(box: list[float], t0: float, t1: float) -> list[dict]:
        visits, last = [], None
        for frame in journal.frames(t0, t1):
            people = [d["box"] for d in frame["detections"] if d["kind"] == "person" and _reach(box, d["box"]) <= REACH]
            if not people:
                continue
            if last is None or frame["at"] - last > VISIT_GAP:
                visits.append([])
            visits[-1].append((frame["at"], tuple(min(people, key=lambda p: _reach(box, p)))))
            last = frame["at"]
        picks = sorted({pick for v in visits for pick in ((v[0], v[len(v) // 2], v[-1]) if len(v) >= 3 else v)})[:MAX_FRAMES]
        segs = segments(buffer)
        ends = [s[1] for s in segs]
        by_segment: dict[int, list] = {}
        for at, person in picks:
            i = bisect.bisect_left(ends, at)
            if i < len(segs) and segs[i][0] <= at <= segs[i][1]:
                by_segment.setdefault(i, []).append((at, person))
        out = []
        for i, items in by_segment.items():
            got = decoded_photos(segs[i], sample_times=[at for at, _ in items])
            for at, person in items:
                frame_at, jpeg = min(got, key=lambda g: abs(g[0] - at), default=(None, None))
                if frame_at is not None and abs(frame_at - at) <= 1:
                    out.append({"at": frame_at, "person_box": list(person), "jpeg": jpeg})
        return sorted(out, key=lambda f: f["at"])
    return moments


def judge_proof(judge: Judge, data: dict, moments: Callable) -> dict:
    """Verdict for one proof: the best two spaced use frames, or why there are none."""
    if judge.head is None:
        raise ValueError("No adopted head (models/proof-review-head.json).")
    threshold, need = judge.head["threshold"], judge.head.get("min_frames", 2)
    t0 = data["before_at"]
    frames = moments(data["box"], t0, min(data["late_at"], t0 + WINDOW))
    for frame in frames:
        frame["using"] = judge.using(crop(frame["jpeg"], data["box"], frame["person_box"]))
    picked: list[dict] = []
    for frame in sorted((f for f in frames if f["using"] >= threshold), key=lambda f: -f["using"]):
        if all(abs(frame["at"] - p["at"]) >= SPACING for p in picked):
            picked.append(frame)
    uses = sorted(picked[:2], key=lambda f: f["at"])
    passed = len(picked) >= need
    return {"scores": {"using": [round(f["using"], 4) for f in uses], "frames": len(frames), "clear": len(picked)},
            "passed": passed, "threshold": threshold, "uses": uses if passed else [],
            "reason": None if passed else NOBODY if not frames else NOT_USED}


def review(root: Path, store: Store, judge: Judge, moments: Callable) -> list[dict]:
    """Judge every proof waiting for review; pass it to 'ready' with its use pictures, or mark it 'vetoed'."""
    folder = Path(root) / "proofs"
    with store.lock:
        rows = store.conn.execute("SELECT obligation_id, data FROM proofs WHERE status='review' ORDER BY obligation_id").fetchall()
    results = []
    for row in rows:
        oid, data = row["obligation_id"], json.loads(row["data"])
        try:
            if not (folder / str(oid)).is_dir():
                raise OSError("the proof pictures are gone")
            verdict = judge_proof(judge, data, moments)
        except OSError as error:
            # Like the tracer: what cannot be read closes the proof, or it would pause analysis every hour.
            verdict = {"passed": False, "uses": [], "scores": {}, "reason": f"could not read the footage or pictures ({error})"}
        except ValueError as error:
            # Leave it waiting: a broken run must neither publish nor discard a proof.
            log.warning("Proof %s left for the next review: %s", oid, error)
            results.append({"id": oid, "error": str(error)})
            continue
        uses = verdict.pop("uses")
        data["review"] = verdict | {"at": stamp(utcnow())}
        if uses:
            for n, frame in enumerate(uses, 1):
                (folder / str(oid) / f"using{n}.jpg").write_bytes(frame["jpeg"])
            data.update(using_at=[f["at"] for f in uses], using_boxes=[f["person_box"] for f in uses], person_box=uses[-1]["person_box"])
            # Any name guess so far is of whoever put the dish down, who may only have moved it.
            store.replace_putdown_samples(oid, {}, "", at=utcnow())
            store.set_suggestion(oid, None, None)
        else:
            data["reason"] = verdict["reason"]
        status = "ready" if uses else "vetoed"
        with store.lock, store.conn:
            store.conn.execute("UPDATE proofs SET status=?, data=? WHERE obligation_id=? AND status='review'", (status, json.dumps(data), oid))
        results.append({"id": oid, "status": status, **verdict})
    return results
