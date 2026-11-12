from django.core.management.base import BaseCommand, CommandError

from bnovo.sync.workers import (
    WORKER_AGGREGATION,
    WORKER_RAW,
    run_worker_loop,
    run_worker_once,
)


class Command(BaseCommand):
    help = 'Run one of the background Bnovo workers'

    def add_arguments(self, parser):
        parser.add_argument(
            '--worker',
            required=True,
            choices=[WORKER_RAW, WORKER_AGGREGATION],
            help='Worker code to run',
        )
        parser.add_argument('--once', action='store_true', help='Run only one iteration')
        parser.add_argument('--force', action='store_true', help='Ignore schedule interval on first run')
        parser.add_argument(
            '--poll-seconds',
            type=int,
            default=15,
            help='How often to poll when running in loop mode',
        )

    def handle(self, *args, **options):
        worker_code = options['worker']
        if options['once']:
            result = run_worker_once(worker_code, force=options['force'])
            if result is None:
                raise CommandError(f'Worker "{worker_code}" did not run (disabled, already running, or interval not reached)')
            self.stdout.write(self.style.SUCCESS(f'Worker "{worker_code}" finished with status={result.status}'))
            return

        self.stdout.write(self.style.SUCCESS(f'Starting worker loop: {worker_code}'))
        run_worker_loop(
            worker_code,
            poll_seconds=options['poll_seconds'],
            force_first_run=options['force'],
        )
