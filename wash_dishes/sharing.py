"""A finished photo is a presentation of observations, never an automatic accusation."""
from __future__ import annotations

import io

from PIL import Image, ImageDraw, ImageOps

from .notify import timezone
from .store import parse_stamp
from .vision import box_iou


def readiness(item: dict, sequence: dict | None, decision: dict) -> tuple[bool, str]:
    if item['archived_at'] or item['lifecycle'] in ('dismissed', 'disputed') or decision.get('condition') in ('clean', 'not_dish'):
        return False, 'This record is not a dirty-dish photo.'
    if not sequence or sequence.get('stale') or not sequence.get('image_url'):
        return False, 'The saved photos are updating or unavailable.'
    if sequence.get('status') == 'rejected' or any(v is False for v in sequence.get('checks', {}).values()):
        return False, 'A photo in this sequence was marked incorrect.'
    if item['review_pending'] or sequence.get('continuity_events'):
        return False, 'The dish could not be followed reliably between views.'
    if not sequence.get('complete'):
        missing = [s['key'] for s in sequence.get('stages', []) if not s.get('sha256')]
        if set(missing) <= {'later'} and not sequence.get('followup_captured'):
            return False, 'Waiting for the two-hour photo.'
        return False, 'Photos do not clearly box this dish.' if sequence.get('captured', 0) >= 4 else 'Earlier photos are not attached yet.'
    stages = {s['key']:s for s in sequence['stages']}
    left, later = stages['leaving'], stages['later']
    seconds = (parse_stamp(later['at'])-parse_stamp(left['at'])).total_seconds()
    if seconds < 7200 or not sequence.get('followup_captured'):
        return False, 'Waiting for the two-hour photo.'
    if box_iou(left['box'], later['box']) < .3:
        return False, 'The later photo does not reliably match the dish’s position.'
    return True, ''


WIDTH, GAP, PANEL_H = 1200, 6, 447   # Two 4:3 panels per row; 1200px stays sharp after chat recompression.
INK, PAPER, MUTED, DISH, EARLIER = '#0e1012', '#f3f4f5', '#aab1b9', '#ffd84a', '#74cfff'
def _type(size: int, weight: int = 400):
    # Use the static system face for raster exports. The Jetson's variable-WOFF2
    # renderer produces overlapping glyphs despite reporting valid text metrics.
    from .sequences import _font
    return _font(size, weight >= 600)


def _clock(at: str) -> str:
    return parse_stamp(at).astimezone(timezone()).strftime('%-I:%M %p')


def _title(dishes: str) -> str:
    # "1 pan" reads as data; a single item is just "Pan".
    return (dishes[2:] if dishes.startswith('1 ') and ',' not in dishes else dishes).capitalize()


def _fit(draw, text: str, font, width: float) -> str:
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text+'…', font=font) > width:
        text = text[:-1]
    return text.rstrip()+'…'


def _wrap(draw, text: str, font, width: int, lines: int) -> list[str]:
    out = []
    for word in text.split():
        if out and draw.textlength(f'{out[-1]} {word}', font=font) <= width:
            out[-1] += f' {word}'
        else:
            out.append(word)
    if len(out) > lines:
        out = out[:lines]
        while draw.textlength(out[-1]+'…', font=font) > width and ' ' in out[-1]:
            out[-1] = out[-1].rsplit(' ', 1)[0]
        out[-1] += '…'
    return [_fit(draw, line, font, width) for line in out]


def _crop(boxes: list, aspect: float) -> tuple[float, float, float, float]:
    """One shared region for every panel: the dish boxes with context, more of it above,
    where hands and the person handling the dish usually are."""
    x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x1, y1 = max(b[0]+b[2] for b in boxes), max(b[1]+b[3] for b in boxes)
    w = min(1, max(.36, x1-x0+.2, (y1-y0+.2)*aspect))
    h = min(1, w/aspect)
    w = h*aspect
    left = min(max((x0+x1)/2-w/2, 0), 1-w)
    top = min(max((y0+y1)/2-h*.68, 0), 1-h)
    return left, top, left+w, top+h


def _chip(draw, xy: tuple[int, int], label: str, at: str, width: int) -> None:
    for size in range(34, 23, -1):
        bold, plain = _type(size, 650), _type(size, 450)
        text_w = draw.textlength(label, font=bold)+draw.textlength(f'  {at}', font=plain)
        if text_w <= width-28:
            break
    label = _fit(draw, label, bold, width-28-draw.textlength(f'  {at}', font=plain))
    text_w = draw.textlength(label, font=bold)+draw.textlength(f'  {at}', font=plain)
    x, y = xy
    draw.rounded_rectangle((x, y, x+text_w+28, y+size+20), radius=10, fill=(14, 16, 18, 215))
    draw.text((x+14, y+10), label, font=bold, fill=PAPER)
    draw.text((x+14+draw.textlength(label, font=bold), y+10), f'  {at}', font=plain, fill='#d5d9de')


def render_share(manifest: dict, pictures: dict[str, bytes]) -> bytes:
    stages = {s['key']: s for s in manifest['stages']}
    left, later = stages['leaving'], stages['later']
    minutes = int((parse_stamp(later['at'])-parse_stamp(left['at'])).total_seconds()//60)
    elapsed = f'{minutes//60}h {minutes%60:02d}m'
    panel_w = (WIDTH-GAP)//2
    probe = ImageDraw.Draw(Image.new('RGB', (1, 1)))
    title_font = _type(46, 650)
    date = parse_stamp(later['at']).astimezone(timezone()).strftime('%a %b %-d')
    room = WIDTH-64-int(probe.textlength(date, font=_type(30, 450)))
    # Shorten the dish list, never the place: the location is what housemates recognise.
    dishes, place = _title(manifest['dishes']).split(), manifest.get('location') or 'Kitchen'
    title = _wrap(probe, f"{' '.join(dishes)} · {place}", title_font, room, 3)
    while len(title) > 2 and len(dishes) > 1:
        dishes = dishes[:-1]
        title = _wrap(probe, f"{' '.join(dishes).rstrip(',')}… · {place}", title_font, room, 3)
    title = title[:2]
    head = 30+len(title)*56+58
    canvas = Image.new('RGB', (WIDTH, head+2*PANEL_H+GAP+62), INK)
    draw = ImageDraw.Draw(canvas, 'RGBA')
    for i, line in enumerate(title):
        draw.text((28, 26+i*56), line, font=title_font, fill=PAPER)
    draw.text((WIDTH-28, 36), date, font=_type(30, 450), fill=MUTED, anchor='ra')
    left_label = 'Observed out' if manifest.get('timer_basis') == 'first_observed' else 'Left out'
    draw.text((28, 24+len(title)*56+4), f'{left_label} {_clock(left["at"])} · same spot {elapsed} later',
              font=_type(34, 550), fill=DISH)

    # Panels share one crop so the four views compare directly; the untouched full scenes stay in the record.
    with Image.open(io.BytesIO(pictures['later'])) as src:
        source_aspect = src.height/src.width
    x0, y0, x1, y1 = _crop([s['box'] for s in manifest['stages'] if s['box']], panel_w/PANEL_H*source_aspect)
    name = manifest.get('visible_person')
    labels = {'before': 'Earlier', 'using': f'Activity · {name.title()}' if name else 'Activity',
              'leaving': left_label, 'later': f'{elapsed} later'}
    for i, stage in enumerate(manifest['stages']):
        px, py = (i%2)*(panel_w+GAP), head+(i//2)*(PANEL_H+GAP)
        with Image.open(io.BytesIO(pictures[stage['key']])) as src:
            original = ImageOps.exif_transpose(src).convert('RGB')
        bounds = (round(x0*original.width), round(y0*original.height), round(x1*original.width), round(y1*original.height))
        scene = original.crop(bounds).resize((panel_w, PANEL_H), Image.Resampling.LANCZOS)
        canvas.paste(scene, (px, py))
        if stage['box']:
            a, b, w, h = stage['box']
            sx, sy = panel_w/(x1-x0), PANEL_H/(y1-y0)
            rect = (px+(a-x0)*sx, py+(b-y0)*sy, px+(a+w-x0)*sx, py+(b+h-y0)*sy)
            draw.rectangle(rect, outline=INK, width=9)
            draw.rectangle(rect, outline=EARLIER if stage['key'] == 'before' else DISH, width=5)
        _chip(draw, (px+14, py+14), labels[stage['key']], _clock(stage['at']), panel_w-28)
    draw.text((28, canvas.height-45), 'Camera photos. Activity between photos is not shown.', font=_type(27, 450), fill=MUTED)
    out = io.BytesIO()
    canvas.save(out, 'JPEG', quality=86, optimize=True)
    return out.getvalue()
