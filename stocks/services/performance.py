"""
Performance analytics engine.

All functions accept a QuerySet or list of Trade objects.
Only CLOSED trades with an outcome are included in metrics.
"""

from collections import defaultdict


# ─── CORE METRICS ──────────────────────────────────────────────────────────────

def compute_metrics(trades):
    """Return a metrics dict for the given set of trades, or None if no data."""
    closed = [t for t in trades if t.paper_status == 'CLOSED' and t.outcome]
    if not closed:
        return None

    wins      = [t for t in closed if t.outcome == 'WIN']
    losses    = [t for t in closed if t.outcome == 'LOSS']
    breakevens= [t for t in closed if t.outcome == 'BREAKEVEN']

    total     = len(closed)
    win_count = len(wins)
    win_rate  = win_count / total * 100 if total else 0.0

    total_win_pnl  = sum(t.pnl_dollars for t in wins   if t.pnl_dollars is not None)
    total_loss_pnl = abs(sum(t.pnl_dollars for t in losses if t.pnl_dollars is not None))

    avg_win  = total_win_pnl  / len(wins)   if wins   else 0.0
    avg_loss = total_loss_pnl / len(losses) if losses else 0.0

    profit_factor = (total_win_pnl / total_loss_pnl
                     if total_loss_pnl > 0 else None)

    loss_rate  = 1.0 - win_rate / 100.0
    expectancy = (win_rate / 100.0 * avg_win) - (loss_rate * avg_loss)

    avg_rr   = (sum(t.rr_ratio   for t in closed) / total) if total else 0.0
    avg_days = (sum(t.days_held  for t in closed if t.days_held is not None)
                / max(1, sum(1 for t in closed if t.days_held is not None)))

    total_pnl = sum(t.pnl_dollars for t in closed if t.pnl_dollars is not None)

    return {
        'total':           total,
        'win_count':       win_count,
        'loss_count':      len(losses),
        'breakeven_count': len(breakevens),
        'win_rate':        round(win_rate, 1),
        'avg_win':         round(avg_win, 2),
        'avg_loss':        round(avg_loss, 2),
        'profit_factor':   round(profit_factor, 2) if profit_factor is not None else None,
        'expectancy':      round(expectancy, 2),
        'avg_rr':          round(avg_rr, 2),
        'avg_days':        round(avg_days, 1),
        'total_pnl':       round(total_pnl, 2),
        'total_win_pnl':   round(total_win_pnl, 2),
        'total_loss_pnl':  round(total_loss_pnl, 2),
    }


# ─── EQUITY CURVE ──────────────────────────────────────────────────────────────

def compute_equity_curve(trades, starting_capital: float):
    """
    Simulate account balance over time from closed trades.
    Returns (curve_list, final_balance).
    curve_list: [{'date': str, 'balance': float, 'ticker': str, 'outcome': str}, ...]
    """
    closed = sorted(
        [t for t in trades
         if t.paper_status == 'CLOSED' and t.pnl_dollars is not None and t.exit_date],
        key=lambda t: t.exit_date,
    )

    balance = starting_capital
    curve   = [{'date': 'Start', 'balance': round(balance, 2), 'ticker': '', 'outcome': ''}]

    for t in closed:
        balance += t.pnl_dollars
        curve.append({
            'date':    str(t.exit_date),
            'balance': round(balance, 2),
            'ticker':  t.ticker.symbol,
            'outcome': t.outcome,
        })

    return curve, round(balance, 2)


# ─── BREAKDOWN HELPERS ─────────────────────────────────────────────────────────

def _rvol_bucket(rvol):
    if rvol is None:  return 'Unknown'
    if rvol >= 1.2:   return 'Strong (≥1.2)'
    if rvol >= 1.0:   return 'Neutral (1.0–1.2)'
    return 'Weak (<1.0)'


def _rr_bucket(rr):
    if rr is None:  return 'Unknown'
    if rr >= 2.5:   return 'Excellent (≥2.5x)'
    if rr >= 2.0:   return 'Good (2.0–2.5x)'
    if rr >= 1.7:   return 'Minimum (1.7–2.0x)'
    return 'Below min (<1.7x)'


def compute_breakdown(trades, by: str) -> dict:
    """
    Break down metrics by a categorical dimension.
    by: 'strategy_type' | 'rvol_bucket' | 'rr_bucket'
    Returns an ordered dict: {label: metrics_dict or None}
    """
    groups = defaultdict(list)
    for t in trades:
        if by == 'strategy_type':
            key = t.get_strategy_type_display()
        elif by == 'rvol_bucket':
            key = _rvol_bucket(t.relative_volume)
        elif by == 'rr_bucket':
            key = _rr_bucket(t.rr_ratio)
        else:
            key = str(getattr(t, by, 'Unknown'))
        groups[key].append(t)

    return {label: compute_metrics(group) for label, group in sorted(groups.items())}
