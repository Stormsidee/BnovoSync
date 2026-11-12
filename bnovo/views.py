from threading import Thread
from datetime import timedelta
import secrets

from django.shortcuts import render, redirect
from django.views.decorators.http import require_http_methods
from django.utils import timezone

from .forms import IntegrationSettingsForm
from .models import ApiToken, Arrival, EndpointSetting, IntegrationSettings, SyncLog, SyncRun, WorkerLease
from .sync.arrivals import run_arrival_aggregation
from .sync.syncer import run_arrival_raw_sync


def _run_arrivals_pipeline(triggered_by='manual'):
    raw_run = run_arrival_raw_sync(triggered_by=triggered_by)
    if raw_run and raw_run.status == 'cancelled':
        return
    run_arrival_aggregation(triggered_by=triggered_by)
 
@require_http_methods(['GET', 'POST']) 
def dashboard(request):
    settings = IntegrationSettings.get_solo()
    form = IntegrationSettingsForm(instance=settings)
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'save_settings':
            form = IntegrationSettingsForm(request.POST, instance=settings)
            if form.is_valid():
                form.save()
        elif action == 'queue_sync':
            Thread(target=_run_arrivals_pipeline, kwargs={'triggered_by': 'manual'}, daemon=True).start()
        elif action == 'toggle_autosync':
            settings.auto_sync_enabled = not settings.auto_sync_enabled
            settings.save(update_fields=['auto_sync_enabled', 'updated_at'])
        elif action == 'toggle_aggregation':
            settings.aggregation_enabled = not settings.aggregation_enabled
            settings.save(update_fields=['aggregation_enabled', 'updated_at'])
        return redirect('dashboard')
    runs = SyncRun.objects.order_by('-created_at')[:20]
    logs = SyncLog.objects.order_by('-created_at')[:50] 
    arrivals_count = Arrival.objects.count()
    worker_leases = WorkerLease.objects.order_by('code')
    return render(
        request,
        'bnovo/dashboard.html',
        {
            'form': form,
            'runs': runs,
            'logs': logs,
            'arrivals_count': arrivals_count,
            'worker_leases': worker_leases,
        },
    )


@require_http_methods(['GET', 'POST'])
def endpoints(request):
    if request.method == 'POST':
        for code in request.POST.getlist('endpoint_code'):
            enabled = request.POST.get(f'endpoint_enabled_{code}') == 'on'
            setting, _ = EndpointSetting.objects.get_or_create(code=code)
            setting.enabled = enabled
            setting.name = request.POST.get(f'endpoint_name_{code}', '')[:255]
            setting.description = request.POST.get(f'endpoint_description_{code}', '')
            setting.save()
        return redirect('endpoints')

    from .sync.endpoints import ENDPOINTS
    endpoint_rows = []
    for endpoint in ENDPOINTS:
        setting, created = EndpointSetting.objects.get_or_create(
            code=endpoint['code'],
            defaults={
                'name': endpoint.get('name', ''),
                'description': endpoint.get('description', ''),
                'enabled': bool(endpoint.get('enabled')),
            },
        )
        changed = False
        if not setting.name and endpoint.get('name'):
            setting.name = endpoint.get('name', '')
            changed = True
        if not setting.description and endpoint.get('description'):
            setting.description = endpoint.get('description', '')
            changed = True
        if changed:
            setting.save(update_fields=['name', 'description', 'updated_at'])
        endpoint_rows.append({
            'code': setting.code,
            'name': setting.name,
            'description': setting.description,
            'enabled': setting.enabled,
        })

    return render(
        request,
        'bnovo/endpoints.html',
        {'endpoint_rows': endpoint_rows},
    )


@require_http_methods(['GET'])
def logs(request):
    logs = SyncLog.objects.order_by('-created_at')[:200]
    return render(request, 'bnovo/logs.html', {'logs': logs})


@require_http_methods(['POST'])
def stop_run(request, run_id):
    run = SyncRun.objects.filter(pk=run_id).first()
    if run and run.status == 'running':
        run.cancel_requested = True
        run.status = 'cancelled'
        run.finished_at = timezone.now()
        if not run.note:
            run.note = 'Cancelled by user'
        run.save(update_fields=['cancel_requested', 'status', 'finished_at', 'note'])
        SyncLog.objects.create(
            run=run,
            level='warning',
            endpoint='sync',
            message='Cancellation requested by user',
        )
    return redirect('dashboard')


@require_http_methods(['GET', 'POST'])
def tokens(request):
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'create_token':
            ttl = request.POST.get('ttl')
            ttl_map = {
                '1h': timedelta(hours=1),
                '1d': timedelta(days=1),
                '1m': timedelta(days=30),
                '3m': timedelta(days=90),
            }
            expires_at = None
            if ttl in ttl_map:
                expires_at = timezone.now() + ttl_map[ttl]
            token_value = secrets.token_urlsafe(32)
            ApiToken.objects.create(
                token=token_value,
                expires_at=expires_at,
            )
            return redirect('tokens')
        if action == 'revoke_token':
            token_id = request.POST.get('token_id')
            ApiToken.objects.filter(pk=token_id, revoked_at__isnull=True).update(
                revoked_at=timezone.now()
            )
            return redirect('tokens')
    tokens = ApiToken.objects.order_by('-created_at')[:100]
    return render(request, 'bnovo/tokens.html', {'tokens': tokens})
