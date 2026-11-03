"""Synthetic local evidence rendered by the production filmstrip renderer."""
import argparse
import json
import sys
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image

sys.path[:0] = [str(Path(__file__).resolve().parents[2]), str(Path(__file__).resolve().parents[2] / 'scripts/house_e2e')]
from seed import jpeg  # noqa: E402

from wash_dishes.filmstrip import render  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument('--output', required=True)
output = Path(parser.parse_args().output).resolve()
if not output.is_relative_to('/Volumes/Neural/dishwatch-scratch'):
    raise SystemExit('Fixture output must stay in scratch.')
output.mkdir(parents=True, exist_ok=True)
titles = {101:'Pan and pot',103:'Dinner plate',104:'Pan by the stove',105:'Pot on the counter',106:'2 pans, pot and plate',110:'Pot and bowl',112:'Object near the stove',113:'Cup on the counter'}
kinds = {101: ['pan', 'pot'], 103: ['plate'], 104: ['pan'], 105: ['pot'], 106: ['pan', 'pan', 'pot', 'plate'], 110: ['pot', 'bowl'], 112: ['bowl'], 113: ['cup']}
result = {}
for index, (visit_id, title) in enumerate(titles.items()):
    at = datetime(2026, 11, 2, 11, 14, tzinfo=ZoneInfo('America/Los_Angeles')) + timedelta(hours=index)
    # A short strip exercises intrinsic sizing; missing arrival is not a naming cue.
    count = 4 if visit_id == 103 else 6
    frames = [{'at': (at + timedelta(minutes=i)).timestamp(), 'jpeg': jpeg(f'DEMO: {title}, frame {i+1}'), 'person_boxes': []} for i in range(count)]
    boxes = [[.2 + i * .1, .4, .15, .35] for i in range(len(kinds[visit_id]))]
    item = {'dishes': [{'kind': kind, 'quantity': 1} for kind in kinds[visit_id]], 'box': boxes[0], 'location': 'Dining table' if visit_id == 103 else 'Counter'}
    strip = render(item, frames, boxes=boxes, already_at=at.timestamp() if visit_id in (103, 113) else None)
    photo = jpeg(f'DEMO: {title}, later photo')
    (output / f'{visit_id}-strip.jpg').write_bytes(strip)
    (output / f'{visit_id}-later.jpg').write_bytes(photo)
    with Image.open(BytesIO(strip)) as image:
        width, height = image.size
    result[str(visit_id)] = {'at':at.isoformat(), 'title':title, 'area':item['location'], 'frame_times':[datetime.fromtimestamp(f['at'],at.tzinfo).isoformat() for f in frames], 'strip':f'{visit_id}-strip.jpg', 'photo':f'{visit_id}-later.jpg', 'width':width, 'height':height, 'photo_width':960, 'photo_height':540, 'arrival_seen':visit_id not in (103, 113)}
print(json.dumps(result))
