import time
import traceback
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from ..models import IntegrationSettings, SyncLog, SyncRun, WorkerLease
from .arrivals import run_arrival_aggregation
from .syncer import run_arrival_raw_sync


WORKER_RAW = 'raw_arrivals'
WORKER_AGGREGATION = 'arrival_aggregation'


def _settings_for_worker(settings, worker_code):
    if worker_code == WORKER_RAW:
        return settings.auto_sync_enabled, max(1, int(settings.auto_sync_interval_minutes or 10)), 'last_auto_sync_at'
    if worker_code == WORKER_AGGREGATION:
        return settings.aggregation_enabled, max(1, int(settings.aggregation_interval_minutes or 10)), 'last_aggregation_sync_at'
    raise ValueError(f'Unknown worker: {worker_code}')


def _job_code_for_worker(worker_code):
    if worker_code == WORKER_RAW:
        return 'raw_sync'
    if worker_code == WORKER_AGGREGATION:
        return 'arrival_aggregation'
    raise ValueError(f'Unknown worker: {worker_code}')


def _lease_ttl(interval_minutes):
    return max(300, interval_minutes * 60 * 2)


def _stale_run_timeout(interval_minutes):
    return max(1800, _lease_ttl(interval_minutes) * 3)


def acquire_worker_lease(worker_code, ttl_seconds):
    now = timezone.now()
    with transaction.atomic():
        lease, _ = WorkerLease.objects.select_for_update().get_or_create(code=worker_code)
        if lease.locked_until and lease.locked_until > now:
            return None
        lease.locked_until = now + timedelta(seconds=ttl_seconds)
        lease.last_started_at = now
        lease.last_error = ''
        lease.save(update_fields=['locked_until', 'last_started_at', 'last_error'])
        return lease


def release_worker_lease(worker_code, *, error=''):
    now = timezone.now()
    updates = {
        'locked_until': None,
        'last_finished_at': now,
        'last_error': error or '',
    }
    if not error:
        updates['last_success_at'] = now
    WorkerLease.objects.filter(code=worker_code).update(**updates)


def should_run_worker(settings, worker_code, *, force=False):
    enabled, interval_minutes, last_field = _settings_for_worker(settings, worker_code)
    if not enabled and not force:
        return False, interval_minutes, last_field
    if force:
        return True, interval_minutes, last_field
    last_run_at = getattr(settings, last_field)
    if not last_run_at:
        return True, interval_minutes, last_field
    elapsed = timezone.now() - last_run_at
    return elapsed.total_seconds() >= interval_minutes * 60, interval_minutes, last_field


def _append_note(note, extra_note):
    note = (note or '').strip()
    if not note:
        return extra_note
    if extra_note in note:
        return note
    return f'{note} | {extra_note}'


def recover_stale_runs(worker_code, interval_minutes):
    job_code = _job_code_for_worker(worker_code)
    running_runs = list(
        SyncRun.objects.filter(job_code=job_code, status='running').order_by('started_at', 'created_at')
    )
    if not running_runs:
        return 0

    now = timezone.now()
    lease = WorkerLease.objects.filter(code=worker_code).first()
    if lease and lease.locked_until and lease.locked_until > now:
        return 0

    stale_before = now - timedelta(seconds=_stale_run_timeout(interval_minutes))
    recovered = 0
    for run in running_runs:
        started_at = run.started_at or run.created_at
        if started_at and started_at > stale_before:
            continue
        run.status = 'failed'
        run.finished_at = now
        run.note = _append_note(run.note, 'Recovered from stale running state')
        run.save(update_fields=['status', 'finished_at', 'note'])
        SyncLog.objects.create(
            run=run,
            level='error',
            endpoint=job_code,
            message='Run was marked as failed automatically because it was stuck in running state without an active worker lease.',
        )
        recovered += 1
    return recovered


def run_worker_once(worker_code, *, force=False):
    settings = IntegrationSettings.get_solo()
    should_run, interval_minutes, last_field = should_run_worker(settings, worker_code, force=force)
    if not should_run:
        return None
    recover_stale_runs(worker_code, interval_minutes)
    job_code = _job_code_for_worker(worker_code)
    if SyncRun.objects.filter(job_code=job_code, status='running').exists():
        return None

    lease = acquire_worker_lease(worker_code, _lease_ttl(interval_minutes))
    if lease is None:
        return None

    try:
        if worker_code == WORKER_RAW:
            result = run_arrival_raw_sync(triggered_by='schedule')
        else:
            result = run_arrival_aggregation(triggered_by='schedule')
        settings = IntegrationSettings.get_solo()
        setattr(settings, last_field, timezone.now())
        settings.save(update_fields=[last_field, 'updated_at'])
        release_worker_lease(worker_code)
        return result
    except Exception as exc:
        release_worker_lease(worker_code, error=str(exc))
        raise


def run_worker_loop(worker_code, *, poll_seconds=15, force_first_run=False):
    first_iteration = True
    while True:
        try:
            run_worker_once(worker_code, force=force_first_run and first_iteration)
        except Exception:
            print(f'[{timezone.now().isoformat()}] worker {worker_code} crashed during iteration', flush=True)
            traceback.print_exc()
        first_iteration = False
        time.sleep(max(1, int(poll_seconds)))
