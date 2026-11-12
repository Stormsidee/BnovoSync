from threading import Event, Thread
from django.utils import timezone

from .syncer import run_sync
from ..models import IntegrationSettings, SyncRun

_started = False
_stop_event = Event()


def start_auto_sync():
    global _started
    if _started:
        return
    _started = True
    Thread(target=_loop, daemon=True).start()


def _loop():
    while not _stop_event.wait(30):
        try:
            settings = IntegrationSettings.get_solo()
        except Exception:
            continue
        if not settings.auto_sync_enabled:
            continue
        interval = max(1, settings.auto_sync_interval_minutes or 30)
        last = settings.last_auto_sync_at
        if last and (timezone.now() - last).total_seconds() < interval * 60:
            continue
        if SyncRun.objects.filter(status='running').exists():
            continue
        settings.last_auto_sync_at = timezone.now()
        settings.save(update_fields=['last_auto_sync_at'])
        Thread(target=run_sync, kwargs={'triggered_by': 'schedule'}, daemon=True).start()

