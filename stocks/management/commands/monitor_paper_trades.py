"""
Management command: monitor open paper trades against today's price data.

Run daily after cron_daily_analysis (prices must already be updated):
    python manage.py monitor_paper_trades

Logic per open trade:
  - Get today's TickerPrice (high/low)
  - low  <= stop_loss   → CLOSED_LOSS  at stop_loss
  - high >= target_price → CLOSED_WIN  at target_price
  - If both on same day  → CLOSED_LOSS (conservative, stop first)
  - Portfolio cash_balance updated for each close

Crontab example (after market close, after cron_daily_analysis):
    30 17 * * 1-5 /path/to/venv/bin/python /path/to/manage.py monitor_paper_trades >> /var/log/stockanalyzer.log 2>&1
"""

import logging
from datetime import date

from django.core.management.base import BaseCommand
from django.contrib.auth.models import User

from stocks.models import PaperTrade, TickerPrice
from stocks.email_utils import send_paper_monitor_email

logger = logging.getLogger('stocks.management')


def _close_trade(trade: PaperTrade, exit_price: float, status: str) -> float:
    pnl         = round((exit_price - trade.entry_price) * trade.shares, 2)
    pnl_pct     = round((exit_price - trade.entry_price) / trade.entry_price * 100, 2)
    cash_return = round(trade.capital_used + pnl, 2)

    trade.status      = status
    trade.exit_price  = round(exit_price, 2)
    trade.exit_date   = date.today()
    trade.pnl_dollars = pnl
    trade.pnl_percent = pnl_pct
    trade.save()

    portfolio = trade.portfolio
    portfolio.cash_balance = round(portfolio.cash_balance + cash_return, 2)
    portfolio.save()
    return pnl


class Command(BaseCommand):
    help = 'Check open paper trades against today\'s price data and close wins/losses.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--no-email', action='store_true',
            help='Skip sending email summaries',
        )
        parser.add_argument(
            '--date', type=str, default=None, metavar='YYYY-MM-DD',
            help='Override check date (default: today)',
        )

    def handle(self, **options):
        no_email   = options['no_email']
        check_date = options['date']

        if check_date:
            try:
                from datetime import datetime
                check_date = datetime.strptime(check_date, '%Y-%m-%d').date()
            except ValueError:
                self.stderr.write(f'Invalid date format: {options["date"]}. Use YYYY-MM-DD.')
                return
        else:
            check_date = date.today()

        self.stdout.write(
            f'\n=== Paper Trade Monitor — {check_date} ===\n'
        )

        open_trades = list(
            PaperTrade.objects
            .filter(status='OPEN')
            .select_related('ticker', 'portfolio', 'portfolio__user')
        )

        if not open_trades:
            self.stdout.write('No open paper trades. Nothing to do.\n')
            return

        self.stdout.write(f'Open trades to check: {len(open_trades)}\n\n')

        # closed_by_user: {user_id: [result_dict, ...]}
        closed_by_user = {}
        wins = losses = skipped = 0

        for trade in open_trades:
            symbol = trade.ticker.symbol
            price_row = TickerPrice.objects.filter(
                ticker=trade.ticker,
                date=check_date,
            ).first()

            if not price_row:
                self.stdout.write(
                    f'  {symbol:<10} no price data for {check_date} — skipped\n'
                )
                skipped += 1
                continue

            stop_hit   = price_row.low  <= trade.stop_loss
            target_hit = price_row.high >= trade.target_price

            if not stop_hit and not target_hit:
                self.stdout.write(
                    f'  {symbol:<10} still open  '
                    f'H={price_row.high:.2f}  L={price_row.low:.2f}  '
                    f'stop={trade.stop_loss:.2f}  target={trade.target_price:.2f}\n'
                )
                continue

            # Stop takes priority when both levels are touched on the same day
            if stop_hit:
                status     = 'CLOSED_LOSS'
                exit_price = trade.stop_loss
                label      = 'LOSS'
            else:
                status     = 'CLOSED_WIN'
                exit_price = trade.target_price
                label      = 'WIN '

            try:
                pnl = _close_trade(trade, exit_price, status)
            except Exception as exc:
                self.stdout.write(f'  {symbol:<10} ERROR closing: {exc}\n')
                logger.exception('monitor_paper_trades: close failed trade_id=%s', trade.id)
                continue

            sign = '+' if pnl >= 0 else ''
            self.stdout.write(
                f'  {symbol:<10} [{label}]  exit=${exit_price:.2f}  '
                f'P&L {sign}${pnl:,.2f}\n'
            )

            if label == 'WIN ':
                wins += 1
            else:
                losses += 1

            user = trade.portfolio.user
            closed_by_user.setdefault(user.id, {'user': user, 'trades': []})
            closed_by_user[user.id]['trades'].append({
                'symbol':      symbol,
                'status':      status,
                'pnl':         pnl,
                'pnl_pct':     trade.pnl_percent,
                'entry_price': trade.entry_price,
                'exit_price':  exit_price,
                'shares':      trade.shares,
                'capital':     trade.capital_used,
                'cash_now':    trade.portfolio.cash_balance,
            })

        self.stdout.write(
            f'\nSummary — closed WIN:{wins}  LOSS:{losses}  skipped:{skipped}\n'
        )

        if no_email or not closed_by_user:
            if no_email:
                self.stdout.write('Email skipped (--no-email).\n')
            else:
                self.stdout.write('No trades closed — no email sent.\n')
            return

        self.stdout.write('\nSending email summaries...\n')
        sent = email_errors = 0

        for entry in closed_by_user.values():
            user   = entry['user']
            trades = entry['trades']
            if not user.email:
                continue
            try:
                send_paper_monitor_email(user, trades, check_date)
                self.stdout.write(
                    f'  ✓ {user.username} ({user.email})  '
                    f'{len(trades)} trade(s) closed\n'
                )
                sent += 1
            except Exception as mail_err:
                self.stdout.write(f'  ✗ {user.username}: {mail_err}\n')
                logger.exception('monitor_paper_trades: email failed user=%s', user.username)
                email_errors += 1

        self.stdout.write(f'\nEmails — sent:{sent}  errors:{email_errors}\n')
