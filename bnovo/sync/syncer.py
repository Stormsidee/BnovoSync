from copy import deepcopy
from datetime import date, datetime, time, timedelta
import uuid
import os

from django.apps import apps
from django.core.exceptions import FieldDoesNotExist
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from .client import BnovoAuthError, BnovoClient, BnovoError, BnovoResponseError
from .endpoints import ENDPOINTS
from .rate_limit import RateLimiter
from ..models import Booking, BookingDetail, IntegrationSettings, RoomType, SyncLog, SyncRun


ARRIVAL_ENDPOINT_CODES = ('planning_bookings', 'booking_details')


def _parse_timestamp(value):
    if not value or not isinstance(value, str):
        return None
    dt = parse_datetime(value)
    if dt:
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.get_current_timezone())
        return dt
    d = parse_date(value)
    if d:
        dt = datetime.combine(d, time.min)
        return timezone.make_aware(dt, timezone.get_current_timezone())
    return None


def _model_has_field(model, field_name):
    try:
        model._meta.get_field(field_name)
        return True
    except FieldDoesNotExist:
        return False


def _get_item_datetime(item, keys):
    for key in keys:
        value = item.get(key)
        if value:
            return _parse_timestamp(value)
    return None


def _extract_items(data, list_path, dict_values=False):
    if isinstance(list_path, (list, tuple)):
        for path in list_path:
            items = _extract_items(data, path, dict_values=dict_values)
            if items:
                return items
        return []
    if list_path:
        for part in str(list_path).split('.'):
            if isinstance(data, dict):
                data = data.get(part, [])
            else:
                return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and dict_values:
        return list(data.values())
    if data is None:
        return []
    return [data]


def _summarize_payload(payload):
    if isinstance(payload, dict):
        return f"dict keys={list(payload.keys())[:12]}"
    if isinstance(payload, list):
        return f"list len={len(payload)}"
    return f"type={type(payload).__name__}"


def _clean_params(params):
    if not params:
        return None
    cleaned = {}
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        cleaned[key] = value
    return cleaned or None


def _format_date(value):
    if not value:
        return None
    return value.strftime('%d.%m.%Y')


def _apply_date_range(params_or_body, date_from, date_to):
    if params_or_body is None:
        return None
    payload = deepcopy(params_or_body)
    date_from_str = _format_date(date_from)
    date_to_str = _format_date(date_to)
    for key in (
        'dfrom',
        'dto',
        'date_from',
        'date_to',
        'create_from',
        'create_to',
        'arrival_from',
        'arrival_to',
        'departure_from',
        'departure_to',
        'cancellation_from',
        'cancellation_to',
    ):
        if key not in payload or payload.get(key):
            continue
        if key.endswith('_from') or key in ('dfrom', 'date_from', 'arrival_from', 'departure_from', 'create_from', 'cancellation_from'):
            if date_from_str:
                payload[key] = date_from_str
        if key.endswith('_to') or key in ('dto', 'date_to', 'arrival_to', 'departure_to', 'create_to', 'cancellation_to'):
            if date_to_str:
                payload[key] = date_to_str
    return payload


def _get_total_pages(payload):
    if not isinstance(payload, dict):
        return 1
    page_value = payload.get('pages') or payload.get('pagination') or payload.get('page')
    if isinstance(page_value, int):
        return max(1, page_value)
    if isinstance(page_value, str) and page_value.isdigit():
        return max(1, int(page_value))
    if isinstance(page_value, dict):
        for key in ('pages', 'total_pages', 'page_count', 'totalPages'):
            value = page_value.get(key)
            if isinstance(value, int):
                return max(1, value)
            if isinstance(value, str) and value.isdigit():
                return max(1, int(value))
    return 1


def _pick_value(item, keys):
    for key in keys:
        if key in item and item.get(key) not in (None, ''):
            return item.get(key)
    return None


def _get_model(model_name):
    return apps.get_model('bnovo', model_name)


def _save_item(model, lookup, defaults):
    matches = list(model.objects.filter(**lookup).order_by('-id').values_list('id', flat=True)[:25])
    if not matches:
        create_kwargs = {**lookup, **defaults}
        model.objects.create(**create_kwargs)
        return
    obj = model.objects.get(pk=matches[0])
    for field_name, value in defaults.items():
        setattr(obj, field_name, value)
    obj.save(update_fields=list(defaults.keys()))
    if len(matches) > 1:
        model.objects.filter(pk__in=matches[1:]).delete()


def _save_items(model, items, id_key, allow_missing_id=False, fallback_prefix=''):
    saved = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_id = None
        if isinstance(id_key, (list, tuple)):
            for key in id_key:
                if item.get(key) not in (None, ''):
                    raw_id = item.get(key)
                    break
        else:
            raw_id = item.get(id_key)
        if raw_id is None and allow_missing_id:
            raw_id = f'{fallback_prefix}{uuid.uuid4().hex}'
        if raw_id is None:
            continue
        account_id = _pick_value(item, ('account_id', 'accountId', 'partner_id', 'partnerId'))
        property_id = _pick_value(item, ('property_id', 'propertyId', 'hotel_id', 'hotelId'))
        source_updated_at = _parse_timestamp(
            _pick_value(item, ('updated_at', 'updatedAt', 'modified_at', 'modifiedAt'))
        )
        lookup = {'bnovo_id': str(raw_id)}
        if property_id:
            lookup['property_id'] = str(property_id)
        defaults = {
            'account_id': str(account_id) if account_id else None,
            'property_id': str(property_id) if property_id else None,
            'payload': item,
            'source_updated_at': source_updated_at,
            'fetched_at': timezone.now(),
        }
        if _model_has_field(model, 'arrival_at'):
            defaults['arrival_at'] = _get_item_datetime(item, ('arrival', 'real_arrival', 'arrival_date'))
        if _model_has_field(model, 'departure_at'):
            defaults['departure_at'] = _get_item_datetime(item, ('departure', 'real_departure', 'departure_date'))
        if _model_has_field(model, 'created_at_source'):
            defaults['created_at_source'] = _get_item_datetime(item, ('create_date', 'created_at'))
        _save_item(model, lookup, defaults)
        saved += 1
    return saved


def _check_and_cancel(run_id):
    run = SyncRun.objects.filter(pk=run_id).only('status', 'cancel_requested', 'note').first()
    if not run:
        return True
    if run.status != 'running':
        return run.status == 'cancelled'
    if not run.cancel_requested:
        return False
    note = run.note or ''
    if 'Cancelled by user' not in note:
        note = (note + ' | ' if note else '') + 'Cancelled by user'
    run.status = 'cancelled'
    run.finished_at = timezone.now()
    run.note = note
    run.save(update_fields=['status', 'finished_at', 'note'])
    SyncLog.objects.create(
        run=run,
        level='warning',
        endpoint='sync',
        message='Cancelled by user',
    )
    return True


def _enabled_endpoint_map():
    from ..models import EndpointSetting

    if not EndpointSetting.objects.exists():
        return {}
    return {item.code: item.enabled for item in EndpointSetting.objects.all()}


def _resolve_sync_window(settings, date_from=None, date_to=None, rolling_days=None):
    if date_from or date_to:
        return date_from, date_to
    if settings.date_from or settings.date_to:
        return settings.date_from, settings.date_to
    if rolling_days is None:
        return None, None
    today = timezone.localdate()
    days = max(1, int(rolling_days))
    return today - timedelta(days=days - 1), today


def _login_if_needed(settings, client, run):
    ttl = timedelta(minutes=55)
    last_auth = settings.last_auth_at
    should_auth = not settings.sid or not last_auth or (timezone.now() - last_auth) > ttl
    login = settings.api_login or os.environ.get('BNOVO_USERNAME')
    password = settings.api_password or os.environ.get('BNOVO_PASSWORD')
    if not should_auth:
        return
    if not login or not password:
        SyncLog.objects.create(
            run=run,
            level='warning',
            endpoint='auth',
            message='Bnovo credentials are missing; reusing current SID if present',
        )
        return
    try:
        new_sid = client.login(login, password)
        if new_sid and new_sid != settings.sid:
            settings.sid = new_sid
        settings.last_auth_at = timezone.now()
        settings.save(update_fields=['sid', 'last_auth_at', 'updated_at'])
    except BnovoAuthError as exc:
        SyncLog.objects.create(run=run, level='error', endpoint='auth', message=str(exc))


def _selected_endpoints(endpoint_codes=None, *, respect_endpoint_settings=True):
    endpoint_map = _enabled_endpoint_map()
    selected = []
    requested_codes = set(endpoint_codes or [])
    for endpoint in ENDPOINTS:
        code = endpoint.get('code')
        if endpoint_codes and code not in requested_codes:
            continue
        if respect_endpoint_settings:
            if endpoint_map:
                if code in endpoint_map and not endpoint_map[code]:
                    continue
            elif not endpoint.get('enabled'):
                continue
        selected.append(endpoint)
    return selected


def _bump_run(run, field_name, amount=1):
    setattr(run, field_name, getattr(run, field_name) + amount)
    run.save(update_fields=[field_name])


def _log(run, level, endpoint_code, message):
    SyncLog.objects.create(run=run, level=level, endpoint=endpoint_code, message=message)


def _request_and_extract(run, client, endpoint, path, params, body):
    _bump_run(run, 'total_requests')
    data = client.request(endpoint.get('method', 'GET'), path, params=params, json=body)
    items = _extract_items(
        data,
        endpoint.get('list_path'),
        dict_values=bool(endpoint.get('dict_values')),
    )
    return data, items


def _run_iterated_endpoint(run, client, endpoint, params):
    iterate_model = _get_model(endpoint.get('iterate_model'))
    iterate_field = endpoint.get('iterate_field', 'bnovo_id')
    iterate_param = endpoint.get('iterate_param', 'id')
    ids = iterate_model.objects.values_list(iterate_field, flat=True).distinct()
    if not ids.exists():
        _log(run, 'warning', endpoint.get('code', ''), f'No IDs found in {endpoint.get("iterate_model")}.{iterate_field}')
        return
    total_saved = 0
    for raw_id in ids.iterator(chunk_size=500):
        if _check_and_cancel(run.id):
            return
        if raw_id in (None, ''):
            continue
        path = endpoint.get('path').format(**{iterate_param: str(raw_id)})
        try:
            _, items = _request_and_extract(run, client, endpoint, path, params, None)
            model = _get_model(endpoint.get('model'))
            total_saved += _save_items(
                model,
                items,
                endpoint.get('id_key', 'id'),
                allow_missing_id=endpoint.get('allow_missing_id', False),
                fallback_prefix=f"{endpoint.get('code', 'item')}-",
            )
            _bump_run(run, 'successful_requests')
        except (BnovoAuthError, BnovoResponseError, BnovoError, Exception) as exc:
            _bump_run(run, 'failed_requests')
            _log(run, 'error', endpoint.get('code', ''), f'{raw_id}: {exc}')
    _log(run, 'info', endpoint.get('code', ''), f'Saved {total_saved} items (details)')


def _run_collection_endpoint(run, client, endpoint, params, body):
    paginate = endpoint.get('paginate', False)
    page_param = endpoint.get('page_param', 'page')
    request_params = dict(params) if params else {}
    if paginate and page_param not in request_params:
        request_params[page_param] = 1
    total_saved = 0
    while True:
        if _check_and_cancel(run.id):
            return
        data, items = _request_and_extract(run, client, endpoint, endpoint.get('path'), request_params, body)
        if not items:
            _log(
                run,
                'warning',
                endpoint.get('code', ''),
                f'Empty items; list_path={endpoint.get("list_path")} payload={_summarize_payload(data)}',
            )
        model = _get_model(endpoint.get('model'))
        total_saved += _save_items(
            model,
            items,
            endpoint.get('id_key', 'id'),
            allow_missing_id=endpoint.get('allow_missing_id', False),
            fallback_prefix=f"{endpoint.get('code', 'item')}-",
        )
        _bump_run(run, 'successful_requests')
        if not paginate:
            break
        total_pages = _get_total_pages(data)
        if request_params[page_param] >= total_pages:
            break
        request_params[page_param] += 1
    _log(run, 'info', endpoint.get('code', ''), f'Saved {total_saved} items')


def _cleanup_old_booking_raw_data(settings, window_start):
    if not window_start:
        return
    window_start_dt = timezone.make_aware(datetime.combine(window_start, time.min))
    Booking.objects.filter(arrival_at__lt=window_start_dt).delete()
    Booking.objects.filter(arrival_at__isnull=True, created_at_source__lt=window_start_dt).delete()
    BookingDetail.objects.filter(arrival_at__lt=window_start_dt).delete()
    BookingDetail.objects.filter(arrival_at__isnull=True, created_at_source__lt=window_start_dt).delete()


def _window_datetimes(date_from=None, date_to=None):
    start_dt = None
    end_dt = None
    if date_from:
        start_dt = timezone.make_aware(datetime.combine(date_from, time.min))
    if date_to:
        end_dt = timezone.make_aware(datetime.combine(date_to, time.max))
    return start_dt, end_dt


def _collect_booking_ids(items, id_key):
    booking_ids = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_id = item.get(id_key)
        if raw_id in (None, ''):
            continue
        booking_id = str(raw_id)
        if booking_id in seen:
            continue
        seen.add(booking_id)
        booking_ids.append(booking_id)
    return booking_ids


def _cleanup_arrival_window_raw_data(active_booking_ids, date_from, date_to):
    start_dt, end_dt = _window_datetimes(date_from, date_to)
    if not start_dt and not end_dt:
        return

    for model in (Booking, BookingDetail):
        queryset = model.objects.all()
        if start_dt:
            queryset = queryset.filter(Q(departure_at__gte=start_dt) | Q(departure_at__isnull=True))
        if end_dt:
            queryset = queryset.filter(arrival_at__lte=end_dt)
        if active_booking_ids:
            queryset = queryset.exclude(bnovo_id__in=active_booking_ids)
        queryset.delete()


def _is_booking_details_html_response(error_message):
    return (
        'Non-JSON response: HTTP 200' in error_message
        and ('frontend_version' in error_message or 'isYandexTokenSaved' in error_message)
    )


def _booking_detail_enrichment_enabled():
    value = os.environ.get('BNOVO_ENABLE_BOOKING_DETAILS', '')
    return value.strip().lower() in ('1', 'true', 'yes', 'on')


def _sync_room_types(run, client):
    room_types_endpoint = next(endpoint for endpoint in ENDPOINTS if endpoint.get('code') == 'room_types')
    try:
        _bump_run(run, 'total_requests')
        data = client.request(
            room_types_endpoint.get('method', 'GET'),
            room_types_endpoint.get('path'),
            params=_clean_params(room_types_endpoint.get('params')),
        )
        items = _extract_items(data, room_types_endpoint.get('list_path'))
        saved = _save_items(RoomType, items, room_types_endpoint.get('id_key', 'id'))
        _bump_run(run, 'successful_requests')
        _log(run, 'info', room_types_endpoint.get('code', ''), f'Loaded {saved} room categories')
        return saved
    except (BnovoAuthError, BnovoResponseError, BnovoError, Exception) as exc:
        _bump_run(run, 'failed_requests')
        _log(run, 'error', room_types_endpoint.get('code', ''), str(exc))
        return 0


def run_sync(
    triggered_by='manual',
    *,
    endpoint_codes=None,
    date_from=None,
    date_to=None,
    rolling_days=None,
    job_code='raw_sync',
    note='',
    respect_endpoint_settings=True,
):
    settings = IntegrationSettings.get_solo()
    effective_date_from, effective_date_to = _resolve_sync_window(
        settings,
        date_from=date_from,
        date_to=date_to,
        rolling_days=rolling_days,
    )
    run = SyncRun.objects.create(
        job_code=job_code,
        status='running',
        triggered_by=triggered_by,
        started_at=timezone.now(),
        note=note,
    )
    try:
        client = BnovoClient(
            settings.base_url,
            settings.sid,
            RateLimiter(settings.rate_limit_per_min),
            timeout_seconds=settings.timeout_seconds,
            verify_ssl=settings.verify_ssl,
        )
        _login_if_needed(settings, client, run)
        cancelled = False

        for endpoint in _selected_endpoints(endpoint_codes, respect_endpoint_settings=respect_endpoint_settings):
            if _check_and_cancel(run.id):
                cancelled = True
                break
            endpoint_code = endpoint.get('code', '')
            path = endpoint.get('path')
            path_params = deepcopy(endpoint.get('path_params') or {})
            if path_params:
                missing = [key for key, value in path_params.items() if value in (None, '')]
                if missing:
                    _log(run, 'warning', endpoint_code, f'Missing path params: {", ".join(missing)}')
                    continue
                path = path.format(**path_params)

            params = _clean_params(_apply_date_range(endpoint.get('params'), effective_date_from, effective_date_to))
            body = _apply_date_range(endpoint.get('body'), effective_date_from, effective_date_to)
            requires_any = endpoint.get('requires_any') or []
            if requires_any and not any(params and params.get(key) for key in requires_any):
                _log(run, 'warning', endpoint_code, 'Missing required filters for request')
                continue

            try:
                if endpoint.get('iterate_model'):
                    _run_iterated_endpoint(run, client, {**endpoint, 'path': path}, params)
                else:
                    _run_collection_endpoint(run, client, {**endpoint, 'path': path}, params, body)
            except (BnovoAuthError, BnovoResponseError, BnovoError, Exception) as exc:
                _bump_run(run, 'failed_requests')
                _log(run, 'error', endpoint_code, str(exc))

        if cancelled or _check_and_cancel(run.id):
            return SyncRun.objects.filter(pk=run.id).first()

        final_status = 'success' if run.failed_requests == 0 else 'failed'
        run.status = final_status
        run.finished_at = timezone.now()
        run.save(update_fields=['status', 'finished_at'])

        if final_status == 'success' and set(endpoint_codes or []) == set(ARRIVAL_ENDPOINT_CODES) and effective_date_from:
            _cleanup_old_booking_raw_data(settings, effective_date_from)
        return run
    except Exception as exc:
        note = run.note or ''
        suffix = f'Unhandled error: {exc}'
        run.status = 'failed'
        run.finished_at = timezone.now()
        run.note = f'{note} | {suffix}' if note else suffix
        run.save(update_fields=['status', 'finished_at', 'note'])
        SyncLog.objects.create(
            run=run,
            level='error',
            endpoint='sync',
            message=suffix,
        )
        return run


def run_arrival_raw_sync(triggered_by='manual', window_days=None):
    settings = IntegrationSettings.get_solo()
    effective_window_days = max(1, int(window_days or settings.sync_window_days or 30))
    date_to = timezone.localdate()
    date_from = date_to - timedelta(days=effective_window_days - 1)
    note = f'arrival_window={date_from}..{date_to}'
    run = SyncRun.objects.create(
        job_code='raw_sync',
        status='running',
        triggered_by=triggered_by,
        started_at=timezone.now(),
        note=note,
    )
    try:
        client = BnovoClient(
            settings.base_url,
            settings.sid,
            RateLimiter(settings.rate_limit_per_min),
            timeout_seconds=settings.timeout_seconds,
            verify_ssl=settings.verify_ssl,
        )
        _login_if_needed(settings, client, run)

        room_types_loaded = _sync_room_types(run, client)

        planning_endpoint = next(endpoint for endpoint in ENDPOINTS if endpoint.get('code') == 'planning_bookings')
        planning_body = {
            **(planning_endpoint.get('body') or {}),
            'dfrom': date_from.isoformat(),
            'dto': date_to.isoformat(),
        }
        _bump_run(run, 'total_requests')
        planning_data = client.request(
            planning_endpoint.get('method', 'POST'),
            planning_endpoint.get('path'),
            json=planning_body,
        )
        planning_items = _extract_items(planning_data, planning_endpoint.get('list_path'))
        active_booking_ids = _collect_booking_ids(planning_items, planning_endpoint.get('id_key', 'booking_id'))
        if planning_items:
            _save_items(Booking, planning_items, planning_endpoint.get('id_key', 'booking_id'))
        else:
            _log(run, 'warning', planning_endpoint.get('code', ''), f'No bookings returned for {date_from}..{date_to}')
        _bump_run(run, 'successful_requests')
        _log(run, 'info', planning_endpoint.get('code', ''), f'Loaded {len(active_booking_ids)} active bookings')

        details_loaded = 0
        details_skipped_reason = ''
        if not _booking_detail_enrichment_enabled():
            details_skipped_reason = 'disabled'
        else:
            for booking_id in active_booking_ids:
                if _check_and_cancel(run.id):
                    return SyncRun.objects.filter(pk=run.id).first()
                try:
                    _bump_run(run, 'total_requests')
                    detail_data = client.request('GET', f'/booking/general/{booking_id}')
                    detail_items = _extract_items(detail_data, 'booking')
                    details_loaded += _save_items(BookingDetail, detail_items, 'id')
                    _bump_run(run, 'successful_requests')
                except BnovoResponseError as exc:
                    message = str(exc)
                    if _is_booking_details_html_response(message):
                        details_skipped_reason = 'html-response'
                        _log(
                            run,
                            'warning',
                            'booking_details',
                            'booking/general returned HTML instead of JSON; skipped detail enrichment for the remaining bookings.',
                        )
                        break
                    _bump_run(run, 'failed_requests')
                    _log(run, 'error', 'booking_details', f'{booking_id}: {message}')
                except (BnovoAuthError, BnovoError, Exception) as exc:
                    _bump_run(run, 'failed_requests')
                    _log(run, 'error', 'booking_details', f'{booking_id}: {exc}')

        if _check_and_cancel(run.id):
            return SyncRun.objects.filter(pk=run.id).first()

        _cleanup_arrival_window_raw_data(active_booking_ids, date_from, date_to)
        settings = IntegrationSettings.get_solo()
        settings.last_auto_sync_at = timezone.now()
        settings.save(update_fields=['last_auto_sync_at', 'updated_at'])

        final_status = 'success' if run.failed_requests == 0 else 'failed'
        run.status = final_status
        run.finished_at = timezone.now()
        run.note = (
            f'{note}; room_types={room_types_loaded}; '
            f'active_bookings={len(active_booking_ids)}; details_loaded={details_loaded}'
        )
        if details_skipped_reason:
            run.note = f'{run.note}; details_skipped={details_skipped_reason}'
        run.save(update_fields=['status', 'finished_at', 'note'])
        return run
    except Exception as exc:
        suffix = f'Unhandled error: {exc}'
        run.status = 'failed'
        run.finished_at = timezone.now()
        run.note = f'{note} | {suffix}'
        run.save(update_fields=['status', 'finished_at', 'note'])
        SyncLog.objects.create(
            run=run,
            level='error',
            endpoint='raw_sync',
            message=suffix,
        )
        return run
