"""Small chat-ready weekly result, drawn from the same league result as the dashboard."""
from __future__ import annotations

from datetime import date, timedelta
from io import BytesIO

from PIL import Image, ImageDraw

from .collage import INK, MUTED, PAPER, _fit, _type

SITE = 'https://highest-house.vercel.app'
WIDTH = 1080


def render(data: dict, *, demo: bool = False) -> bytes:
    week, rules = data['week'], data['rules']
    first = date.fromisoformat(week['start'])
    last = date.fromisoformat(week['end']) - timedelta(days=1)
    canvas = Image.new('RGB', (WIDTH, 1700 + 220 * len(data['standings'])), PAPER)
    draw = ImageDraw.Draw(canvas)
    margin, usable, y = 64, WIDTH - 128, 56

    def line(text, size=36, color=INK, weight=450):
        nonlocal y
        value, font = _fit(draw, text, range(size, 23, -2), weight, usable)
        draw.text((margin, y), value, fill=color, font=font)
        y += size + 18

    def paragraph(text, size=32, color=MUTED):
        nonlocal y
        font = _type(size)
        words, row = text.split(), ''
        for word in words:
            candidate = f'{row} {word}'.strip()
            if row and draw.textlength(candidate, font=font) > usable:
                line(row, size, color)
                row = word
            else:
                row = candidate
        if row:
            line(row, size, color)

    line('Highest House' + (' · DEMO' if demo else ''), 40, weight=650)
    line(f'{first.strftime("%b")} {first.day} – {last.strftime("%b")} {last.day}, {last.year}', 52, weight=650)
    line('Final weekly result' if week['closed'] else 'Week in progress · points can change', 30, MUTED)
    y += 24
    rows = [r for r in data['standings'] if r['points'] > 0]
    for person in rows[:3]:
        label, font = _fit(draw, person['name'], range(48, 25, -2), 600, usable - 220)
        draw.text((margin, y), label, font=font, fill=INK)
        draw.text((WIDTH - margin, y), f"{person['points']} pt", font=_type(48, 650), fill=INK, anchor='ra')
        y += 82
        draw.line((margin, y, WIDTH - margin, y), fill='#30363d', width=2)
        y += 24
    if len(rows) > 3:
        paragraph('Also scoring: ' + ', '.join(f"{p['name']} {p['points']} pt" for p in rows[3:]), 28)
    if len(rows) < len(data['standings']):
        line('Everyone else 0' if rows else 'Everyone 0 points', 32, MUTED)
    y += 32
    line('Kitchen duty result' if week['closed'] else 'Next kitchen duty · if the week ended now', 32, MUTED)
    chosen, current = data['pick'], data['on_duty']
    name = chosen['name'] or (current['name'] if current else 'Nobody assigned')
    line(name if rules['enabled'] else 'Kitchen duty is off', 56, weight=650)
    paragraph(chosen['reason'])
    if rules['enabled'] and not chosen['resident_id']:
        paragraph(f"{current['name']} stays on kitchen duty." if current else 'No current duty assignment to carry forward.')
    y += 22
    paragraph(f"Pan {rules['weights']['pan']} pt · pot {rules['weights']['pot']} pt · other {rules['weights']['default']} pt. Pick needs {rules['min_points']} points and a {rules['min_lead']}-point lead.", 28)
    if not week['closed']:
        paragraph('Names and disputes stay open until Monday noon, house time.', 28)
    y += 26
    line(SITE, 34, weight=600)
    image = canvas.crop((0, 0, WIDTH, y + 40))
    out = BytesIO()
    image.save(out, 'JPEG', quality=88, optimize=True)
    return out.getvalue()
