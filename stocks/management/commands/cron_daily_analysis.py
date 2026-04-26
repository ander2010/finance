"""
Management command for crontab-driven daily analysis.

Design:
- Each ticker is fetched and analyzed ONCE, shared across all users.
- Tickers are processed one at a time with a configurable sleep between
  each one so a cheap server is never overwhelmed.
- After all tickers are done, each user who has send_analysis_email=True
  gets their personalised email report.

Crontab example (runs every day at 6:30 AM server time):
    30 6 * * 1-5 /path/to/venv/bin/python /path/to/manage.py cron_daily_analysis >> /var/log/stockanalyzer.log 2>&1
"""

import time
import logging
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User

from stocks.models import Ticker, Watchlist, Trade, TradingProfile
from stocks.analysis import run_analysis_for_ticker
from stocks.services.trade_engine import generate_trade_plan
from stocks.email_utils import send_analysis_report_email

logger = logging.getLogger('stocks.management')


class Command(BaseCommand):
    help = (
        'Crontab-safe daily analysis: analyzes every watched ticker ONCE, '
        'generates trade plans for all users, and sends email reports.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--force', action='store_true',
            help='Re-analyze even if already done today',
        )
        parser.add_argument(
            '--sleep', type=float, default=2.0, metavar='SECONDS',
            help='Seconds to wait between tickers (default: 2). '
                 'Increase on very slow servers.',
        )
        parser.add_argument(
            '--no-email', action='store_true',
            help='Skip sending email reports at the end',
        )

    def handle(self, *args, **options):
        force    = options['force']
        sleep    = options['sleep']
        no_email = options['no_email']

        self.stdout.write(self.style.MIGRATE_HEADING(
            '\n=== StockAnalyzer — Cron Daily Analysis ==='
        ))
        self.stdout.write(
            f'force={force}  sleep={sleep}s  email={"off" if no_email else "on"}\n'
        )

        # ── 1. Collect unique tickers across ALL watchlists ──────────────────
        ticker_ids = list(
            Ticker.objects
            .filter(watchers__isnull=False)
            .distinct()
            .values_list('id', flat=True)
            .order_by('symbol')
        )
        total = len(ticker_ids)
        if not total:
            self.stdout.write(self.style.WARNING('No tickers in any watchlist. Exiting.'))
            return

        self.stdout.write(f'Tickers to analyze: {total}\n')

        # ── 2. Analyze one ticker at a time ──────────────────────────────────
        success = errors = skipped = 0

        for idx, ticker_id in enumerate(ticker_ids, start=1):
            try:
                ticker = Ticker.objects.get(id=ticker_id)
            except Ticker.DoesNotExist:
                errors += 1
                continue

            prefix = f'[{idx:>{len(str(total))}}/{total}]'
            self.stdout.write(f'{prefix} {ticker.symbol:<10}', ending=' ')
            self.stdout.flush()

            try:
                analysis = run_analysis_for_ticker(ticker, force=force)

                if analysis is None:
                    self.stdout.write(self.style.WARNING('insufficient data'))
                    skipped += 1
                else:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'{analysis.signal:<7} '
                            f'score={analysis.confidence_score:>3}%  '
                            f'R:R {analysis.risk_reward_ratio or 0:.1f}  '
                            f'${analysis.current_price or 0:.2f}'
                        )
                    )
                    success += 1

                    # Generate trade plan for every user watching this ticker
                    watcher_users = (
                        User.objects
                        .filter(watchlist__ticker=ticker)
                        .distinct()
                    )
                    for user in watcher_users:
                        try:
                            generate_trade_plan(user, ticker, analysis)
                        except Exception as tp_err:
                            logger.warning(
                                'Trade plan failed for user=%s ticker=%s: %s',
                                user.username, ticker.symbol, tp_err,
                            )

            except Exception as exc:
                self.stdout.write(self.style.ERROR(f'ERROR: {exc}'))
                logger.exception('cron_daily_analysis failed for %s', ticker.symbol)
                errors += 1

            if idx < total:
                time.sleep(sleep)

        # ── 3. Summary ────────────────────────────────────────────────────────
        self.stdout.write(self.style.MIGRATE_HEADING(
            f'\nAnalysis done — OK:{success}  skipped:{skipped}  errors:{errors}'
        ))

        # ── 4. Send email reports to opted-in users ───────────────────────────
        if no_email:
            self.stdout.write('Email skipped (--no-email).')
            return

        self.stdout.write('\nSending email reports...')
        users_with_email = (
            User.objects
            .filter(
                watchlist__isnull=False,
                trading_profile__send_analysis_email=True,
            )
            .exclude(email='')
            .distinct()
        )

        sent = email_errors = 0
        for user in users_with_email:
            try:
                analysis_rows = []
                for entry in (
                    Watchlist.objects
                    .filter(user=user)
                    .select_related('ticker')
                ):
                    analysis_rows.append({
                        'ticker':   entry.ticker,
                        'analysis': entry.ticker.latest_analysis(),
                    })

                # Latest trade per ticker for this user
                seen = set()
                latest_trades = []
                for trade in (
                    Trade.objects
                    .filter(user=user)
                    .select_related('ticker', 'analysis')
                    .order_by('-created_at')
                ):
                    if trade.ticker_id not in seen:
                        seen.add(trade.ticker_id)
                        latest_trades.append(trade)
                latest_trades.sort(key=lambda t: (-t.rr_ratio, -t.confidence_score))

                send_analysis_report_email(user, analysis_rows, latest_trades)
                self.stdout.write(f'  ✓ {user.username} ({user.email})')
                sent += 1

            except Exception as mail_err:
                self.stdout.write(
                    self.style.ERROR(f'  ✗ {user.username}: {mail_err}')
                )
                logger.exception('Email failed for user=%s', user.username)
                email_errors += 1

        self.stdout.write(self.style.SUCCESS(
            f'\nEmails — sent:{sent}  errors:{email_errors}'
        ))
