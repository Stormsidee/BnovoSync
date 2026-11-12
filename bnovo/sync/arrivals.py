from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation

from django.db.models import Q
from django.utils import timezone

from ..models import Arrival, Booking, BookingDetail, IntegrationSettings, RoomType, SyncLog, SyncRun
from .syncer import _check_and_cancel, _parse_timestamp


PERSON_NAME_KEYS = (
    'full_name',
    'fullname',
    'fio',
    'name',
    'surname',
    'last_name',
    'first_name',
    'patronymic',
    'middle_name',
)


def _clean_text(value):
    if value is None:
        return ''
    return str(value).strip()


def _coalesce(data, keys, default=''):
    if not isinstance(data, dict):
        return default
    for key in keys:
        value = data.get(key)
        if value not in (None, ''):
            return value
    return default


def _to_decimal(value):
    if value in (None, '', False):
        return None
    if isinstance(value, Decimal):
        return value.quantize(Decimal('0.01'))
    if isinstance(value, (int, float)):
        return Decimal(str(value)).quantize(Decimal('0.01'))
    text = str(value).strip().replace(' ', '').replace(',', '.')
    if not text:
        return None
    for token in ('RUB', 'rub', '₽'):
        text = text.replace(token, '')
    try:
        return Decimal(text).quantize(Decimal('0.01'))
    except (InvalidOperation, ValueError):
        return None


def _to_int(value):
    if value in (None, '', False):
        return 0
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def _compose_full_name(data):
    if not isinstance(data, dict):
        return ''
    direct = _clean_text(_coalesce(data, ('full_name', 'fullname', 'fio')))
    if direct:
        return direct
    parts = [
        _clean_text(_coalesce(data, ('surname', 'last_name', 'lastname'))),
        _clean_text(_coalesce(data, ('name', 'first_name', 'firstname'))),
        _clean_text(_coalesce(data, ('patronymic', 'middle_name', 'middlename'))),
    ]
    return ' '.join(part for part in parts if part)


def _looks_like_person(data):
    if not isinstance(data, dict):
        return False
    if not any(key in data for key in PERSON_NAME_KEYS):
        return False
    if any(key in data for key in ('service_name', 'room_type_name', 'plan_name')):
        return False
    if data.get('full_name') or data.get('fullname') or data.get('fio'):
        return True
    if any(data.get(key) for key in ('surname', 'last_name', 'lastname')):
        return bool(_compose_full_name(data))
    full_name = _compose_full_name(data)
    return bool(full_name) and any(
        data.get(key) for key in ('phone', 'phone_number', 'mobile', 'email', 'guest_type', 'customer_id')
    )


def _person_to_dict(data, fallback_id=''):
    if not isinstance(data, dict):
        return {}
    return {
        'id': _clean_text(_coalesce(data, ('id', 'customer_id', 'guest_id'), fallback_id)),
        'full_name': _compose_full_name(data),
        'phone': _clean_text(_coalesce(data, ('phone', 'phone_number', 'mobile'))),
        'email': _clean_text(_coalesce(data, ('email', 'mail'))),
    }


def _walk_people(node, collected):
    if isinstance(node, dict):
        if _looks_like_person(node):
            collected.append(_person_to_dict(node))
        for value in node.values():
            _walk_people(value, collected)
    elif isinstance(node, list):
        for item in node:
            _walk_people(item, collected)


def _unique_people(people):
    unique = []
    seen = set()
    for person in people:
        if not person:
            continue
        full_name = _clean_text(person.get('full_name'))
        person_id = _clean_text(person.get('id'))
        key = person_id or full_name.lower()
        if not key:
            continue
        if key in seen:
            continue
        seen.add(key)
        unique.append(
            {
                'id': person_id,
                'full_name': full_name,
                'phone': _clean_text(person.get('phone')),
                'email': _clean_text(person.get('email')),
            }
        )
    return unique


def _extract_booking_root(payload):
    if isinstance(payload, dict) and isinstance(payload.get('booking'), dict):
        return payload.get('booking')
    if isinstance(payload, dict):
        return payload
    return {}


def _extract_customer(booking):
    customer = booking.get('customer')
    if isinstance(customer, dict) and _looks_like_person(customer):
        normalized = _person_to_dict(customer, fallback_id=_clean_text(booking.get('customer_id')))
        if normalized.get('full_name'):
            return normalized
    fallback = {
        'id': _clean_text(_coalesce(booking, ('customer_id', 'guest_id'))),
        'full_name': _compose_full_name(booking),
        'phone': _clean_text(_coalesce(booking, ('phone', 'phone_number', 'mobile'))),
        'email': _clean_text(_coalesce(booking, ('email', 'mail'))),
    }
    return fallback


def _extract_visitors(payload, customer):
    people = []
    _walk_people(payload, people)
    unique_people = _unique_people(people)
    customer_id = _clean_text(customer.get('id'))
    customer_name = _clean_text(customer.get('full_name')).lower()
    visitors = []
    for person in unique_people:
        person_id = _clean_text(person.get('id'))
        full_name = _clean_text(person.get('full_name')).lower()
        if customer_id and person_id and customer_id == person_id:
            continue
        if customer_name and full_name and customer_name == full_name:
            continue
        visitors.append(person)
    return visitors


def _first_dict(items):
    if not isinstance(items, list):
        return {}
    for item in items:
        if isinstance(item, dict):
            return item
    return {}


def _extract_room_info(booking):
    price_row = _first_dict(booking.get('prices')) or _first_dict(booking.get('prices_all'))
    return {
        'room_bnovo_id': _clean_text(
            price_row.get('room_id')
            or price_row.get('roomId')
            or booking.get('room_id')
            or booking.get('roomId')
        ),
        'room_type_bnovo_id': _clean_text(
            price_row.get('room_type_id')
            or price_row.get('roomTypeId')
            or price_row.get('dual_roomtype_id')
            or booking.get('room_type_id')
            or booking.get('roomTypeId')
            or booking.get('dual_roomtype_id')
        ),
        'room_type_name': _clean_text(
            price_row.get('room_type_name') or booking.get('room_type_name') or booking.get('room_type')
        ),
        'room_name': _clean_text(price_row.get('room_name') or booking.get('room_name')),
        'plan_name': _clean_text(price_row.get('plan_name') or booking.get('plan_name')),
    }


def _normalize_service_rows(payload):
    raw_services = payload.get('services') if isinstance(payload, dict) else None
    if not isinstance(raw_services, list):
        return []
    rows = []
    for item in raw_services:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                'name': _clean_text(item.get('name') or item.get('service_name')),
                'quantity': _clean_text(item.get('quantity')),
                'price': str(_to_decimal(item.get('price')) or ''),
            }
        )
    return [row for row in rows if row.get('name')]


def _normalize_payment_rows(payload):
    candidates = []
    if isinstance(payload, dict):
        for key in ('payments', 'payment_rows', 'finance_payments'):
            value = payload.get(key)
            if isinstance(value, list):
                candidates = value
                break
    rows = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        amount = _to_decimal(
            item.get('amount')
            or item.get('sum')
            or item.get('value')
            or item.get('paid')
        )
        rows.append(
            {
                'title': _clean_text(item.get('title') or item.get('name') or item.get('payment_type_name')),
                'amount': str(amount or ''),
                'date': _clean_text(item.get('date') or item.get('created_at')),
            }
        )
    return [row for row in rows if row.get('title') or row.get('amount')]


def _sum_payment_rows(rows):
    total = Decimal('0.00')
    found = False
    for row in rows:
        amount = _to_decimal(row.get('amount'))
        if amount is None:
            continue
        found = True
        total += amount
    return total if found else None


def _extract_amounts(booking, payload, payments):
    amount = _to_decimal(
        _coalesce(
            booking,
            ('amount', 'total_amount', 'sum', 'booking_amount'),
        )
    )
    paid_amount = _to_decimal(
        _coalesce(
            booking,
            ('paid_amount', 'paid', 'payment_total', 'payments_total', 'paid_sum'),
        )
    )
    if paid_amount is None:
        paid_amount = _sum_payment_rows(payments)
    balance_amount = _to_decimal(
        _coalesce(
            booking,
            ('balance_amount', 'balance', 'debt', 'left_to_pay'),
        )
    )
    if balance_amount is None and amount is not None and paid_amount is not None:
        balance_amount = (amount - paid_amount).quantize(Decimal('0.01'))
    return amount, paid_amount, balance_amount


def _extract_dates(booking, detail):
    arrival_at = detail.arrival_at or _parse_timestamp(
        _coalesce(booking, ('arrival', 'real_arrival', 'arrival_date'))
    )
    departure_at = detail.departure_at or _parse_timestamp(
        _coalesce(booking, ('departure', 'real_departure', 'departure_date'))
    )
    nights = None
    if arrival_at and departure_at and departure_at >= arrival_at:
        nights = max(0, (departure_at.date() - arrival_at.date()).days)
    return arrival_at, departure_at, nights


def _build_excerpt(booking, visitors, services, payments):
    return {
        'status_name': _clean_text(booking.get('status_name')),
        'source_name': _clean_text(booking.get('source_name')),
        'visitors_count': len(visitors),
        'services_count': len(services),
        'payments_count': len(payments),
    }


def build_arrival_record(detail):
    payload = detail.payload or {}
    booking = _extract_booking_root(payload)
    customer = _extract_customer(booking)
    visitors = _extract_visitors(payload, customer)
    services = _normalize_service_rows(payload)
    payments = _normalize_payment_rows(payload)
    room_info = _extract_room_info(booking)
    amount, paid_amount, balance_amount = _extract_amounts(booking, payload, payments)
    arrival_at, departure_at, nights = _extract_dates(booking, detail)
    return {
        'booking_bnovo_id': detail.bnovo_id,
        'account_id': detail.account_id,
        'property_id': detail.property_id,
        'booking_number': _clean_text(booking.get('number')),
        'room_bnovo_id': room_info['room_bnovo_id'],
        'room_type_bnovo_id': room_info['room_type_bnovo_id'],
        'customer_id': _clean_text(booking.get('customer_id')),
        'source_id': _clean_text(booking.get('source_id')),
        'source_name': _clean_text(booking.get('source_name')),
        'status_id': _clean_text(booking.get('status_id')),
        'status_name': _clean_text(booking.get('status_name')),
        'customer_full_name': _clean_text(customer.get('full_name')),
        'customer_phone': _clean_text(customer.get('phone')),
        'customer_email': _clean_text(customer.get('email')),
        'visitor_names': [person.get('full_name') for person in visitors if person.get('full_name')],
        'visitor_details': visitors,
        'services': services,
        'payments': payments,
        'payload_excerpt': _build_excerpt(booking, visitors, services, payments),
        'adults': _to_int(booking.get('adults') or payload.get('adults')),
        'children': _to_int(booking.get('children') or payload.get('children')),
        'nights': nights,
        'arrival_at': arrival_at,
        'departure_at': departure_at,
        'room_type_name': room_info['room_type_name'],
        'room_name': room_info['room_name'],
        'plan_name': room_info['plan_name'],
        'amount': amount,
        'paid_amount': paid_amount,
        'balance_amount': balance_amount,
        'created_at_source': detail.created_at_source or _parse_timestamp(booking.get('create_date')),
        'raw_updated_at': detail.source_updated_at,
        'raw_fetched_at': detail.fetched_at,
    }


def _extract_room_type_name(payload):
    return _clean_text(
        _coalesce(
            payload,
            ('name', 'title', 'room_type_name', 'roomTypeName', 'category_name'),
        )
    )


def _room_type_name_maps():
    by_property = {}
    by_id = {}
    for room_type in RoomType.objects.all().only('bnovo_id', 'property_id', 'payload'):
        name = _extract_room_type_name(room_type.payload)
        if not name:
            continue
        room_type_id = _clean_text(room_type.bnovo_id)
        property_id = _clean_text(room_type.property_id)
        if property_id:
            by_property[(room_type_id, property_id)] = name
        by_id.setdefault(room_type_id, name)
    return by_property, by_id


def _apply_room_type_name(record, by_property, by_id):
    if record.get('room_type_name'):
        return
    room_type_id = _clean_text(record.get('room_type_bnovo_id'))
    if not room_type_id:
        return
    property_id = _clean_text(record.get('property_id'))
    record['room_type_name'] = by_property.get((room_type_id, property_id)) or by_id.get(room_type_id, '')


def _save_arrival(record):
    defaults = dict(record)
    booking_bnovo_id = defaults.pop('booking_bnovo_id')
    Arrival.objects.update_or_create(
        booking_bnovo_id=booking_bnovo_id,
        defaults=defaults,
    )


def _window_start(settings, window_days=None):
    today = timezone.localdate()
    days = max(1, int(window_days or settings.sync_window_days or 30))
    return today - timedelta(days=days - 1)


def _window_bounds(settings, window_days=None):
    start_date = _window_start(settings, window_days=window_days)
    end_date = timezone.localdate()
    start_dt = timezone.make_aware(datetime.combine(start_date, time.min))
    end_dt = timezone.make_aware(datetime.combine(end_date, time.max))
    return start_dt, end_dt


def _overlaps_window(queryset, start_dt, end_dt):
    return queryset.filter(arrival_at__lte=end_dt).filter(
        Q(departure_at__gte=start_dt) | Q(departure_at__isnull=True)
    )


def cleanup_old_arrivals(settings, window_days=None):
    start_dt, _ = _window_bounds(settings, window_days=window_days)
    Arrival.objects.filter(departure_at__lt=start_dt).delete()
    Arrival.objects.filter(departure_at__isnull=True, arrival_at__lt=start_dt).delete()


def cleanup_missing_arrivals(settings, window_days=None):
    start_dt, end_dt = _window_bounds(settings, window_days=window_days)
    raw_booking_ids = _overlaps_window(
        Booking.objects.all(),
        start_dt,
        end_dt,
    ).values_list('bnovo_id', flat=True)
    _overlaps_window(
        Arrival.objects.all(),
        start_dt,
        end_dt,
    ).exclude(booking_bnovo_id__in=raw_booking_ids).delete()


def run_arrival_aggregation(triggered_by='manual', window_days=None):
    settings = IntegrationSettings.get_solo()
    effective_window_days = max(1, int(window_days or settings.sync_window_days or 30))
    run = SyncRun.objects.create(
        job_code='arrival_aggregation',
        status='running',
        triggered_by=triggered_by,
        started_at=timezone.now(),
        note=f'window_days={effective_window_days}',
    )
    start_dt, end_dt = _window_bounds(settings, window_days=effective_window_days)
    queryset = _overlaps_window(Booking.objects.all(), start_dt, end_dt).order_by('arrival_at', 'id')
    run.total_requests = queryset.count()
    run.save(update_fields=['total_requests'])

    detail_map = {}
    detail_queryset = _overlaps_window(
        BookingDetail.objects.all(),
        start_dt,
        end_dt,
    ).order_by('bnovo_id', '-fetched_at', '-id')
    for detail in detail_queryset.iterator(chunk_size=200):
        if detail.bnovo_id not in detail_map:
            detail_map[detail.bnovo_id] = detail

    room_type_names_by_property, room_type_names_by_id = _room_type_name_maps()

    processed = 0
    for booking in queryset.iterator(chunk_size=200):
        if _check_and_cancel(run.id):
            return SyncRun.objects.filter(pk=run.id).first()
        try:
            source = detail_map.get(booking.bnovo_id) or booking
            record = build_arrival_record(source)
            _apply_room_type_name(record, room_type_names_by_property, room_type_names_by_id)
            _save_arrival(record)
            processed += 1
            run.successful_requests += 1
            if processed % 100 == 0:
                run.save(update_fields=['successful_requests'])
        except Exception as exc:
            run.failed_requests += 1
            SyncLog.objects.create(
                run=run,
                level='error',
                endpoint='arrival_aggregation',
                message=f'{booking.bnovo_id}: {exc}',
            )
            if run.failed_requests % 20 == 0:
                run.save(update_fields=['failed_requests'])

    cleanup_missing_arrivals(settings, window_days=effective_window_days)
    cleanup_old_arrivals(settings, window_days=effective_window_days)
    settings.last_aggregation_sync_at = timezone.now()
    settings.save(update_fields=['last_aggregation_sync_at', 'updated_at'])
    final_status = 'success' if run.failed_requests == 0 else 'failed'
    run.status = final_status
    run.finished_at = timezone.now()
    run.note = f'window_days={effective_window_days}; aggregated={processed}'
    run.save(
        update_fields=[
            'status',
            'finished_at',
            'note',
            'successful_requests',
            'failed_requests',
        ]
    )
    SyncLog.objects.create(
        run=run,
        level='info',
        endpoint='arrival_aggregation',
        message=f'Aggregated {processed} arrival summaries',
    )
    return run
