"""A nightly second look at new proof pictures by a small local vision-language model (Qwen3.5-2B).

With WASHDISHES_PROOF_REVIEW=1 the tracer saves a finished proof as 'review' instead of 'ready',
so it stays off the house page (the dish still shows as a plain still-out card) until this check
passes it. Run as `python -m wash_dishes review-proofs` with live analysis stopped: the model needs
the Jetson's GPU memory. The recorder keeps running.

The question, asked of the put-down picture cropped around the dish (yellow) and person (cyan):
is that person USING the dish (eating, drinking, cooking, serving) rather than only carrying or
setting it down? A logistic head on the model's hidden state answers it. Measured on 352
hand-labelled frames from 2026-11-17..21 on the Jetson's GPU, leaving each day out in turn:
AUC 0.90 (the model's own A/B answer: 0.85); at the 0.7 threshold it kept 77% of real use and
rejected 90% of carrying or setting down. A proof below the threshold is 'vetoed' with its score
and reason, and is never traced again. Asking the model whether the outlined thing is a dish at
all was tried and dropped: AUC 0.70 on 648 audited boxes, below the CLIP probes already in use.

On Apple GPUs this model returns NaN in every precision; it runs on CUDA or, slowly, on a CPU.
"""
from __future__ import annotations

import io
import json
import logging
from collections.abc import Sequence
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
            out = self.model(**inputs, output_hidden_states=bool(layers))
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


def judge_proof(judge: Judge, folder: Path, data: dict) -> dict:
    """Score and verdict for one proof folder's put-down picture and its saved boxes."""
    if judge.head is None:
        raise ValueError("No adopted head (models/proof-review-head.json).")
    using = judge.using(crop((folder / "putting.jpg").read_bytes(), data["box"], data.get("person_box")))
    threshold = judge.head["threshold"]
    passed = using >= threshold
    return {"scores": {"using": round(using, 4)}, "passed": passed, "threshold": threshold,
            "reason": None if passed else "the put-down picture shows the dish being carried or set down, not used"}


def review(root: Path, store: Store, judge: Judge) -> list[dict]:
    """Judge every proof waiting for review; pass it to 'ready' or mark it 'vetoed'."""
    folder = Path(root) / "proofs"
    with store.lock:
        rows = store.conn.execute("SELECT obligation_id, data FROM proofs WHERE status='review' ORDER BY obligation_id").fetchall()
    results = []
    for row in rows:
        oid, data = row["obligation_id"], json.loads(row["data"])
        try:
            verdict = judge_proof(judge, folder / str(oid), data)
        except OSError:
            verdict = {"passed": False, "reason": "the proof pictures are gone", "scores": {}}
        except ValueError as error:
            # Leave it waiting: a broken run must neither publish nor discard a proof.
            log.warning("Proof %s left for the next review: %s", oid, error)
            results.append({"id": oid, "error": str(error)})
            continue
        data["review"] = verdict | {"at": stamp(utcnow())}
        status = "ready" if verdict["passed"] else "vetoed"
        if not verdict["passed"]:
            data["reason"] = verdict["reason"]
        with store.lock, store.conn:
            store.conn.execute("UPDATE proofs SET status=?, data=? WHERE obligation_id=? AND status='review'", (status, json.dumps(data), oid))
        results.append({"id": oid, "status": status, **verdict})
    return results
