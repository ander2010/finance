"""
Paper trading views — completely separate from the existing trade/analysis pipeline.
"""

import math
from datetime import date, timedelta

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST
from django.http import JsonResponse
from django.db.models import Sum, Prefetch

from .models import (
    Ticker, Watchlist, TickerAnalysis, AccumulationSignal,
    PaperPortfolio, PaperDeposit, PaperTrade, TickerPrice,
)


# ─── HELPERS ───────────────────────────────────────────────────────────────────

def _get_or_none(user):
    try:
        return user.paper_portfolio
    except PaperPortfolio.DoesNotExist:
        return None


def _calc_shares(capital: float, entry: float, stop: float, risk_pct: float = 1.0) -> int:
    """Position sizing: risk X% of capital per trade."""
    risk_dollars     = capital * risk_pct / 100
    risk_per_share   = entry - stop
    if risk_per_share <= 0:
        return 0
    return max(1, math.floor(risk_dollars / risk_per_share))


def _close_trade(trade: PaperTrade, exit_price: float, status: str):
    """Shared logic for closing a paper trade and returning funds."""
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


# ─── MAIN PAGE ─────────────────────────────────────────────────────────────────

@login_required
def paper_portfolio(request):
    portfolio = _get_or_none(request.user)

    if not portfolio:
        return render(request, 'stocks/paper_portfolio.html', {'setup_needed': True})

    # Prefetch recent prices for open trades — avoids N+1 (1 query vs N)
    recent_date = date.today() - timedelta(days=7)
    open_trades = (
        PaperTrade.objects
        .filter(portfolio=portfolio, status='OPEN')
        .select_related('ticker')
        .prefetch_related(
            Prefetch(
                'ticker__prices',
                queryset=TickerPrice.objects.filter(date__gte=recent_date).order_by('-date'),
                to_attr='_recent_prices',
            )
        )
    )
    closed_trades = (
        PaperTrade.objects
        .filter(portfolio=portfolio, status__in=['CLOSED_WIN', 'CLOSED_LOSS', 'CLOSED_MANUAL'])
        .select_related('ticker')
        .order_by('-exit_date')[:30]
    )

    # Stats — DB aggregates, no Python loops
    closed_all   = PaperTrade.objects.filter(portfolio=portfolio).exclude(status='OPEN')
    wins         = closed_all.filter(status='CLOSED_WIN').count()
    losses       = closed_all.filter(status='CLOSED_LOSS').count()
    total_closed = wins + losses
    win_rate     = round(wins / total_closed * 100) if total_closed else 0
    total_pnl    = closed_all.aggregate(s=Sum('pnl_dollars'))['s'] or 0.0

    # Today's signals available for execution (only tickers in user's watchlist)
    today           = date.today()
    watchlist_ids   = set(Watchlist.objects.filter(user=request.user).values_list('ticker_id', flat=True))
    open_ticker_ids = set(open_trades.values_list('ticker_id', flat=True))

    buy_signals = list(
        TickerAnalysis.objects
        .filter(ticker_id__in=watchlist_ids, date=today, signal='BUY')
        .exclude(ticker_id__in=open_ticker_ids)
        .select_related('ticker')
        .order_by('-confidence_score')
    )
    ready_signals = list(
        AccumulationSignal.objects
        .filter(ticker_id__in=watchlist_ids, date=today, signal_type='READY_TO_BUY')
        .exclude(ticker_id__in=open_ticker_ids)
        .select_related('ticker')
        .order_by('-score')
    )

    # Enrich open trades — prices come from prefetch cache, zero DB hits
    enriched_open = []
    for t in open_trades:
        recent = t.ticker._recent_prices
        cur_price = recent[0].close if recent else t.entry_price
        enriched_open.append({
            'trade':         t,
            'current_price': cur_price,
            'unrealized':    round((cur_price - t.entry_price) * t.shares, 2),
            'unreal_pct':    round((cur_price - t.entry_price) / t.entry_price * 100, 2),
        })

    # Pre-compute portfolio value stats to avoid model properties hitting DB in template
    open_value      = sum(row['current_price'] * row['trade'].shares for row in enriched_open)
    total_deposited = portfolio.total_deposited
    total_value     = portfolio.cash_balance + open_value
    portfolio_pnl   = round(total_value - total_deposited, 2)

    return render(request, 'stocks/paper_portfolio.html', {
        'portfolio':       portfolio,
        'open_trades':     enriched_open,
        'closed_trades':   closed_trades,
        'buy_signals':     buy_signals,
        'ready_signals':   ready_signals,
        'wins':            wins,
        'losses':          losses,
        'win_rate':        win_rate,
        'total_pnl':       total_pnl,
        'today':           today,
        # Pre-computed portfolio stats (bypasses N+1 model properties)
        'total_deposited': total_deposited,
        'open_value':      open_value,
        'total_value':     total_value,
        'portfolio_pnl':   portfolio_pnl,
    })


# ─── SETUP & FUNDS ─────────────────────────────────────────────────────────────

@login_required
@require_POST
def paper_setup(request):
    """Create the portfolio with an initial deposit."""
    if _get_or_none(request.user):
        messages.warning(request, 'Ya tienes un portfolio activo.')
        return redirect('paper_portfolio')

    try:
        amount = float(request.POST.get('amount', 0))
    except ValueError:
        amount = 0

    if amount <= 0:
        messages.error(request, 'Ingresa un monto válido.')
        return redirect('paper_portfolio')

    portfolio = PaperPortfolio.objects.create(user=request.user, cash_balance=amount)
    PaperDeposit.objects.create(portfolio=portfolio, amount=amount, note='Capital inicial')
    messages.success(request, f'Portfolio creado con ${amount:,.2f} virtuales.')
    return redirect('paper_portfolio')


@login_required
@require_POST
def paper_add_funds(request):
    """Add more virtual funds to existing portfolio."""
    portfolio = get_object_or_404(PaperPortfolio, user=request.user)
    try:
        amount = float(request.POST.get('amount', 0))
    except ValueError:
        amount = 0

    if amount <= 0:
        messages.error(request, 'Ingresa un monto válido.')
        return redirect('paper_portfolio')

    portfolio.cash_balance = round(portfolio.cash_balance + amount, 2)
    portfolio.save()
    PaperDeposit.objects.create(
        portfolio=portfolio, amount=amount,
        note=request.POST.get('note', 'Depósito adicional'),
    )
    messages.success(request, f'${amount:,.2f} añadidos. Balance: ${portfolio.cash_balance:,.2f}')
    return redirect('paper_portfolio')


# ─── EXECUTE TRADE ─────────────────────────────────────────────────────────────

@login_required
@require_POST
def paper_execute(request):
    """User clicks Execute on a signal — opens a paper trade."""
    portfolio = get_object_or_404(PaperPortfolio, user=request.user)

    symbol = request.POST.get('symbol', '').upper()
    source = request.POST.get('source', 'BUY_SIGNAL')

    try:
        entry  = float(request.POST.get('entry_price', 0))
        stop   = float(request.POST.get('stop_loss',   0))
        target = float(request.POST.get('target_price', 0))
    except ValueError:
        messages.error(request, 'Precios inválidos.')
        return redirect('paper_portfolio')

    if not symbol or entry <= 0 or stop <= 0 or target <= 0:
        messages.error(request, 'Completa todos los campos.')
        return redirect('paper_portfolio')

    if stop >= entry:
        messages.error(request, 'Stop loss debe ser menor que el precio de entrada.')
        return redirect('paper_portfolio')

    ticker = get_object_or_404(Ticker, symbol=symbol)

    shares       = _calc_shares(portfolio.cash_balance, entry, stop, risk_pct=1.0)
    capital_used = round(entry * shares, 2)

    if capital_used > portfolio.cash_balance:
        messages.error(request, f'Cash insuficiente. Necesitas ${capital_used:,.2f}, tienes ${portfolio.cash_balance:,.2f}')
        return redirect('paper_portfolio')

    if shares <= 0:
        messages.error(request, 'No hay suficiente capital para cubrir el riesgo mínimo.')
        return redirect('paper_portfolio')

    PaperTrade.objects.create(
        portfolio=portfolio,
        ticker=ticker,
        source=source,
        entry_price=entry,
        shares=shares,
        stop_loss=stop,
        target_price=target,
        capital_used=capital_used,
    )
    portfolio.cash_balance = round(portfolio.cash_balance - capital_used, 2)
    portfolio.save()

    messages.success(
        request,
        f'Trade abierto: {shares} acciones de {symbol} @ ${entry:.2f} '
        f'(capital ${capital_used:,.2f})'
    )
    return redirect('paper_portfolio')


# ─── CLOSE TRADE ───────────────────────────────────────────────────────────────

@login_required
@require_POST
def paper_close(request, trade_id):
    """User manually closes an open paper trade."""
    trade = get_object_or_404(PaperTrade, id=trade_id, portfolio__user=request.user, status='OPEN')

    try:
        exit_price = float(request.POST.get('exit_price', 0))
    except ValueError:
        exit_price = 0

    if exit_price <= 0:
        latest = trade.ticker.latest_price()
        exit_price = latest.close if latest else trade.entry_price

    pnl = _close_trade(trade, exit_price, 'CLOSED_MANUAL')
    sign = '+' if pnl >= 0 else ''
    messages.success(
        request,
        f'{trade.ticker.symbol} cerrado @ ${exit_price:.2f}  P&L: {sign}${pnl:,.2f}'
    )
    return redirect('paper_portfolio')


# ─── AJAX: current price for a ticker ──────────────────────────────────────────

@login_required
def paper_ticker_price(request, symbol):
    ticker = get_object_or_404(Ticker, symbol=symbol.upper())
    latest = ticker.latest_price()
    return JsonResponse({'price': latest.close if latest else None})
