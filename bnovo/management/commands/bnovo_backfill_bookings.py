from django.core.management.base import BaseCommand

from bnovo.models import Booking
from bnovo.sync.syncer import _parse_timestamp


class Command(BaseCommand):
    help = 'Backfill Booking arrival/departure/created_at_source from payload'

    def handle(self, *args, **options):
        qs = Booking.objects.all()
        total = qs.count()
        updated = 0
        for booking in qs.iterator():
            payload = booking.payload or {}
            arrival = payload.get('arrival') or payload.get('real_arrival') or payload.get('arrival_date')
            departure = payload.get('departure') or payload.get('real_departure') or payload.get('departure_date')
            created = payload.get('create_date') or payload.get('created_at')

            arrival_dt = _parse_timestamp(arrival)
            departure_dt = _parse_timestamp(departure)
            created_dt = _parse_timestamp(created)

            dirty = False
            if arrival_dt and booking.arrival_at != arrival_dt:
                booking.arrival_at = arrival_dt
                dirty = True
            if departure_dt and booking.departure_at != departure_dt:
                booking.departure_at = departure_dt
                dirty = True
            if created_dt and booking.created_at_source != created_dt:
                booking.created_at_source = created_dt
                dirty = True
            if dirty:
                booking.save(update_fields=['arrival_at', 'departure_at', 'created_at_source'])
                updated += 1

        self.stdout.write(self.style.SUCCESS(f'Backfilled {updated} of {total} bookings'))
