"""Synthetic camera pixels only: presentation must never manufacture evidence."""

import hashlib
import io
import json

import pytest
from PIL import Image, ImageDraw

from wash_dishes import evidence_image as visual
from wash_dishes import filmstrip
from wash_dishes.rerender import run


def picture(color, *, head=False, sharp=True):
    image = Image.new("RGB", (1920, 1080), color)
    draw = ImageDraw.Draw(image)
    draw.rectangle((700, 700, 820, 800), fill="yellow")
    if head:
        for x in range(500, 800, 8 if sharp else 60):
            draw.line((x, 100, x, 350), fill="white", width=3)
    out = io.BytesIO()
    image.save(out, "JPEG")
    return out.getvalue()


def test_phone_text_budget_and_adaptive_stages(monkeypatch):
    from wash_dishes.sequences import render_collage

    texts = []
    original = ImageDraw.ImageDraw.text

    def record(self, xy, text, *args, **kwargs):
        texts.append((text, kwargs["font"].size))
        return original(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", record)
    manifest = {
        "obligation_id": 123456,
        "dishes": "1 cup",
        "location": "Counter (visible portion)",
        "timer_basis": "first_observed",
        "stages": [
            {"key": key, "at": at, "box": [0.36, 0.65, 0.07, 0.1], "time_basis": "capture"}
            for key, at in [
                ("before", None),
                ("using", None),
                ("leaving", "2026-11-03T14:47:00+00:00"),
                ("later", "2026-11-03T16:47:00+00:00"),
            ]
        ],
    }
    data = render_collage(manifest, {"leaving": picture("gray"), "later": picture("navy")})
    assert len(data) <= 350_000
    assert Image.open(io.BytesIO(data)).width == 1080
    assert min(size * 390 / 1080 for _, size in texts) >= 14
    content = " ".join(t for t, _ in texts)
    assert "already here at 6:47 AM" in content
    assert "2 hours later" in content
    assert not any(
        word in content
        for word in ("unavailable", "123456", "1 cup", "continuous non-washing", "visible portion")
    )
    assert sum(t == "First saved view" for t, _ in texts) == 1


def test_dish_crop_preserves_every_box_at_frame_edges():
    data = picture("gray")
    for boxes in ([[0, 0.8, 0.05, 0.12]], [[0.9, 0, 0.1, 0.1]], [[0, 0, 0.08, 0.08], [0.9, 0.9, 0.1, 0.1]]):
        x, y, xx, yy = visual.region(boxes, data)
        assert 0 <= x < xx <= 1 and 0 <= y < yy <= 1
        for bx, by, w, h in boxes:
            assert x <= bx and y <= by and xx >= bx + w and yy >= by + h


def test_distinct_people_keep_sharp_larger_views_and_remove_repeats(monkeypatch):
    monkeypatch.setattr(visual, "_face_cascade", lambda: None)
    box = [0.26, 0.09, 0.16, 0.65]
    sharp = {"at": 2, "jpeg": picture("gray", head=True), "person_boxes": [box]}
    blur = {"at": 1, "jpeg": picture("gray"), "person_boxes": [box]}
    repeat = sharp | {"at": 3}
    assert visual.person_quality(sharp) > visual.person_quality(blur)
    chosen = visual.distinct_people([blur, sharp, repeat])
    assert sharp in chosen and repeat not in chosen


def test_already_here_never_displays_a_nearby_visitor(monkeypatch):
    labels = []
    original = visual.render

    def record(title, subtitle, panels, notes):
        labels.extend(p["label"] for p in panels)
        return original(title, subtitle, panels, notes)

    monkeypatch.setattr(visual, "render", record)
    item = {"box": [0.36, 0.65, 0.07, 0.1], "location": "Counter"}
    frames = [
        {"at": i + 100, "jpeg": picture(color), "person_boxes": [[0.2, 0.1, 0.2, 0.7]]}
        for i, color in enumerate(("red", "blue"))
    ]
    filmstrip.render(item, frames, already_at=100)
    assert labels == ["Dish detail"]


def test_rerender_dry_run_is_read_only_and_apply_preserves_review(store, media):
    from test_sequences import captured

    from wash_dishes.sequences import STAGES, SequenceCollection

    item = captured(store, media)
    collection = SequenceCollection(store, media)
    collection.refresh()
    saved = collection.get(item["id"])
    collection.review(item["id"], saved["fingerprint"], {k: True for k in STAGES})

    def state():
        return list(store.conn.iterdump()), {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in media.media.iterdir() if p.is_file()
        }

    before = state()
    result = run(store.root)
    assert result["dry_run"] and result["actions"] == [{"kind": "sequence", "target": item["id"]}]
    assert state() == before
    result = run(store.root, apply=True)
    assert len(result["updated"]) == 1
    after = collection.get(item["id"])
    assert after["checks"] == {k: True for k in STAGES} and after["status"] == "ready"
    assert store.obligation(item["id"])["resident_id"] is None
    assert after["stages"] == saved["stages"]


def test_rerender_missing_root_never_creates_database(tmp_path):
    with pytest.raises(Exception):
        run(tmp_path / "does-not-exist")
    assert not (tmp_path / "does-not-exist").exists()


def test_source_frames_never_replace_stage_with_distant_or_smaller_photo(monkeypatch, tmp_path):
    from wash_dishes.render_sources import SourceFrames

    data = picture("gray")
    monkeypatch.setattr(filmstrip, "footage_segments", lambda _: [])
    monkeypatch.setattr(filmstrip, "_photo_at", lambda _, at: (at + 1, data))
    source = SourceFrames(tmp_path)
    assert source.best(100, data)[1]["source"] == "saved_still"
    small = io.BytesIO()
    Image.new("RGB", (320, 180)).save(small, "JPEG")
    monkeypatch.setattr(filmstrip, "_photo_at", lambda _, at: (at, small.getvalue()))
    assert SourceFrames(tmp_path).best(100, data)[0] == data
    monkeypatch.setattr(filmstrip, "_photo_at", lambda _, at: (at, data))
    replacement, meta = SourceFrames(tmp_path).best(100, small.getvalue())
    assert replacement == data and meta["source"] == "buffer"
    assert meta["timestamp_estimated"]


def test_simultaneous_people_are_not_replaced_by_only_the_largest_visitor(monkeypatch):
    monkeypatch.setattr(visual, "_face_cascade", lambda: None)
    group = {"at": 1, "jpeg": picture("gray"), "person_boxes": [[0.1, 0.1, 0.1, 0.3], [0.6, 0.1, 0.1, 0.3]]}
    single = {"at": 2, "jpeg": picture("blue", head=True), "person_boxes": [[0.1, 0.1, 0.4, 0.8]]}
    assert group in visual.distinct_people([single, group])


def test_filmstrip_rerender_preserves_membership_identity_and_expiry(store, media, monkeypatch):
    from datetime import timedelta

    from wash_dishes.store import stamp, utcnow

    now = utcnow()
    item = store.create_obligation(
        None, [{"kind": "pan", "quantity": 1}], source="vision", candidate=True, box=[0.36, 0.65, 0.07, 0.1]
    )
    data = picture("gray")
    at = now.timestamp()
    frames = [
        {"at": at, "jpeg": data, "person_boxes": []},
        {"at": at + 10, "jpeg": picture("blue"), "person_boxes": []},
    ]
    meta = {
        "obligation_ids": [item["id"]],
        "canonical_obligation_ids": [item["id"]],
        "identity_evidence_id": "unchanged-identity-anchor",
        "dish_boxes": {str(item["id"]): item["box"]},
        "window": {"before": at, "after": at + 10, "arrival_recorded": False, "first_visible_at": at},
        "frames": [{k: v for k, v in f.items() if k != "jpeg"} for f in frames],
    }
    with store.conn:
        store.conn.execute(
            "insert into evidence values(?,?,?,'filmstrip',?,?,'arrival',0,?)",
            (
                "arrival-test",
                item["id"],
                "arrival-test.jpg",
                stamp(now),
                stamp(now + timedelta(days=7)),
                json.dumps(meta),
            ),
        )
        store.conn.execute(
            "insert into arrival_visits values(?,?,?,?)",
            ("test", "arrival-test", stamp(now), json.dumps(meta)),
        )
        store.conn.execute("insert into arrival_visit_members values(?,?)", (item["id"], "test"))
    media.safe_path("arrival-test.jpg").write_bytes(data)
    monkeypatch.setattr(filmstrip, "display_frames", lambda *args, **kw: frames)
    before = store.evidence("arrival-test")
    result = run(store.root, apply=True)
    assert result["updated"][0]["kind"] == "filmstrip"
    after = store.evidence("arrival-test")
    capture = json.loads(after["capture"])
    assert capture["identity_evidence_id"] == meta["identity_evidence_id"]
    assert capture["obligation_ids"] == meta["obligation_ids"]
    assert after["id"] == before["id"] and after["expires_at"] == before["expires_at"]
    assert store.obligation(item["id"])["arrival_visit_id"] == "test"
    assert capture["image_width"] == 1080


def test_partial_footage_never_relabels_a_person_frame_as_the_before(monkeypatch, tmp_path):
    from wash_dishes.rerender import ReadJournal

    monkeypatch.setattr(filmstrip, "footage_segments", lambda _: [])
    monkeypatch.setattr(filmstrip, "_photo_at", lambda _, at: (at, picture("gray")) if at > 100 else None)
    window = {"before": 100, "after": 110, "arrival_recorded": True}
    found = {"window": window, "frames": [{"at": 100, "person_boxes": []}, {"at": 110, "person_boxes": []}]}
    assert (
        filmstrip.display_frames(
            ReadJournal(tmp_path / "absent.db"),
            tmp_path,
            found,
            [[0.3, 0.5, 0.1, 0.1]],
            deadline=float("inf"),
        )
        == []
    )


def test_new_render_design_preserves_prior_semantic_reviews(store, media):
    from test_sequences import captured

    from wash_dishes.sequences import STAGES, SequenceCollection

    item = captured(store, media)
    collection = SequenceCollection(store, media)
    collection.refresh()
    saved = collection.get(item["id"])
    collection.review(item["id"], saved["fingerprint"], {k: True for k in STAGES})
    old = json.loads(
        store.conn.execute(
            "select manifest from evidence_sequences where obligation_id=?", (item["id"],)
        ).fetchone()[0]
    )
    old.pop("render_version")
    with store.conn:
        store.conn.execute(
            "update evidence_sequences set manifest=? where obligation_id=?", (json.dumps(old), item["id"])
        )
    assert collection.refresh() == 1
    updated = collection.get(item["id"])
    assert updated["fingerprint"] == saved["fingerprint"]
    assert updated["status"] == "ready" and all(updated["checks"].values())
    assert collection.refresh() == 0
