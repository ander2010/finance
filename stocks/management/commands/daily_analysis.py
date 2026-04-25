import logging
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from stocks.models import Ticker, Watchlist
from stocks.analysis import run_analysis_for_ticker
from stocks.services.trade_engine import generate_trade_plan

logger = logging.getLogger('stocks.management')


class Command(BaseCommand):
    help = 'Run daily analysis for all watched tickers and generate trade plans'

    def add_arguments(self, parser):
        parser.add_argument('--force', action='store_true',
                            help='Force re-analysis even if already done today')

    def handle(self, *args, **options):
        force = options['force']
        label = 'Force re-analyzing' if force else 'Analyzing'
        self.stdout.write(f'{label} all watched tickers...\n')

        # Unique tickers that appear in any watchlist
        tickers = Ticker.objects.filter(watchers__isnull=False).distinct()
        seen    = set()
        success = errors = 0

        for ticker in tickers:
            if ticker.symbol in seen:
                continue
            seen.add(ticker.symbol)
            try:
                analysis = run_analysis_for_ticker(ticker, force=force)
                if analysis:
                    self.stdout.write(
                        f'  ✓ {ticker.symbol:<8} {analysis.signal:<7} '
                        f'{analysis.confidence_score:>3}%  R:R {analysis.risk_reward_ratio or 0:.1f}'
                    )
                    success += 1
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'  ✗ {ticker.symbol}: {e}'))
                logger.exception('daily_analysis failed for %s', ticker.symbol)
                errors += 1

        # Generate trade plans for every user
        self.stdout.write('\nGenerating trade plans...')
        for user in User.objects.filter(watchlist__isnull=False).distinct():
            plans = 0
            for entry in Watchlist.objects.filter(user=user).select_related('ticker'):
                analysis = entry.ticker.latest_analysis()
                if analysis:
                    generate_trade_plan(user, entry.ticker, analysis)
                    plans += 1
            self.stdout.write(f'  {user.username}: {plans} trade plans updated')

        self.stdout.write(self.style.SUCCESS(
            f'\nDone: {success} tickers analyzed, {errors} errors.'
        ))
