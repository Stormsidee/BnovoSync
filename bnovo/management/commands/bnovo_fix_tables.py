from django.core.management.base import BaseCommand
from django.db import connection

from bnovo.models import Booking


class Command(BaseCommand):
    help = 'Create missing bnovo tables if they were dropped'

    def handle(self, *args, **options):
        existing = set(connection.introspection.table_names())
        created = []
        with connection.schema_editor() as schema_editor:
            if Booking._meta.db_table not in existing:
                schema_editor.create_model(Booking)
                created.append(Booking._meta.db_table)

        if created:
            self.stdout.write(self.style.SUCCESS(f'Created tables: {", ".join(created)}'))
        else:
            self.stdout.write(self.style.SUCCESS('No missing bnovo tables found'))
