"""
Paper-trade simulation engine.

Lifecycle:
  PENDING  → price range touches entry_price   → ACTIVE  (entry filled)
  ACTIVE   → stop hit, target hit, or 15 days  → CLOSED  (exit recorded)

All price lookups use the local DB (TickerPrice) so no yfinance calls are made
during the update loop — prices must already be fresh (run after daily_analysis).
"""

import logging
from datetime import date

from ..models import Trade

logger       = logging.getLogger('stocks.trade_tracker')
MAX_HOLD_DAYS = 15


# ─── ACTIVATION ────────────────────────────────────────────────────────────────

def activate_pending_trades():
    """Flip PENDING → ACTIVE for VALID setups where price has reached entry."""
    pending = Trade.objects.filter(
        paper_status='PENDING',
        validation_status='VALID',
    ).select_related('ticker')

    activated = 0
    for trade in pending:
        today = trade.ticker.prices.order_by('-date').first()
        if not today:
            continue
        # Limit-order fill: entry price was touched if it falls within the day's range
        if today.low <= trade.entry_price <= today.high:
            trade.paper_status = 'ACTIVE'
            trade.entry_date   = today.date
            trade.save(update_fields=['paper_status', 'entry_date'])
            activated += 1
            logger.info('%s activated at entry %.2f on %s', trade.ticker.symbol,
                        trade.entry_price, today.date)
    return activated


# ─── EXIT CHECK ────────────────────────────────────────────────────────────────

def update_active_trades():
    """Check exit conditions for every ACTIVE paper trade."""
    active = Trade.objects.filter(
        paper_status='ACTIVE',
    ).select_related('ticker')

    closed = 0
    for trade in active:
        try:
            if _check_exit(trade):
                closed += 1
        except Exception as exc:
            logger.exception('Exit check failed for %s: %s', trade.ticker.symbol, exc)
    return closed


def _check_exit(trade: Trade) -> bool:
    today = trade.ticker.prices.order_by('-date').first()
    if not today:
        return False

    entry_date = trade.entry_date or today.date
    days_held  = (today.date - entry_date).days

    exit_price = None
    outcome    = None

    # Priority: stop first (worst case), then target, then time limit
    if today.low <= trade.stop_loss:
        exit_price = trade.stop_loss
        outcome    = 'LOSS'
    elif today.high >= trade.target_price:
        exit_price = trade.target_price
        outcome    = 'WIN'
    elif days_held >= MAX_HOLD_DAYS:
        exit_price = today.close
        risk       = trade.entry_price - trade.stop_loss
        if exit_price >= trade.entry_price + risk * 0.10:
            outcome = 'WIN'
        elif exit_price <= trade.entry_price - risk * 0.10:
            outcome = 'LOSS'
        else:
            outcome = 'BREAKEVEN'

    if exit_price is None:
        return False

    _close_trade(trade, exit_price, outcome, today.date, days_held)
    return True


# ─── CLOSE ─────────────────────────────────────────────────────────────────────

def _close_trade(trade: Trade, exit_price: float, outcome: str,
                 exit_date: date, days_held: int):
    entry_date = trade.entry_date or exit_date

    pnl_dollars = round((exit_price - trade.entry_price) * trade.position_size, 2)
    pnl_percent = round((exit_price - trade.entry_price) / trade.entry_price * 100, 2)

    # Max profit / drawdown during holding period (daily closes)
    prices = list(
        trade.ticker.prices
        .filter(date__gte=entry_date, date__lte=exit_date)
        .order_by('date')
        .values_list('high', 'low', flat=False)
    )
    if prices:
        all_highs = [p[0] for p in prices]
        all_lows  = [p[1] for p in prices]
        max_profit   = round((max(all_highs) - trade.entry_price) / trade.entry_price * 100, 2)
        max_drawdown = round((min(all_lows)  - trade.entry_price) / trade.entry_price * 100, 2)
    else:
        max_profit = max_drawdown = None

    trade.paper_status = 'CLOSED'
    trade.outcome      = outcome
    trade.exit_date    = exit_date
    trade.exit_price   = round(exit_price, 2)
    trade.pnl_dollars  = pnl_dollars
    trade.pnl_percent  = pnl_percent
    trade.max_profit   = max_profit
    trade.max_drawdown = max_drawdown
    trade.days_held    = days_held
    trade.save(update_fields=[
        'paper_status', 'outcome', 'exit_date', 'exit_price',
        'pnl_dollars', 'pnl_percent', 'max_profit', 'max_drawdown', 'days_held',
    ])

    logger.info('%s closed: %s | pnl $%.2f (%.1f%%) | %d days',
                trade.ticker.symbol, outcome, pnl_dollars, pnl_percent, days_held)
