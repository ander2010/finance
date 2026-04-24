from django.core.management.base import BaseCommand
from django.core.mail import send_mail
from django.conf import settings
from stocks.models import Ticker, Watchlist
from stocks.analysis import run_analysis_for_ticker


class Command(BaseCommand):
    help = 'Run analysis for all tracked tickers (each ticker computed once, shared by all watchers)'

    def add_arguments(self, parser):
        parser.add_argument('--symbol', type=str, help='Run only for a specific ticker symbol')
        parser.add_argument('--notify', action='store_true', help='Email users on BUY signals')

    def handle(self, *args, **options):
        tickers = Ticker.objects.all()
        if options['symbol']:
            tickers = tickers.filter(symbol=options['symbol'].upper())

        total = buy_signals = errors = 0

        for ticker in tickers:
            self.stdout.write(f'  Analyzing {ticker.symbol}...', ending=' ')
            try:
                analysis = run_analysis_for_ticker(ticker)
                if analysis:
                    badge = {'BUY': '[BUY]', 'WATCH': '[WATCH]', 'NO_BUY': '[NO BUY]'}.get(analysis.signal, '')
                    self.stdout.write(self.style.SUCCESS(
                        f'{badge} score={analysis.confidence_score} price=${analysis.current_price}'
                    ))
                    if analysis.signal == 'BUY':
                        buy_signals += 1
                        if options['notify']:
                            self._notify_watchers(ticker, analysis)
                else:
                    self.stdout.write(self.style.WARNING('Insufficient data'))
                total += 1
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'ERROR: {e}'))
                errors += 1

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'Done — Tickers analyzed: {total} | BUY signals: {buy_signals} | Errors: {errors}'
        ))

    def _notify_watchers(self, ticker, analysis):
        """Send BUY alert to every user watching this ticker."""
        watchers = Watchlist.objects.filter(ticker=ticker).select_related('user')
        for w in watchers:
            user = w.user
            if not user.email:
                continue
            subject = f'[StockAnalyzer] BUY Signal: {ticker.symbol}'
            body = (
                f'Hi {user.username},\n\n'
                f'A BUY signal was detected for {ticker.symbol}.\n\n'
                f'Signal:       {analysis.signal}\n'
                f'Confidence:   {analysis.confidence_score}%\n'
                f'Close Price:  ${analysis.current_price}\n'
                f'Entry Price:  ${analysis.entry_price}\n'
                f'Stop Loss:    ${analysis.stop_loss}\n'
                f'Take Profit:  ${analysis.take_profit}\n'
                f'Risk/Reward:  {analysis.risk_reward_ratio}x\n\n'
                f'Strategies: {", ".join(analysis.strategies_triggered)}\n\n'
                f'{analysis.explanation}\n\n'
                '-- StockAnalyzer'
            )
            try:
                send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [user.email], fail_silently=True)
            except Exception:
                pass
