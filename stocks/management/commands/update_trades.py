import logging
from django.core.management.base import BaseCommand
from stocks.services.trade_tracker import activate_pending_trades, update_active_trades
from stocks.models import Trade

logger = logging.getLogger('stocks.management')


class Command(BaseCommand):
    help = 'Activate pending paper trades and check exit conditions for active ones'

    def handle(self, *args, **options):
        self.stdout.write('Updating paper trades...\n')

        activated = activate_pending_trades()
        closed    = update_active_trades()

        pending  = Trade.objects.filter(paper_status='PENDING', validation_status='VALID').count()
        active   = Trade.objects.filter(paper_status='ACTIVE').count()
        total_cl = Trade.objects.filter(paper_status='CLOSED').count()

        self.stdout.write(f'  Activated : {activated}')
        self.stdout.write(f'  Closed    : {closed}')
        self.stdout.write(f'  Status    : {pending} pending | {active} active | {total_cl} closed')

        wins  = Trade.objects.filter(paper_status='CLOSED', outcome='WIN').count()
        loss  = Trade.objects.filter(paper_status='CLOSED', outcome='LOSS').count()
        if wins + loss > 0:
            wr = wins / (wins + loss) * 100
            self.stdout.write(
                self.style.SUCCESS(f'  Win rate  : {wr:.1f}% ({wins}W / {loss}L)')
            )

        self.stdout.write(self.style.SUCCESS('\nDone.'))
