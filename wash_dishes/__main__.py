from __future__ import annotations

import argparse

from .config import Config


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Dish watch on this machine.")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--demo", action="store_true", help="Use a separate data root and illustrative events")
    serve.add_argument("--port", type=int, default=8787)
    serve.add_argument("--host", default="127.0.0.1", help="Loopback by default; use Tailscale Serve for remote access")
    sub.add_parser("access-key", help="Print the generated household key locally")
    models = sub.add_parser("models", help="Explicit one-time local model preparation")
    models.add_argument("kind", choices=("faces", "dishes", "teacher", "verifier", "all"), default="all", nargs="?")
    sub.add_parser("check", help="Check local readiness without connecting to the camera")
    dataset = sub.add_parser("dataset", help="Export labeled kitchen frames in YOLO format")
    dataset.add_argument("action", choices=("export", "summary"))
    train = sub.add_parser("train", help="Fine-tune a detector on the labeled kitchen frames")
    train.add_argument("--epochs", type=int, default=80)
    train.add_argument("--imgsz", type=int, default=1280)
    train.add_argument("--batch", type=int, default=4)
    train.add_argument("--device", default=None, help="CUDA index, mps, or cpu; detected automatically")
    train.add_argument("--with-teacher", action="store_true", help="Also train on unreviewed teacher boxes")
    evaluate = sub.add_parser("evaluate", help="Replay recorded footage through the pipeline and score it against hand-labeled truth")
    evaluate.add_argument("truth", help="Truth JSON: from, to, and the dish changes per area visit")
    evaluate.add_argument("--footage", help="Folder of recorded .ts segments covering the window")
    evaluate.add_argument("--reuse", default=None, help="Score the predictions in an earlier report instead of replaying")
    evaluate.add_argument("--fps", type=float, default=2.0)
    evaluate.add_argument("--detector", default=None, help="Detector to evaluate instead of the current one")
    evaluate.add_argument("--dish-detector", default=None, help="Kitchen-trained detector for the per-area pass")
    evaluate.add_argument("--out", default=None, help="Write the full report here")
    evaluate.add_argument("--no-verify", action="store_true", help="Leave out the second look at new dishes")
    evaluate.add_argument("--verify-threshold", type=float, default=None, help="Override the second look's threshold; 0 scores without dropping")
    teach = sub.add_parser("teach", help="Pre-box unlabeled training frames with the offline teacher model")
    teach.add_argument("--footage", help="Folder of recorded .ts segments to sample extra frames from first")
    teach.add_argument("--every", type=float, default=60, help="Seconds between sampled frames")
    teach.add_argument("--skip", action="append", default=[], metavar="START/END", help="ISO time range to leave out, such as an evaluation window")
    improve = sub.add_parser("improve", help="Weekly: retrain the kitchen detector and adopt it only if it scores better on recorded truth")
    improve.add_argument("--truth", required=True, action="append", help="Truth JSON for an evaluation window; repeat with --footage for more periods")
    improve.add_argument("--footage", required=True, action="append", help="Recorded segments covering that truth window, in the same order")
    improve.add_argument("--epochs", type=int, default=40)
    improve.add_argument("--force", action="store_true", help="Run even with few newly labeled frames")
    sub.add_parser("rollback-detector", help="Go back to the kitchen detector in use before the last adoption")
    sub.add_parser("fit-identity", help="Relearn identity cue weights from confirmed names; adopted only if more accurate")
    sub.add_parser("add-faces", help="Add face cues to saved sightings from their person crops and put-down clips")
    verifier = sub.add_parser("train-verifier", help="Retrain the kitchen probe from people's answers about detections; adopted only if it holds up")
    verifier.add_argument("--holdout", action="append", default=[], metavar="TRUTH_JSON", help="Keep this evaluation window's frames out of training")
    verifier.add_argument("--frames", action="store_true", help="Retrain the general probe over the labeled frames instead (loads the detector and CLIP: stop live analysis first)")
    args = parser.parse_args()
    config = Config.environment(demo=getattr(args, "demo", False))
    config.prepare()
    if args.command == "serve":
        import uvicorn

        from .app import create_app
        if config.demo:
            print("Isolated demo at http://127.0.0.1:%s — access key: demo" % args.port)
        else:
            print("Dish watch at http://127.0.0.1:%s — run 'washdishes access-key' for the household key." % args.port)
        uvicorn.run(create_app(config), host=args.host, port=args.port, proxy_headers=True, forwarded_allow_ips="127.0.0.1", log_level="warning")
    elif args.command == "access-key":
        from .security import Access
        Access(config.root, config.password)
        if config.password:
            print("The access key is supplied by WASHDISHES_ACCESS_KEY; it is not printed.")
        else:
            print((config.root / "access-key").read_text().strip())
    elif args.command == "models":
        from .model_setup import prepare_detector, prepare_faces, prepare_teacher
        if args.kind == "teacher":
            print("Teacher ready:", prepare_teacher(config.root))
            return
        if args.kind == "verifier":
            from .model_setup import prepare_verifier
            print("Second-look model ready:", prepare_verifier(config.root))
            return
        if args.kind in ("faces", "all"):
            prepare_faces(config.root)
        if args.kind in ("dishes", "all"):
            prepare_detector(config.root)
        print("Model preparation complete. Enable local vision in Setup.")
    elif args.command in ("dataset", "train", "teach"):
        import json

        from .dataset import Dataset
        from .store import Store
        store = Store(config.root)
        data = Dataset(config.root, store)
        if args.command == "dataset" and args.action == "summary":
            print(json.dumps(data.summary(), indent=2))
        elif args.command == "dataset":
            print("Exported:", data.export())
        elif args.command == "teach":
            from datetime import datetime

            from .model_setup import TEACHER_NAME
            from .vision import LocalVision
            if args.footage:
                skip = [tuple(datetime.fromisoformat(part) for part in window.split("/")) for window in args.skip]
                print("Sampled frames:", data.sample_footage(args.footage, every=args.every, skip=skip))
                print("Held out for evaluation:", data.hold_out(skip))
            teacher = LocalVision(config.root, config.root / "models" / TEACHER_NAME)
            # The counter band again at full resolution, as live analysis does.
            print("Pre-boxed frames:", data.prelabel(lambda jpeg: teacher.analyze(jpeg, focus=[0.0, 0.45, 1.0, 0.55])["detections"]))
        else:
            from .model_setup import train_detector
            result = train_detector(config.root, data.export(include_teacher=args.with_teacher), epochs=args.epochs, imgsz=args.imgsz, batch=args.batch, device=args.device)
            print(json.dumps(result, indent=2))
            print("Compare map50: higher is better. Score it with evaluate --dish-detector", result["model"], "and, if it helps, copy it to models/dishes-kitchen.pt")
        store.close()
    elif args.command == "evaluate":
        import json
        from pathlib import Path

        from .evaluation import evaluate
        from .store import Store
        store = Store(config.root)
        if not args.footage and not args.reuse:
            raise SystemExit("Give --footage to replay, or --reuse to score an earlier report.")
        report = evaluate(config, store, Path(args.truth), Path(args.footage) if args.footage else None, fps=args.fps,
                          detector=Path(args.detector) if args.detector else None,
                          dish_detector=Path(args.dish_detector) if args.dish_detector else None, reuse=Path(args.reuse) if args.reuse else None,
                          out=Path(args.out) if args.out else None, verify=not args.no_verify, verify_threshold=args.verify_threshold)
        store.close()
        if args.out:
            Path(args.out).write_text(json.dumps(report, indent=1))
        summary = {key: report[key] and {k: v for k, v in report[key].items() if k not in ("missed", "extra")} for key in ("changes", "left_out", "deployed_left_out")}
        print(json.dumps(summary, indent=1))
    elif args.command == "train-verifier":
        import json
        from datetime import datetime
        from pathlib import Path

        from .store import Store
        from .verifier import refit_kitchen
        store = Store(config.root)
        windows = [(datetime.fromisoformat(t["from"]), datetime.fromisoformat(t["to"])) for t in (json.loads(Path(p).read_text()) for p in args.holdout)]
        try:
            if not args.frames:
                print(json.dumps(refit_kitchen(config.root, store, holdout=windows), indent=1))
                return
            from .config import monitoring_areas
            from .inventory import START_SCORE
            from .verifier import Embedder, refit
            from .vision import LocalVision
            areas = monitoring_areas(store.settings())
            region = [min(a["box"][0] for a in areas), min(a["box"][1] for a in areas)]
            region += [max(a["box"][0] + a["box"][2] for a in areas) - region[0], max(a["box"][1] + a["box"][3] for a in areas) - region[1]]
            vision = LocalVision(config.root)

            def detect(jpeg: bytes):
                # The same per-area pass as live analysis, so its mistakes are the ones to learn.
                vision.analyze(jpeg)
                return vision.last_image, [d for d in vision.detect_region(region) if d["score"] >= START_SCORE]
            print(json.dumps(refit(config.root, store, detect, Embedder(config.root), holdout=windows), indent=1))
        finally:
            store.close()
    elif args.command in ("improve", "rollback-detector", "fit-identity"):
        import json
        from pathlib import Path

        from .store import Store
        store = Store(config.root)
        try:
            if args.command == "fit-identity":
                from .identity_fit import refit
                print(json.dumps({k: v for k, v in refit(store).items()}, indent=1))
            elif args.command == "rollback-detector":
                from .improve import rollback
                rollback(config.root, store)
                print("Rolled back. Restart the service to load it.")
            else:
                import hashlib

                from .dataset import Dataset
                from .evaluation import evaluate, evaluate_frames
                from .improve import combine
                from .improve import improve as run
                from .model_setup import train_detector
                if len(args.truth) != len(args.footage):
                    raise SystemExit("Give one --footage for each --truth.")
                periods = [(Path(t), Path(f)) for t, f in zip(args.truth, args.footage)]
                truth_id = hashlib.sha256(b"".join(t.read_bytes() for t, _ in periods)).hexdigest()
                data = Dataset(config.root, store)
                result = run(config.root, store, data.summary()["counts"]["labeled"], force=args.force, truth_id=truth_id,
                             train=lambda: Path(train_detector(config.root, data.export(), epochs=args.epochs)["model"]),
                             evaluate=lambda model: combine([evaluate(config, store, t, f, dish_detector=model) for t, f in periods]),
                             evaluate_stills=lambda model: evaluate_frames(config.root, store, dish_detector=model))
                print(json.dumps(result, indent=1))
        finally:
            store.close()
    elif args.command == "add-faces":
        import json

        from .identity_fit import add_faces
        from .store import Store
        from .vision import LocalVision
        store = Store(config.root)
        try:
            vision = LocalVision(config.root)

            def face_of(path):
                crop = vision._cv.imread(str(path)) if path.is_file() else None
                return None if crop is None else vision.sighting_face(crop)
            vision._load_faces()
            print(json.dumps(add_faces(store, face_of, lambda path: vision.clip_face(path) if path.is_file() else None), indent=1))
        finally:
            store.close()
    elif args.command == "check":
        import shutil

        from .vision import LocalVision
        print("Data directory:", config.root)
        print("FFmpeg:", shutil.which("ffmpeg") or "not installed")
        print("Local vision:", LocalVision(config.root).status()["message"])
        print("Tailscale CLI:", shutil.which("tailscale") or "not installed")


if __name__ == "__main__":
    main()
