from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from bnovo.models import ApiToken, Arrival, Booking, BookingDetail, IntegrationSettings, RoomType
from bnovo.sync.arrivals import build_arrival_record, run_arrival_aggregation
from bnovo.sync.syncer import _cleanup_arrival_window_raw_data, _save_item
from bnovo_sync.fastapi_app import (
    get_arrival_summaries_by_room,
    get_arrival_summary_by_booking,
    list_arrival_booking_ids,
)


def _sample_booking_payload():
    return {
        'booking': {
            'id': 'booking-42',
            'number': 'BN-42',
            'customer_id': 'cust-1',
            'source_id': 'src-1',
            'source_name': 'Website',
            'status_id': '3',
            'status_name': 'Confirmed',
            'arrival': '2026-04-02',
            'departure': '2026-04-05',
            'create_date': '2026-03-31T10:00:00+03:00',
            'amount': '15000.00',
            'paid_amount': '5000.00',
            'adults': '2',
            'children': '1',
            'customer': {
                'id': 'cust-1',
                'surname': 'Иванов',
                'name': 'Иван',
                'patronymic': 'Иванович',
                'phone': '+79990001122',
                'email': 'ivan@example.com',
            },
            'prices': [
                {
                    'room_id': 'room-501',
                    'room_type_id': 'room-type-9',
                    'room_type_name': 'Deluxe',
                    'room_name': '101',
                    'plan_name': 'Standard',
                }
            ],
        },
        'guests': [
            {'id': 'cust-1', 'surname': 'Иванов', 'name': 'Иван', 'patronymic': 'Иванович'},
            {'id': 'guest-2', 'surname': 'Петров', 'name': 'Петр'},
        ],
        'services': [
            {'name': 'Breakfast', 'quantity': '2', 'price': '1500.00'},
        ],
        'payments': [
            {'title': 'Card payment', 'amount': '5000.00', 'date': '2026-04-01'},
        ],
    }


class ArrivalAggregationTests(TestCase):
    def setUp(self):
        settings = IntegrationSettings.get_solo()
        settings.sync_window_days = 30
        settings.save()

    def test_build_arrival_record_extracts_main_fields(self):
        detail = BookingDetail(
            bnovo_id='booking-42',
            payload=_sample_booking_payload(),
            arrival_at=timezone.now(),
            departure_at=timezone.now() + timedelta(days=3),
        )

        record = build_arrival_record(detail)

        self.assertEqual(record['booking_bnovo_id'], 'booking-42')
        self.assertEqual(record['customer_full_name'], 'Иванов Иван Иванович')
        self.assertEqual(record['visitor_names'], ['Петров Петр'])
        self.assertEqual(record['room_bnovo_id'], 'room-501')
        self.assertEqual(record['room_type_bnovo_id'], 'room-type-9')
        self.assertEqual(record['room_name'], '101')
        self.assertEqual(record['plan_name'], 'Standard')
        self.assertEqual(record['amount'], Decimal('15000.00'))
        self.assertEqual(record['paid_amount'], Decimal('5000.00'))
        self.assertEqual(record['balance_amount'], Decimal('10000.00'))

    def test_run_arrival_aggregation_creates_summary(self):
        Booking.objects.create(
            bnovo_id='booking-42',
            payload=_sample_booking_payload()['booking'],
            arrival_at=timezone.now() - timedelta(days=1),
            departure_at=timezone.now() + timedelta(days=2),
            created_at_source=timezone.now() - timedelta(days=3),
        )
        BookingDetail.objects.create(
            bnovo_id='booking-42',
            payload=_sample_booking_payload(),
            arrival_at=timezone.now() - timedelta(days=1),
            departure_at=timezone.now() + timedelta(days=2),
            created_at_source=timezone.now() - timedelta(days=3),
        )

        run = run_arrival_aggregation(triggered_by='manual')

        self.assertEqual(run.status, 'success')
        self.assertEqual(Arrival.objects.count(), 1)
        arrival = Arrival.objects.get()
        self.assertEqual(arrival.booking_bnovo_id, 'booking-42')
        self.assertEqual(arrival.customer_full_name, 'Иванов Иван Иванович')
        self.assertEqual(arrival.visitor_names, ['Петров Петр'])
        self.assertEqual(arrival.room_bnovo_id, 'room-501')
        self.assertEqual(arrival.amount, Decimal('15000.00'))

    def test_run_arrival_aggregation_falls_back_to_booking_raw_data(self):
        RoomType.objects.create(
            bnovo_id='room-type-9',
            payload={'id': 'room-type-9', 'name': 'Townhouse'},
        )
        Booking.objects.create(
            bnovo_id='booking-42',
            payload={
                'booking_id': 'booking-42',
                'booking_number': 'BN-42',
                'status_id': '3',
                'status_name': 'Confirmed',
                'source_name': 'Website',
                'surname': 'Ivanov',
                'name': 'Ivan',
                'phone': '+79990001122',
                'email': 'ivan@example.com',
                'room_id': 'room-501',
                'dual_roomtype_id': 'room-type-9',
                'real_arrival': '2026-04-02 16:00:00',
                'real_departure': '2026-04-05 12:00:00',
                'amount': '15000.00',
                'adults': 2,
                'children': 1,
                'plan': 'Standard',
            },
            arrival_at=timezone.now() - timedelta(days=1),
            departure_at=timezone.now() + timedelta(days=2),
        )

        run = run_arrival_aggregation(triggered_by='manual')

        self.assertEqual(run.status, 'success')
        arrival = Arrival.objects.get(booking_bnovo_id='booking-42')
        self.assertEqual(arrival.status_name, 'Confirmed')
        self.assertEqual(arrival.room_bnovo_id, 'room-501')
        self.assertEqual(arrival.room_type_bnovo_id, 'room-type-9')
        self.assertEqual(arrival.room_type_name, 'Townhouse')
        self.assertEqual(arrival.customer_full_name, 'Ivanov Ivan')
        self.assertEqual(arrival.amount, Decimal('15000.00'))

    def test_run_arrival_aggregation_includes_bookings_overlapping_window(self):
        started_at = timezone.now() - timedelta(days=40)
        departed_at = timezone.now() + timedelta(days=2)
        Booking.objects.create(
            bnovo_id='long-stay-booking',
            payload={
                'booking_id': 'long-stay-booking',
                'booking_number': 'LONG-1',
                'status_name': 'In house',
                'source_name': 'Direct',
                'surname': 'Long',
                'name': 'Stay',
                'room_id': 'room-777',
                'dual_roomtype_id': 'room-type-9',
            },
            arrival_at=started_at,
            departure_at=departed_at,
        )

        run = run_arrival_aggregation(triggered_by='manual')

        self.assertEqual(run.status, 'success')
        self.assertTrue(
            Arrival.objects.filter(booking_bnovo_id='long-stay-booking').exists()
        )

    def test_save_item_allows_overlap_between_lookup_and_defaults(self):
        _save_item(
            BookingDetail,
            lookup={'bnovo_id': 'booking-99', 'property_id': 'hotel-1'},
            defaults={
                'property_id': 'hotel-1',
                'payload': {'booking': {'id': 'booking-99'}},
                'account_id': 'account-1',
                'source_updated_at': None,
                'fetched_at': timezone.now(),
            },
        )

        self.assertTrue(
            BookingDetail.objects.filter(bnovo_id='booking-99', property_id='hotel-1').exists()
        )

    def test_run_arrival_aggregation_removes_stale_arrivals(self):
        Arrival.objects.create(
            booking_bnovo_id='stale-booking',
            booking_number='OLD-1',
            status_name='Cancelled',
            arrival_at=timezone.now() - timedelta(days=1),
            departure_at=timezone.now(),
        )
        BookingDetail.objects.create(
            bnovo_id='booking-42',
            payload=_sample_booking_payload(),
            arrival_at=timezone.now() - timedelta(days=1),
            departure_at=timezone.now() + timedelta(days=2),
        )

        run = run_arrival_aggregation(triggered_by='manual')

        self.assertEqual(run.status, 'success')
        self.assertFalse(Arrival.objects.filter(booking_bnovo_id='stale-booking').exists())

    def test_cleanup_arrival_window_raw_data_removes_missing_bookings(self):
        current_dt = timezone.now()
        start_date = timezone.localdate() - timedelta(days=1)
        end_date = timezone.localdate() + timedelta(days=1)

        Booking.objects.create(bnovo_id='keep-booking', payload={'booking_id': 'keep-booking'}, arrival_at=current_dt)
        Booking.objects.create(bnovo_id='drop-booking', payload={'booking_id': 'drop-booking'}, arrival_at=current_dt)
        BookingDetail.objects.create(
            bnovo_id='keep-booking',
            payload={'booking': {'id': 'keep-booking'}},
            arrival_at=current_dt,
        )
        BookingDetail.objects.create(
            bnovo_id='drop-booking',
            payload={'booking': {'id': 'drop-booking'}},
            arrival_at=current_dt,
        )

        _cleanup_arrival_window_raw_data(['keep-booking'], start_date, end_date)

        self.assertTrue(Booking.objects.filter(bnovo_id='keep-booking').exists())
        self.assertFalse(Booking.objects.filter(bnovo_id='drop-booking').exists())
        self.assertTrue(BookingDetail.objects.filter(bnovo_id='keep-booking').exists())
        self.assertFalse(BookingDetail.objects.filter(bnovo_id='drop-booking').exists())


class ArrivalSummaryApiTests(TestCase):
    def setUp(self):
        self.token = ApiToken.objects.create(token='test-token')
        self.arrival = Arrival.objects.create(
            booking_bnovo_id='booking-42',
            booking_number='BN-42',
            room_bnovo_id='room-501',
            room_type_bnovo_id='room-type-9',
            customer_id='cust-1',
            customer_full_name='Иванов Иван Иванович',
            customer_phone='+79990001122',
            customer_email='ivan@example.com',
            visitor_names=['Петров Петр'],
            visitor_details=[{'id': 'guest-2', 'full_name': 'Петров Петр'}],
            services=[{'name': 'Breakfast'}],
            payments=[{'title': 'Card payment', 'amount': '5000.00'}],
            arrival_at=timezone.now(),
            departure_at=timezone.now() + timedelta(days=3),
            room_name='101',
            room_type_name='Deluxe',
            plan_name='Standard',
            amount=Decimal('15000.00'),
            paid_amount=Decimal('5000.00'),
            balance_amount=Decimal('10000.00'),
            status_id='3',
            status_name='Confirmed',
            source_id='src-1',
            source_name='Website',
            nights=3,
            adults=2,
            children=1,
        )

    def test_booking_ids_endpoint_returns_ids(self):
        payload = list_arrival_booking_ids(
            limit=100,
            offset=0,
            arrival_from=None,
            arrival_to=None,
            _token=self.token,
        )
        self.assertEqual(payload['total'], 1)
        self.assertEqual(payload['items'][0]['booking_id'], 'booking-42')
        self.assertEqual(payload['items'][0]['room_type_id'], 'room-type-9')
        self.assertEqual(payload['items'][0]['room_type_name'], 'Deluxe')

    def test_summary_by_booking_endpoint_returns_short_info(self):
        payload = get_arrival_summary_by_booking('booking-42', _token=self.token)
        self.assertEqual(payload['booking_id'], 'booking-42')
        self.assertEqual(payload['room_id'], 'room-501')
        self.assertEqual(payload['customer']['full_name'], 'Иванов Иван Иванович')

    def test_summary_by_room_endpoint_returns_items(self):
        payload = get_arrival_summaries_by_room('room-501', limit=20, _token=self.token)
        self.assertEqual(payload['room_id'], 'room-501')
        self.assertEqual(payload['count'], 1)
        self.assertEqual(payload['items'][0]['booking_id'], 'booking-42')
