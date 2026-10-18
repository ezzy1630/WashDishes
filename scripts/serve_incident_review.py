"""Serve a private review packet; all answers stay outside the live ledger."""
import argparse
from pathlib import Path

import uvicorn

from wash_dishes.review_packet import create_review_app

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("packet_directory", type=Path)
parser.add_argument("--port", type=int, default=8795)
args = parser.parse_args()
uvicorn.run(create_review_app(args.packet_directory.resolve()), host="127.0.0.1", port=args.port, access_log=False)
