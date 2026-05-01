"""
Management command for crontab-driven daily analysis.

Design:
- Each ticker is fetched and analyzed ONCE, shared across all users.
- Tickers are processed in parallel using a thread pool (default 10 workers).
  PostgreSQL handles concurrent connections safely — one connection per thread.
- After all tickers finish, each user with send_analysis_email=True receives
  their personalised email report.

Crontab example (every weekday at 5:00 PM Eastern — after market close):
    0 17 * * 1-5 /path/to/venv/bin/python /path/to/manage.py cron_daily_analysis >> /var/log/stockanalyzer.log 2>&1
"""

import threading
import logging
from datetime import date as today_date
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.db import connection

from stocks.models import Ticker, Watchlist, Trade, AccumulationSignal
from stocks.analysis import run_analysis_for_ticker
from stocks.accumulation import analyze_accumulation_for_ticker
from stocks.services.trade_engine import generate_trade_plan
from stocks.email_utils import send_analysis_report_email, send_accumulation_report_email

logger = logging.getLogger('stocks.management')

_print_lock = threading.Lock()


def _log(stdout, style_fn, msg):
    with _print_lock:
        stdout.write(style_fn(msg) if style_fn else msg)
        stdout.flush()


def _analyze_one(ticker_id, force, index, total, stdout):
    """
    Worker function — runs in a thread.
    Returns dict with result info.
    """
    prefix = f'[{index:>{len(str(total))}}/{total}]'
    result = {'ticker_id': ticker_id, 'symbol': '?', 'ok': False, 'skipped': False}

    try:
        ticker = Ticker.objects.get(id=ticker_id)
        result['symbol'] = ticker.symbol

        analysis = run_analysis_for_ticker(ticker, force=force)

        if analysis is None:
            result['skipped'] = True
            _log(stdout, None, f'{prefix} {ticker.symbol:<10} — insufficient data\n')
        else:
            result['ok'] = True
            result['signal'] = analysis.signal
            result['score']  = analysis.confidence_score
            result['rr']     = analysis.risk_reward_ratio or 0
            result['price']  = analysis.current_price or 0

            _log(stdout, None,
                 f'{prefix} {ticker.symbol:<10} {analysis.signal:<7} '
                 f'score={analysis.confidence_score:>3}%  '
                 f'R:R {analysis.risk_reward_ratio or 0:.1f}  '
                 f'${analysis.current_price or 0:.2f}\n')

            # Generate trade plan for every user watching this ticker
            for user in User.objects.filter(watchlist__ticker=ticker).distinct():
                try:
                    generate_trade_plan(user, ticker, analysis)
                except Exception as tp_err:
                    logger.warning('Trade plan failed user=%s ticker=%s: %s',
                                   user.username, ticker.symbol, tp_err)

    except Exception as exc:
        symbol = result['symbol']
        _log(stdout, None, f'{prefix} {symbol:<10} ERROR: {exc}\n')
        logger.exception('cron_daily_analysis failed for ticker_id=%s', ticker_id)
        result['error'] = str(exc)
    finally:
        # Each thread owns its DB connection — close it when done so the pool
        # doesn't leak connections after the thread exits.
        connection.close()

    return result


class Command(BaseCommand):
    help = (
        'Crontab-safe daily analysis: analyzes every watched ticker in parallel, '
        'generates trade plans for all users, and sends email reports.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--force', action='store_true',
            help='Re-analyze even if already done today',
        )
        parser.add_argument(
            '--workers', type=int, default=10, metavar='N',
            help='Number of parallel threads (default: 10)',
        )
        parser.add_argument(
            '--no-email', action='store_true',
            help='Skip sending email reports at the end',
        )

    def handle(self, **options):
        force    = options['force']
        workers  = options['workers']
        no_email = options['no_email']

        self.stdout.write(
            f'\n=== StockAnalyzer — Cron Daily Analysis ===\n'
            f'force={force}  workers={workers}  email={"off" if no_email else "on"}\n\n'
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
            self.stdout.write('No tickers in any watchlist. Exiting.\n')
            return

        self.stdout.write(f'Tickers to analyze: {total}  (using {workers} threads)\n\n')

        # ── 2. Analyze in parallel ────────────────────────────────────────────
        success = errors = skipped = 0
        futures_map = {}

        with ThreadPoolExecutor(max_workers=workers) as pool:
            for idx, ticker_id in enumerate(ticker_ids, start=1):
                future = pool.submit(
                    _analyze_one,
                    ticker_id, force, idx, total, self.stdout,
                )
                futures_map[future] = ticker_id

            for future in as_completed(futures_map):
                res = future.result()
                if res.get('ok'):
                    success += 1
                elif res.get('skipped'):
                    skipped += 1
                else:
                    errors += 1

        # ── 3. Summary ────────────────────────────────────────────────────────
        self.stdout.write(
            f'\nAnalysis done — OK:{success}  skipped:{skipped}  errors:{errors}\n'
        )

        # ── 4. Accumulation zone scan (DB-only, fast) ─────────────────────────
        self.stdout.write('\n--- Accumulation Zone Scan ---\n')
        acc_found = acc_errors = 0

        for ticker_id in ticker_ids:
            try:
                ticker = Ticker.objects.get(id=ticker_id)
                signal = analyze_accumulation_for_ticker(ticker, force=force)
                if signal:
                    self.stdout.write(
                        f'  ACCUM {ticker.symbol:<10} score={signal.score}  '
                        f'RSI={signal.rsi:.1f}  SMA200 {signal.dist_from_sma200_pct:+.1f}%  '
                        f'{signal.notes}\n'
                    )
                    acc_found += 1
            except Exception as exc:
                self.stdout.write(f'  accum error {ticker_id}: {exc}\n')
                logger.warning('accumulation scan failed ticker_id=%s: %s', ticker_id, exc)
                acc_errors += 1

        self.stdout.write(f'Accumulation — signals:{acc_found}  errors:{acc_errors}\n')

        # ── 5. Send combined email (analysis + accumulation) per user ─────────
        if no_email:
            self.stdout.write('Email skipped (--no-email).\n')
            return

        self.stdout.write('\nSending email reports...\n')
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
                # Analysis rows
                analysis_rows = [
                    {'ticker': e.ticker, 'analysis': e.ticker.latest_analysis()}
                    for e in Watchlist.objects.filter(user=user).select_related('ticker')
                ]

                # Latest trade per ticker
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

                # Accumulation rows for this user's watchlist
                acc_rows = []
                for entry in Watchlist.objects.filter(user=user).select_related('ticker'):
                    sig = AccumulationSignal.objects.filter(
                        ticker=entry.ticker,
                        date=today_date.today(),
                    ).first()
                    if sig:
                        acc_rows.append({
                            'symbol':    entry.ticker.symbol,
                            'list_name': entry.list_name,
                            'price':     sig.price,
                            'rsi':       sig.rsi,
                            'dist_pct':  sig.dist_from_sma200_pct,
                            'vol_ratio': sig.vol_ratio,
                            'score':     sig.score,
                            'notes':     sig.notes,
                        })
                acc_rows.sort(key=lambda r: -r['score'])

                send_analysis_report_email(
                    user, analysis_rows, latest_trades,
                    accumulation_rows=acc_rows or None,
                )
                self.stdout.write(
                    f'  ✓ {user.username} ({user.email})'
                    f'{f"  [{len(acc_rows)} accum]" if acc_rows else ""}\n'
                )
                sent += 1

            except Exception as mail_err:
                self.stdout.write(f'  ✗ {user.username}: {mail_err}\n')
                logger.exception('Email failed for user=%s', user.username)
                email_errors += 1

        self.stdout.write(f'\nEmails — sent:{sent}  errors:{email_errors}\n')
