import os
from datetime import datetime, time

import django
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bnovo_sync.settings')
django.setup()

from bnovo import models
from bnovo.models import EndpointSetting
from bnovo.sync.endpoints import ENDPOINTS

app = FastAPI(title='Bnovo Sync API')


MODEL_NAME_TO_MODEL = {
    'Arrival': models.Arrival,
    'Booking': models.Booking,
    'BookingDetail': models.BookingDetail,
    'Service': models.Service,
    'RoomType': models.RoomType,
    'Room': models.Room,
    'TariffPlan': models.TariffPlan,
    'TariffPrice': models.TariffPrice,
    'Guest': models.Guest,
    'Invoice': models.Invoice,
    'Payment': models.Payment,
    'FinanceItem': models.FinanceItem,
    'LegalEntity': models.LegalEntity,
    'DashboardSnapshot': models.DashboardSnapshot,
}

ENDPOINT_CODE_TO_MODEL_NAME = {
    endpoint['code']: endpoint.get('model') for endpoint in ENDPOINTS if endpoint.get('model')
}

ALIAS_TO_MODEL_NAME = {
    'arrival': 'Arrival',
    'arrivals': 'Arrival',
    'booking': 'Booking',
    'bookings': 'Booking',
    'booking_detail': 'BookingDetail',
    'booking_details': 'BookingDetail',
    'service': 'Service',
    'services': 'Service',
    'room': 'Room',
    'rooms': 'Room',
    'room_type': 'RoomType',
    'room_types': 'RoomType',
    'tariff': 'TariffPlan',
    'tariffs': 'TariffPlan',
    'tariff_price': 'TariffPrice',
    'tariff_prices': 'TariffPrice',
    'guest': 'Guest',
    'guests': 'Guest',
    'invoice': 'Invoice',
    'invoices': 'Invoice',
    'payment': 'Payment',
    'payments': 'Payment',
    'finance_item': 'FinanceItem',
    'finance_items': 'FinanceItem',
    'legal_entity': 'LegalEntity',
    'legal_entities': 'LegalEntity',
    'dashboard': 'DashboardSnapshot',
    'adr_revpar': 'DashboardSnapshot',
}


def _enabled_codes():
    queryset = EndpointSetting.objects.all()
    if queryset.exists():
        return {item.code for item in queryset if item.enabled}
    return {endpoint['code'] for endpoint in ENDPOINTS if endpoint.get('enabled')}


def _resolve_model(entity: str):
    if entity in ('arrival', 'arrivals'):
        return models.Arrival

    enabled_codes = _enabled_codes()
    if entity in ENDPOINT_CODE_TO_MODEL_NAME:
        if entity not in enabled_codes:
            raise HTTPException(status_code=404, detail='Endpoint disabled')
        model_name = ENDPOINT_CODE_TO_MODEL_NAME.get(entity)
        return MODEL_NAME_TO_MODEL.get(model_name)

    model_name = ALIAS_TO_MODEL_NAME.get(entity)
    if not model_name:
        return None
    enabled_models = {ENDPOINT_CODE_TO_MODEL_NAME.get(code) for code in enabled_codes}
    if enabled_models and model_name not in enabled_models:
        raise HTTPException(status_code=404, detail='Endpoint disabled')
    return MODEL_NAME_TO_MODEL.get(model_name)


def _extract_token(x_api_token, authorization, token):
    if x_api_token:
        return x_api_token.strip()
    if authorization:
        value = authorization.strip()
        if value.lower().startswith('bearer '):
            return value.split(' ', 1)[1].strip()
    if token:
        return token.strip()
    return None


def require_api_token(
    x_api_token: str = Header(None, alias='X-API-Token'),
    authorization: str = Header(None),
    token: str = Query(None),
):
    token_value = _extract_token(x_api_token, authorization, token)
    if not token_value:
        raise HTTPException(status_code=401, detail='Missing API token')
    token_obj = models.ApiToken.objects.filter(token=token_value).first()
    if not token_obj or not token_obj.is_active:
        raise HTTPException(status_code=403, detail='Invalid or expired API token')
    return token_obj


def _parse_date_value(value: str):
    if not value:
        return None
    value = value.strip()
    dt = parse_datetime(value)
    if dt:
        return dt.date()
    parsed_date = parse_date(value)
    if parsed_date:
        return parsed_date
    for fmt in ('%d.%m.%Y', '%d-%m-%Y'):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _range_to_datetimes(date_from, date_to):
    dt_from = None
    dt_to = None
    if date_from:
        dt_from = timezone.make_aware(datetime.combine(date_from, time.min))
    if date_to:
        dt_to = timezone.make_aware(datetime.combine(date_to, time.max))
    return dt_from, dt_to


def serialize(obj):
    if isinstance(obj, models.Arrival):
        return {
            'id': obj.id,
            'booking_bnovo_id': obj.booking_bnovo_id,
            'booking_number': obj.booking_number,
            'room_bnovo_id': obj.room_bnovo_id,
            'room_type_bnovo_id': obj.room_type_bnovo_id,
            'status_id': obj.status_id,
            'status_name': obj.status_name,
            'source_id': obj.source_id,
            'source_name': obj.source_name,
            'customer_id': obj.customer_id,
            'customer_full_name': obj.customer_full_name,
            'customer_phone': obj.customer_phone,
            'customer_email': obj.customer_email,
            'visitor_names': obj.visitor_names,
            'visitor_details': obj.visitor_details,
            'adults': obj.adults,
            'children': obj.children,
            'nights': obj.nights,
            'arrival_at': obj.arrival_at.isoformat() if obj.arrival_at else None,
            'departure_at': obj.departure_at.isoformat() if obj.departure_at else None,
            'room_type_name': obj.room_type_name,
            'room_name': obj.room_name,
            'plan_name': obj.plan_name,
            'amount': str(obj.amount) if obj.amount is not None else None,
            'paid_amount': str(obj.paid_amount) if obj.paid_amount is not None else None,
            'balance_amount': str(obj.balance_amount) if obj.balance_amount is not None else None,
            'services': obj.services,
            'payments': obj.payments,
            'payload_excerpt': obj.payload_excerpt,
            'raw_updated_at': obj.raw_updated_at.isoformat() if obj.raw_updated_at else None,
            'raw_fetched_at': obj.raw_fetched_at.isoformat() if obj.raw_fetched_at else None,
            'aggregated_at': obj.aggregated_at.isoformat() if obj.aggregated_at else None,
        }
    return {
        'id': obj.id,
        'bnovo_id': obj.bnovo_id,
        'account_id': obj.account_id,
        'property_id': obj.property_id,
        'arrival_at': obj.arrival_at.isoformat() if hasattr(obj, 'arrival_at') and obj.arrival_at else None,
        'departure_at': obj.departure_at.isoformat() if hasattr(obj, 'departure_at') and obj.departure_at else None,
        'source_updated_at': obj.source_updated_at.isoformat() if obj.source_updated_at else None,
        'fetched_at': obj.fetched_at.isoformat() if obj.fetched_at else None,
        'payload': obj.payload,
    }


def _apply_date_filters(queryset, model, arrival_from, arrival_to, departure_from, departure_to):
    if model not in (models.Arrival, models.Booking, models.BookingDetail):
        return queryset

    arrival_date_from = _parse_date_value(arrival_from)
    arrival_date_to = _parse_date_value(arrival_to)
    arrival_dt_from, arrival_dt_to = _range_to_datetimes(arrival_date_from, arrival_date_to)
    if arrival_dt_from:
        queryset = queryset.filter(arrival_at__gte=arrival_dt_from)
    if arrival_dt_to:
        queryset = queryset.filter(arrival_at__lte=arrival_dt_to)

    departure_date_from = _parse_date_value(departure_from)
    departure_date_to = _parse_date_value(departure_to)
    departure_dt_from, departure_dt_to = _range_to_datetimes(departure_date_from, departure_date_to)
    if departure_dt_from:
        queryset = queryset.filter(departure_at__gte=departure_dt_from)
    if departure_dt_to:
        queryset = queryset.filter(departure_at__lte=departure_dt_to)

    return queryset


def serialize_arrival_summary(obj):
    return {
        'booking_id': obj.booking_bnovo_id,
        'booking_number': obj.booking_number,
        'room_id': obj.room_bnovo_id,
        'room_type_id': obj.room_type_bnovo_id,
        'room_name': obj.room_name,
        'room_type_name': obj.room_type_name,
        'plan_name': obj.plan_name,
        'status': {
            'id': obj.status_id,
            'name': obj.status_name,
        },
        'source': {
            'id': obj.source_id,
            'name': obj.source_name,
        },
        'customer': {
            'id': obj.customer_id,
            'full_name': obj.customer_full_name,
            'phone': obj.customer_phone,
            'email': obj.customer_email,
        },
        'visitors': obj.visitor_details,
        'visitor_names': obj.visitor_names,
        'stay': {
            'arrival_at': obj.arrival_at.isoformat() if obj.arrival_at else None,
            'departure_at': obj.departure_at.isoformat() if obj.departure_at else None,
            'nights': obj.nights,
            'adults': obj.adults,
            'children': obj.children,
        },
        'amounts': {
            'amount': str(obj.amount) if obj.amount is not None else None,
            'paid_amount': str(obj.paid_amount) if obj.paid_amount is not None else None,
            'balance_amount': str(obj.balance_amount) if obj.balance_amount is not None else None,
        },
        'services': obj.services,
        'payments': obj.payments,
        'updated_at': obj.aggregated_at.isoformat() if obj.aggregated_at else None,
    }


@app.get('/health')
def health():
    return {'status': 'ok'}


@app.get('/available')
def available(_token=Depends(require_api_token)):
    enabled_codes = sorted(list(_enabled_codes()))
    return {
        'enabled_endpoints': enabled_codes,
        'custom_endpoints': [
            'arrivals',
            'arrival-summary/booking-ids',
            'arrival-summary/by-booking/{booking_id}',
            'arrival-summary/by-room/{room_id}',
        ],
    }


@app.get('/arrival-summary/booking-ids')
def list_arrival_booking_ids(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    arrival_from: str = Query(None),
    arrival_to: str = Query(None),
    _token=Depends(require_api_token),
):
    queryset = models.Arrival.objects.all()
    queryset = _apply_date_filters(queryset, models.Arrival, arrival_from, arrival_to, None, None)
    total = queryset.count()
    queryset = queryset.order_by('-arrival_at', '-aggregated_at')[offset:offset + limit]
    return {
        'total': total,
        'items': [
            {
                'booking_id': obj.booking_bnovo_id,
                'booking_number': obj.booking_number,
                'room_id': obj.room_bnovo_id,
                'room_type_id': obj.room_type_bnovo_id,
                'room_type_name': obj.room_type_name,
                'arrival_at': obj.arrival_at.isoformat() if obj.arrival_at else None,
                'departure_at': obj.departure_at.isoformat() if obj.departure_at else None,
            }
            for obj in queryset
        ],
    }


@app.get('/arrival-summary/by-booking/{booking_id}')
def get_arrival_summary_by_booking(booking_id: str, _token=Depends(require_api_token)):
    obj = models.Arrival.objects.filter(booking_bnovo_id=booking_id).first()
    if not obj:
        raise HTTPException(status_code=404, detail='Arrival summary not found')
    return serialize_arrival_summary(obj)


@app.get('/arrival-summary/by-room/{room_id}')
def get_arrival_summaries_by_room(
    room_id: str,
    limit: int = Query(20, ge=1, le=100),
    _token=Depends(require_api_token),
):
    queryset = models.Arrival.objects.filter(room_bnovo_id=room_id).order_by('-arrival_at', '-aggregated_at')[:limit]
    items = [serialize_arrival_summary(obj) for obj in queryset]
    if not items:
        raise HTTPException(status_code=404, detail='Arrival summaries not found for room')
    return {
        'room_id': room_id,
        'count': len(items),
        'items': items,
    }


@app.get('/{entity}')
def list_entities(
    entity: str,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    arrival_from: str = Query(None),
    arrival_to: str = Query(None),
    departure_from: str = Query(None),
    departure_to: str = Query(None),
    _token=Depends(require_api_token),
):
    model = _resolve_model(entity)
    if not model:
        raise HTTPException(status_code=404, detail='Unknown entity')
    queryset = model.objects.all()
    queryset = _apply_date_filters(queryset, model, arrival_from, arrival_to, departure_from, departure_to)
    if model is models.Arrival:
        queryset = queryset.order_by('-arrival_at', '-aggregated_at')
    elif model in (models.Booking, models.BookingDetail):
        queryset = queryset.order_by('-arrival_at', '-fetched_at')
    else:
        queryset = queryset.order_by('-id')
    queryset = queryset[offset:offset + limit]
    return [serialize(obj) for obj in queryset]


@app.get('/{entity}/{entity_id}')
def get_entity(entity: str, entity_id: str, _token=Depends(require_api_token)):
    model = _resolve_model(entity)
    if not model:
        raise HTTPException(status_code=404, detail='Unknown entity')
    if model is models.Arrival:
        obj = model.objects.filter(booking_bnovo_id=entity_id).first()
    else:
        obj = model.objects.filter(bnovo_id=entity_id).order_by('-fetched_at').first()
    if not obj:
        raise HTTPException(status_code=404, detail='Not found')
    return serialize(obj)
