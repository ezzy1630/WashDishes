from datetime import timedelta

from wash_dishes.media import Media
from wash_dishes.store import utcnow


def test_requested_clip_survives_restart_and_failed_export_retries(store, media, monkeypatch):
    item = store.create_obligation(None, [{'kind': 'pan', 'quantity': 1}])
    at = utcnow() - timedelta(minutes=1)
    media.request_event_clip(item['id'], 'Handling', at=at)
    restarted = Media(store.root, store)
    assert len(restarted.pending_clips) == 1
    monkeypatch.setattr(restarted, 'save_recent_clip', lambda *args, **kwargs: None)
    restarted.flush_event_clips(now=utcnow() + timedelta(minutes=1))
    assert len(restarted.pending_clips) == 1
    assert restarted.capture_status()['retrying'] == 1


def test_clip_request_is_idempotent_and_failures_are_visible(store, media):
    item = store.create_obligation(None, [{'kind': 'pan', 'quantity': 1}])
    at = utcnow() - timedelta(days=2)
    for _ in range(2):
        media.request_event_clip(item['id'], 'Handling', at=at)
    assert len(media.pending_clips) == 1
    media.flush_event_clips(now=utcnow())
    assert media.capture_status()['failed'] == 1
