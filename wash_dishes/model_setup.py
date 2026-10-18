"""Explicit, checksum-verified preparation of local models.

Runtime never calls this module. Install the documented optional dependencies
first; this command downloads model assets, never packages or a replacement Torch.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from urllib.request import Request, urlopen

from .vision import DETECTOR_NAME, KINDS, SFACE_NAME, YUNET_NAME, VisionError, _import_yolo_offline

YUNET_URL = "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
SFACE_URL = "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx"
WORLD_URL = "https://github.com/ultralytics/assets/releases/download/v8.4.0/yolov8s-worldv2.pt"
CLIP_URL = "https://openaipublic.azureedge.net/clip/models/40d365715913c9da98579312b702a82c18be219cc2a73407c4526f58eba950af/ViT-B-32.pt"
YUNET_SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"
SFACE_SHA256 = "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79"
WORLD_SHA256 = "9b2c17ab6124a913e9b3a5c170617920d91b0f01111a8479da69f00e2cf27792"
CLIP_SHA256 = "40d365715913c9da98579312b702a82c18be219cc2a73407c4526f58eba950af"
# The large open-vocabulary model is too slow for live use; it labels training frames offline.
TEACHER_URL = "https://github.com/ultralytics/assets/releases/download/v8.4.0/yolov8x-worldv2.pt"
TEACHER_SHA256 = "41e771bfbbb8894dd857f3fef7cac3b3578dffd49fd3547101efa6a606a02a0e"
TEACHER_NAME = "dishes-teacher.pt"
# The live detector is a small closed-set model fine-tuned on this kitchen.
BASE_URL = "https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo26s.pt"
BASE_SHA256 = "646f8bc3fe0a656803d95c294f7852321748cb29d13466a1af8862e2db384a1b"
TUNED_NAME = "dishes-tuned.pt"
# Image features for the second look at new dishes (open_clip format, LAION-2B ViT-B/32).
VERIFIER_URL = "https://huggingface.co/laion/CLIP-ViT-B-32-laion2B-s34B-b79K/resolve/main/open_clip_model.safetensors"
VERIFIER_SHA256 = "ac4f8c4b88af6d963118cbf40ad93176d092abbedfcb752601ae1866352656e6"
VERIFIER_SIZE = 605143316


def _checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(url: str, target: Path, sha256: str, *, size: int | None = None) -> Path:
    """Reuse only verified assets; interrupted downloads never replace an asset."""
    if target.is_file() and not target.is_symlink():
        if (size is None or target.stat().st_size == size) and _checksum(target) == sha256:
            return target
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".model-download-", dir=target.parent)
    temporary = Path(name)
    try:
        digest = hashlib.sha256()
        total = 0
        request = Request(url, headers={"User-Agent": "WashDishes-local-model-setup/1"})
        with os.fdopen(descriptor, "wb") as output, urlopen(request, timeout=60) as response:
            for chunk in iter(lambda: response.read(1024 * 1024), b""):
                total += len(chunk)
                # CLIP's published checkpoint is below 400 MiB. Bound all assets.
                if total > (size if size is not None else 400 * 1024 * 1024):
                    raise VisionError(f"Unexpected model size while downloading {target.name}.")
                digest.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if (size is not None and total != size) or digest.hexdigest() != sha256:
            raise VisionError(f"Checksum/size verification failed for {target.name}; existing model was preserved.")
        os.replace(temporary, target)
        return target
    except VisionError:
        raise
    except Exception as error:
        raise VisionError(f"Unable to download {target.name}: {error}") from error
    finally:
        temporary.unlink(missing_ok=True)


def prepare_faces(root: Path) -> list[Path]:
    models = Path(root) / "models"
    return [
        _download(YUNET_URL, models / YUNET_NAME, YUNET_SHA256, size=232589),
        _download(SFACE_URL, models / SFACE_NAME, SFACE_SHA256, size=38696353),
    ]


def prepare_detector(root: Path) -> Path:
    world = _download(WORLD_URL, Path(root) / "models" / "yolov8s-worldv2.pt", WORLD_SHA256, size=25923032)
    return _prepare_world(root, world, DETECTOR_NAME)


def prepare_verifier(root: Path) -> Path:
    from .verifier import CLIP_NAME
    return _download(VERIFIER_URL, Path(root) / "models" / CLIP_NAME, VERIFIER_SHA256, size=VERIFIER_SIZE)


def prepare_teacher(root: Path) -> Path:
    world = _download(TEACHER_URL, Path(root) / "models" / "yolov8x-worldv2.pt", TEACHER_SHA256, size=146355704)
    return _prepare_world(root, world, TEACHER_NAME)


def _prepare_world(root: Path, world: Path, name: str) -> Path:
    """Cache nine text prompts once, remove CLIP, save and reload the detector.

    Requires ultralytics==8.4.174 and the pinned Ultralytics CLIP fork in the
    optional setup requirements. CLIP is only used here, never during analysis.
    The generated checkpoint is specific to this app's fixed vocabulary.
    """
    models = Path(root) / "models"
    # Check dependencies before downloading hundreds of MiB. Autoinstall stays off.
    YOLO = _import_yolo_offline(Path(root) / "vision-settings")
    try:
        import clip
        import numpy as np
        from ultralytics import YOLOWorld
        from ultralytics.nn import text_model
        if not callable(getattr(clip, "load", None)) or not callable(getattr(clip, "tokenize", None)):
            raise ImportError("The installed clip package is not the supported CLIP implementation.")
    except Exception as error:
        raise VisionError("Detector setup needs the optional setup requirements, including the pinned CLIP fork. Packages are never installed automatically.") from error

    _download(CLIP_URL, models / "clip" / "ViT-B-32.pt", CLIP_SHA256)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".dishes-prepared-", suffix=".pt", dir=models)
    os.close(descriptor)
    temporary = Path(temporary_name)
    old_weights_dir = text_model.WEIGHTS_DIR
    try:
        # Current CLIP constructor loads WEIGHTS_DIR/clip. It finds the already
        # verified local cache, so set_classes does not need another download.
        text_model.WEIGHTS_DIR = models.resolve()
        detector = YOLOWorld(str(world.resolve()))
        detector.set_classes(list(KINDS))
        del detector.model.clip_model
        detector.save(temporary)
        reloaded = YOLO(str(temporary.resolve()), task="detect")
        if getattr(reloaded.model, "clip_model", None) is not None:
            raise VisionError("Prepared detector unexpectedly still contains the text encoder.")
        labels = reloaded.names
        values = labels.values() if isinstance(labels, dict) else labels
        if tuple(values) != KINDS:
            raise VisionError("Prepared detector vocabulary did not survive serialization.")
        # Test the saved graph, rather than only the in-memory model. This is a
        # compatibility check; it makes no claim about real kitchen accuracy.
        reloaded.predict(source=np.zeros((320, 320, 3), dtype=np.uint8), device="cpu", imgsz=320, verbose=False)
        target = models / name
        os.replace(temporary, target)
        return target
    except VisionError:
        raise
    except Exception as error:
        raise VisionError(f"Unable to prepare the local dish detector: {error}") from error
    finally:
        text_model.WEIGHTS_DIR = old_weights_dir
        temporary.unlink(missing_ok=True)


def train_detector(root: Path, data_yaml: Path, *, epochs: int = 80, imgsz: int = 1280, batch: int = 4, device: str | None = None) -> dict:
    """Fine-tune COCO-pretrained YOLO26s on this kitchen's labeled frames, then compare on held-out frames.

    The result is saved beside the current detector; switch to it in Setup only if it scores better.
    """
    models = Path(root) / "models"
    base = _download(BASE_URL, models / "yolo26s.pt", BASE_SHA256, size=20422725)
    YOLO = _import_yolo_offline(Path(root) / "vision-settings")
    import torch
    device = device or ("0" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu")
    model = YOLO(str(base.resolve()))
    # On Apple's GPU the loss turned NaN within three epochs with mixed precision and the warm-up's 0.1 bias learning
    # rate, and still in the third with the bias rate alone; without both it trained cleanly (2026-10-13). CUDA keeps the defaults.
    apple = {"amp": False, "warmup_bias_lr": 0.0} if device == "mps" else {}
    model.train(data=str(data_yaml), epochs=epochs, imgsz=imgsz, batch=batch, device=device, project=str((Path(root) / "training").resolve()),
                name="kitchen", exist_ok=True, patience=20, close_mosaic=10, plots=False, verbose=False, workers=2, **apple)
    best = Path(model.trainer.best)
    tuned = YOLO(str(best), task="detect")
    names = tuned.names.values() if isinstance(tuned.names, dict) else tuned.names
    if tuple(names) != KINDS:
        raise VisionError("Trained detector vocabulary does not match the expected classes.")
    scores = {}
    for label, path in (("tuned", best), ("current", models / DETECTOR_NAME)):
        if path.is_file():
            metrics = YOLO(str(path), task="detect").val(data=str(data_yaml), imgsz=imgsz, batch=batch, device=device, split="val", plots=False, verbose=False)
            scores[label] = {"map50": round(float(metrics.box.map50), 4), "map50_95": round(float(metrics.box.map), 4)}
    target = models / TUNED_NAME
    os.replace(best, target)
    return {"model": str(target), "scores": scores}
