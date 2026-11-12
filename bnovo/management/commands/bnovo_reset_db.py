from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection


class Command(BaseCommand):
    help = 'Drop the current database schema/file and recreate it via migrations'

    def add_arguments(self, parser):
        parser.add_argument('--force', action='store_true', help='Confirm destructive reset')

    def handle(self, *args, **options):
        if not options['force']:
            raise CommandError('Use --force to confirm full database reset')

        vendor = connection.vendor
        self.stdout.write(f'Resetting database for backend: {vendor}')

        if vendor == 'postgresql':
            with connection.cursor() as cursor:
                cursor.execute('DROP SCHEMA IF EXISTS public CASCADE')
                cursor.execute('CREATE SCHEMA public')
                cursor.execute('GRANT ALL ON SCHEMA public TO CURRENT_USER')
        elif vendor == 'sqlite':
            db_name = connection.settings_dict.get('NAME')
            if not db_name:
                raise CommandError('SQLite database file is not configured')
            db_path = Path(db_name).resolve()
            base_dir = Path(settings.BASE_DIR).resolve()
            if base_dir not in db_path.parents and db_path != base_dir / db_path.name:
                raise CommandError(f'Refusing to delete SQLite DB outside project: {db_path}')
            connection.close()
            if db_path.exists():
                db_path.unlink()
        else:
            call_command('flush', interactive=False)

        call_command('migrate', interactive=False)
        self.stdout.write(self.style.SUCCESS('Database reset complete'))
