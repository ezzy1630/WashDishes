#!/usr/bin/env python3
"""Exercise prepared local models with all Python network connections forbidden."""
import argparse
import json
import socket
from pathlib import Path

from wash_dishes.vision import LocalVision


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True, help="Prepared local data root")
    parser.add_argument("--image", type=Path, required=True, help="A JPEG test image on disk")
    args = parser.parse_args()
    attempts = []

    def offline(*arguments, **keywords):
        attempts.append(True)
        raise RuntimeError("Network connection attempted during local inference")

    socket.socket.connect = socket.socket.connect_ex = offline
    socket.create_connection = socket.getaddrinfo = offline
    engine = LocalVision(args.root)
    result = engine.analyze(args.image.read_bytes())
    print(json.dumps({"detections": [{"kind": d["kind"], "score": round(d["score"], 3), "track_id": d["track_id"]} for d in result["detections"]], "resident_id": result["resident_id"], "network_attempts": len(attempts)}))
    if attempts:
        raise SystemExit("Inference attempted a network connection")


if __name__ == "__main__":
    main()
