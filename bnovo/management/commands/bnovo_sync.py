from django.core.management.base import BaseCommand, CommandError

from bnovo.sync.arrivals import run_arrival_aggregation
from bnovo.sync.syncer import run_arrival_raw_sync


class Command(BaseCommand):
    help = 'Run Bnovo arrival synchronization pipeline'

    def add_arguments(self, parser):
        parser.add_argument('--raw-only', action='store_true', help='Only pull raw arrival data')
        parser.add_argument('--aggregate-only', action='store_true', help='Only aggregate raw arrival data')
        parser.add_argument('--days', type=int, help='Override rolling arrival window in days')

    def handle(self, *args, **options):
        if options['raw_only'] and options['aggregate_only']:
            raise CommandError('Use either --raw-only or --aggregate-only, not both')

        days = options.get('days')
        if options['aggregate_only']:
            run = run_arrival_aggregation(triggered_by='manual', window_days=days)
            self.stdout.write(
                self.style.SUCCESS(
                    f'Aggregation finished with status={run.status}, '
                    f'success={run.successful_requests}, failed={run.failed_requests}'
                )
            )
            return

        raw_run = run_arrival_raw_sync(triggered_by='manual', window_days=days)
        self.stdout.write(
            self.style.SUCCESS(
                f'Raw sync finished with status={raw_run.status}, '
                f'success={raw_run.successful_requests}, failed={raw_run.failed_requests}'
            )
        )

        if options['raw_only']:
            return

        aggregate_run = run_arrival_aggregation(triggered_by='manual', window_days=days)
        self.stdout.write(
            self.style.SUCCESS(
                f'Aggregation finished with status={aggregate_run.status}, '
                f'success={aggregate_run.successful_requests}, failed={aggregate_run.failed_requests}'
            )
        )
