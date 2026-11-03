"""Private local-only two-column arrival grids. Never write camera images into the repo."""
import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from PIL import Image, ImageDraw, ImageFont, ImageOps

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
    font = ImageFont.truetype(str(Path(__file__).parents[1]/'fonts/MonaSans.woff2'), 42)
    small = ImageFont.truetype(str(Path(__file__).parents[1]/'fonts/MonaSans.woff2'), 27)
    font.set_variation_by_axes([100,600])
    small.set_variation_by_axes([100,450])
except OSError:
    font = ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf',42)
    small = ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf',27)
result = {}
for index, (visit_id,title) in enumerate(titles.items()):
    row = rows[1643 if index % 2 == 0 else 1662]
    frames = row['frames']
    stamps = [datetime.fromisoformat(f['utc_estimated']).astimezone(ZoneInfo('America/Los_Angeles')) for f in frames]
    # Match Lane A's renderer: 1200×1668, two 564×423 panels per row.
    grid = Image.new('RGB',(1200,1668),'#111316')
    draw = ImageDraw.Draw(grid)
    draw.text((24,22),f'Demo: {title} · arrival context',font=font,fill='#e8eaed')
    draw.text((24,80),f'{stamps[0].strftime("%b %-d, %Y")} · Kitchen counter',font=small,fill='#c7cbd0')
    draw.text((24,126),'Example names and results; review the photos',font=small,fill='#c7cbd0')
    for i, frame in enumerate(frames):
        # Keep the full scene, letterboxed inside each panel, without guessed marks.
        image=ImageOps.contain(Image.open(frame['path']).convert('RGB'),(564,423))
        x,y=24+(i%2)*588,186+(i//2)*491
        grid.paste(image,(x+(564-image.width)//2,y+(423-image.height)//2))
        draw.text((x,y+435),stamps[i].strftime('%-I:%M:%S %p'),font=small,fill='#e8eaed')
    grid.save(output/f'{visit_id}-strip.jpg',quality=86)
    Image.open(frames[-1]['path']).convert('RGB').resize((1600,900)).save(output/f'{visit_id}-later.jpg',quality=86)
    result[str(visit_id)]={'at':stamps[0].isoformat(),'title':title,'area':'Kitchen counter','source_visit':row['id'],'frame_times':[s.isoformat() for s in stamps],'strip':f'{visit_id}-strip.jpg','photo':f'{visit_id}-later.jpg','width':1200,'height':1668}
print(json.dumps(result))
