"""Private local-only six-frame mock strips. Never write camera images into the repo."""
import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from PIL import Image, ImageDraw, ImageFont

parser = argparse.ArgumentParser()
parser.add_argument('--output', required=True)
args = parser.parse_args()
output = Path(args.output).resolve()
if not str(output).startswith('/Volumes/Neural/'):
    raise SystemExit('Fixture output must stay under /Volumes/Neural.')
output.mkdir(parents=True, exist_ok=True)
source = Path('/Volumes/Neural/dishwatch-scratch/houseleague/laneD/detail_manifest.json')
rows = {row['id']: row for row in json.loads(source.read_text())}
titles = {101:'Pan and pot',103:'Dinner plate',104:'Pan by the stove',105:'Pot on the counter',106:'2 pans, pot and plate',110:'Pot and bowl',112:'Object near the stove'}
try:
    font = ImageFont.truetype(str(Path(__file__).parents[1]/'fonts/MonaSans.woff2'), 22)
    small = ImageFont.truetype(str(Path(__file__).parents[1]/'fonts/MonaSans.woff2'), 18)
except OSError:
    font = ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf',22)
    small = ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf',18)
result = {}
for index, (visit_id,title) in enumerate(titles.items()):
    row = rows[1643 if index % 2 == 0 else 1662]
    frames = row['frames']
    stamps = [datetime.fromisoformat(f['utc_estimated']).astimezone(ZoneInfo('America/Los_Angeles')) for f in frames]
    strip = Image.new('RGB',(1600,240),'#111316')
    draw = ImageDraw.Draw(strip)
    draw.text((12,10),f'Demo: {title} · {stamps[0].strftime("%b %-d, %Y")} · six arrival frames',font=font,fill='#e8eaed')
    for i, frame in enumerate(frames):
        # Every panel keeps the full scene; never substitute guessed action or ownership.
        image=Image.open(frame['path']).convert('RGB').resize((259,146))
        x=12+i*263
        strip.paste(image,(x,48))
        draw.text((x,204),stamps[i].strftime('%-I:%M:%S %p'),font=small,fill='#c7cbd0')
    strip.save(output/f'{visit_id}-strip.jpg',quality=86)
    Image.open(frames[-1]['path']).convert('RGB').resize((1600,900)).save(output/f'{visit_id}-later.jpg',quality=86)
    result[str(visit_id)]={'at':stamps[0].isoformat(),'title':title,'area':'Kitchen counter','source_visit':row['id'],'frame_times':[s.isoformat() for s in stamps],'strip':f'{visit_id}-strip.jpg','photo':f'{visit_id}-later.jpg','width':1600,'height':240}
print(json.dumps(result))
