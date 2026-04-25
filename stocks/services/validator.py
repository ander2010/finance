"""
Professional trade validation engine.

Each trade is assessed on 6 dimensions:
  1. Market trend (SMA alignment)
  2. Market structure (higher highs / higher lows)
  3. RSI position
  4. Volume confirmation (RVOL)
  5. Support / Resistance quality
  6. Macro context (SPY)

Strategy is reduced to exactly two types:
  - Pullback  → buying near SMA200/SMA50 support zone
  - Breakout  → buying a confirmed SMA50 breakout

Status:
  VALID     → 0 failed conditions
  WATCHLIST → 1-2 failed conditions (monitor, not yet execute)
  INVALID   → 3+ failed conditions

R:R minimums:
  Pullback  → 1.7
  Breakout  → 2.0

Volume rules:
  Breakout  → RVOL >= 1.2 required (hard fail otherwise)
  Pullback  → RVOL >= 1.2 preferred; weak volume is a soft fail
"""

import pandas as pd
import logging

logger = logging.getLogger('stocks.validator')

PIVOT_WINDOW       = 5     # bars each side for swing point detection
MIN_PIVOTS         = 3     # minimum confirmed pivots needed for structure
MIN_RR_PULLBACK    = 1.7
MIN_RR_BREAKOUT    = 2.0
MIN_RVOL_BREAKOUT  = 1.2   # hard requirement for breakout volume


# ─── PIVOT DETECTION ───────────────────────────────────────────────────────────

def _find_pivot_highs(df: pd.DataFrame, window: int = PIVOT_WINDOW) -> list:
    """Return list of (index, price) for confirmed swing highs."""
    highs  = df['high'].values
    pivots = []
    limit  = len(highs) - window
    for i in range(window, limit):
        if all(highs[i] > highs[i - j] for j in range(1, window + 1)) and \
           all(highs[i] > highs[i + j] for j in range(1, window + 1)):
            pivots.append((i, float(highs[i])))
    return pivots


def _find_pivot_lows(df: pd.DataFrame, window: int = PIVOT_WINDOW) -> list:
    """Return list of (index, price) for confirmed swing lows."""
    lows   = df['low'].values
    pivots = []
    limit  = len(lows) - window
    for i in range(window, limit):
        if all(lows[i] < lows[i - j] for j in range(1, window + 1)) and \
           all(lows[i] < lows[i + j] for j in range(1, window + 1)):
            pivots.append((i, float(lows[i])))
    return pivots


# ─── STRUCTURE ─────────────────────────────────────────────────────────────────

def is_bullish_structure(df: pd.DataFrame, window: int = PIVOT_WINDOW) -> tuple:
    """
    True when the last 2 confirmed swing highs AND last 2 swing lows are
    each higher than their predecessor (HH + HL pattern).
    Returns (bool, reason_str).
    """
    pivot_highs = _find_pivot_highs(df, window)
    pivot_lows  = _find_pivot_lows(df, window)

    if len(pivot_highs) < MIN_PIVOTS:
        return False, f'Only {len(pivot_highs)} swing high(s) found — need {MIN_PIVOTS}+'
    if len(pivot_lows) < MIN_PIVOTS:
        return False, f'Only {len(pivot_lows)} swing low(s) found — need {MIN_PIVOTS}+'

    hs = [p[1] for p in pivot_highs[-3:]]
    ls = [p[1] for p in pivot_lows[-3:]]

    hh = hs[-1] > hs[-2] and hs[-2] > hs[-3]
    hl = ls[-1] > ls[-2] and ls[-2] > ls[-3]

    if hh and hl:
        return True, 'Higher highs and higher lows confirmed'
    if hh:
        return False, 'Higher highs confirmed but lows are not rising'
    if hl:
        return False, 'Higher lows confirmed but highs are not rising'
    return False, 'No clear higher highs or higher lows — structure is weak'


# ─── SUPPORT / RESISTANCE ──────────────────────────────────────────────────────

def find_nearest_resistance(pivot_highs: list, price: float, buffer: float = 0.01):
    """Nearest confirmed swing high at least `buffer`% above `price`."""
    candidates = [p for _, p in pivot_highs if p > price * (1 + buffer)]
    return min(candidates) if candidates else None


def find_nearest_support(pivot_lows: list, price: float, buffer: float = 0.01):
    """Nearest confirmed swing low at least `buffer`% below `price`."""
    candidates = [p for _, p in pivot_lows if p < price * (1 - buffer)]
    return max(candidates) if candidates else None


# ─── STRATEGY CLASSIFIER ───────────────────────────────────────────────────────

def classify_strategy(strategies: list, df: pd.DataFrame) -> str:
    """
    Exactly 'pullback' or 'breakout' — no multi/sma9_pullback.
    Breakout: price crossed above SMA50 with volume.
    Everything else is a pullback (buying near support zone).
    """
    if 'Fresh SMA50 Breakout' in strategies:
        return 'breakout'
    return 'pullback'


# ─── SCORING (6 components + multiplicative penalties) ─────────────────────────

def compute_validated_score(df: pd.DataFrame, structure_ok: bool,
                            spy_data: dict, rr: float,
                            support: float, resistance: float,
                            rvol: float = 0.0,
                            entry: float = 0.0, target: float = 0.0,
                            strategy_type: str = 'pullback') -> tuple:
    """
    Returns (score: int, breakdown: dict).
    Base components: trend 20, structure 20, RSI 15, volume 15, S/R 20, SPY 10.
    Multiplicative penalties applied after the base score.
    Status-based clamping is applied by the caller after status is determined.
    """
    today    = df.iloc[-1]
    close    = float(today['close'])
    rsi      = float(today['rsi'])
    sma50    = float(today['sma50'])
    sma200_v = today.get('sma200', float('nan'))
    sma200   = float(sma200_v) if not pd.isna(sma200_v) else None
    sma20_v  = today.get('sma20', float('nan'))
    sma20    = float(sma20_v) if not pd.isna(sma20_v) else None
    sma9_v   = today.get('sma9', float('nan'))
    sma9     = float(sma9_v) if not pd.isna(sma9_v) else None

    # 1. Trend alignment (20 pts)
    trend = 0
    if sma200 and close  > sma200:  trend += 8
    if sma200 and sma50  > sma200:  trend += 7
    if sma20  and sma20  > sma50:   trend += 3
    if sma9   and sma20  and sma9 > sma20: trend += 2
    trend = min(trend, 20)

    # 2. Structure (20 pts)
    struct = 20 if structure_ok else 0

    # 3. RSI (15 pts) — ideal zone 45–60
    if   45 <= rsi <= 60:  rsi_pts = 15
    elif 40 <= rsi <= 65:  rsi_pts = 10
    elif 35 <= rsi <= 70:  rsi_pts = 5
    else:                  rsi_pts = 0

    # 4. Volume (15 pts) — based on RVOL
    if   rvol >= 1.2:  vol_pts = 15
    elif rvol >= 1.0:  vol_pts = 8
    else:              vol_pts = 0

    # 5. S/R quality (20 pts)
    if   rr >= 2.5:  sr = 20
    elif rr >= 2.0:  sr = 15
    elif rr >= 1.7:  sr = 10
    else:            sr = 0
    if support:     sr = min(sr + 5, 20)
    if resistance:  sr = min(sr + 5, 20)

    # 6. SPY macro context (10 pts)
    spy = 10 if spy_data.get('above_sma200') else 0

    base_score = min(trend + struct + rsi_pts + vol_pts + sr + spy, 100)

    # ── Additive penalty factor (floor 0.20 prevents total collapse) ──────────
    penalty_factor = 1.0
    issues         = []

    if not structure_ok:
        penalty_factor -= 0.40   # CRITICAL
        issues.append('No bullish structure')

    if spy_data.get('above_sma200') and not spy_data.get('above_sma50', True):
        penalty_factor -= 0.20   # MODERATE
        issues.append('SPY below SMA50')

    min_rr = MIN_RR_BREAKOUT if strategy_type == 'breakout' else MIN_RR_PULLBACK
    if rr < 1.0:
        penalty_factor -= 0.50   # SEVERE
        issues.append('R:R critically low')
    elif rr < min_rr:
        penalty_factor -= 0.30   # MODERATE-SEVERE
        issues.append(f'R:R below minimum ({min_rr})')

    if rvol < 0.6:
        penalty_factor -= 0.30   # SEVERE
        issues.append('Very weak volume')
    elif rvol < 0.8:
        penalty_factor -= 0.15   # MODERATE
        issues.append('Weak volume')

    if entry > 0 and target > 0 and (target - entry) / entry < 0.01:
        penalty_factor -= 0.40   # SEVERE
        issues.append('Target too close to entry (<1%)')

    penalty_factor = max(round(penalty_factor, 2), 0.20)
    final_score    = int(round(base_score * penalty_factor))

    breakdown = {
        'trend':          trend,
        'structure':      struct,
        'rsi':            rsi_pts,
        'volume':         vol_pts,
        'sr':             sr,
        'spy':            spy,
        'base_score':     base_score,
        'penalty_factor': penalty_factor,
        'issues':         issues,
        'final_score':    final_score,
    }
    return final_score, breakdown


# ─── MAIN VALIDATOR ────────────────────────────────────────────────────────────

def validate_trade(ticker, entry: float, stop: float, target: float,
                   strategies: list, df: pd.DataFrame, spy_data: dict) -> dict:
    """
    Full validation pipeline. Returns a dict with all validation results.
    """
    if df.empty or len(df) < 30:
        return _error_result('Insufficient price history for validation')

    reasons_pass = []
    reasons_fail = []

    current_price = float(df.iloc[-1]['close'])
    strategy_type = classify_strategy(strategies, df)

    # ── Pre-compute volume metrics (always needed for return dict) ─────────────
    today_row   = df.iloc[-1]
    current_vol = int(today_row['volume'])
    avg_vol_raw = today_row.get('avg_vol20', float('nan'))
    avg_vol_20  = float(avg_vol_raw) if not pd.isna(avg_vol_raw) else 0.0
    rvol        = round(current_vol / avg_vol_20, 2) if avg_vol_20 > 0 else 0.0
    vol_quality = 'Strong' if rvol >= 1.2 else ('Neutral' if rvol >= 1.0 else 'Weak')

    # ── 1. SPY macro filter (hard block) ──────────────────────────────────────
    spy_ok     = spy_data.get('above_sma200', True)
    spy_close  = spy_data.get('spy_close')
    spy_sma200 = spy_data.get('spy_sma200')

    if not spy_ok:
        msg = (f'Market bearish: SPY ${spy_close} is below SMA200 ${spy_sma200}'
               if spy_close else 'SPY below SMA200 — all BUY trades blocked')
        _bd = {'trend': 0, 'structure': 0, 'rsi': 0, 'volume': 0, 'sr': 0, 'spy': 0,
               'base_score': 0, 'penalty_factor': 0.0,
               'issues': ['SPY below SMA200'], 'final_score': 0}
        return {
            'is_valid': False, 'status': 'INVALID',
            'reasons_pass': [], 'reasons_fail': [msg],
            'strategy_type': strategy_type,
            'resistance_level': None, 'support_level': None,
            'spy_alignment': False,
            'adjusted_target': target, 'adjusted_stop': stop,
            'rr': 0, 'adjusted_score': 0, 'score_breakdown': _bd,
            'current_volume': current_vol, 'avg_volume_20': avg_vol_20,
            'relative_volume': rvol, 'volume_quality': vol_quality,
        }

    reasons_pass.append(f'Market aligned: SPY ${spy_close} above SMA200 ${spy_sma200}')

    # ── 2. Market structure ────────────────────────────────────────────────────
    struct_ok, struct_msg = is_bullish_structure(df)
    if struct_ok:
        reasons_pass.append(f'Bullish structure: {struct_msg}')
    else:
        reasons_fail.append(f'Weak structure: {struct_msg}')

    # ── 3. Volume check ───────────────────────────────────────────────────────
    vol_str = f'{current_vol:,} vs 20-day avg {avg_vol_20:,.0f}'
    if rvol >= MIN_RVOL_BREAKOUT:
        reasons_pass.append(f'Strong volume: RVOL {rvol:.2f} ({vol_str})')
    elif rvol >= 1.0:
        reasons_pass.append(f'Neutral volume: RVOL {rvol:.2f} ({vol_str})')
    else:
        if strategy_type == 'breakout':
            reasons_fail.append(
                f'Breakout requires strong volume — RVOL {rvol:.2f} < {MIN_RVOL_BREAKOUT} ({vol_str})'
            )
        else:
            reasons_fail.append(f'Weak volume: RVOL {rvol:.2f} ({vol_str})')

    # ── 4. Swing point detection ───────────────────────────────────────────────
    pivot_highs = _find_pivot_highs(df)
    pivot_lows  = _find_pivot_lows(df)
    resistance  = find_nearest_resistance(pivot_highs, entry)
    support     = find_nearest_support(pivot_lows, entry)

    # ── 5. Target vs resistance ────────────────────────────────────────────────
    adjusted_target = target
    if resistance:
        if target > resistance:
            adjusted_target = round(resistance * 0.993, 2)
            reasons_pass.append(
                f'Target adjusted to ${adjusted_target:.2f} (below resistance ${resistance:.2f})'
            )
        else:
            reasons_pass.append(
                f'Target ${target:.2f} is below resistance ${resistance:.2f}'
            )
    else:
        reasons_pass.append('No overhead resistance — target has clear room')

    # ── 6. Stop vs support ────────────────────────────────────────────────────
    if support:
        if stop > support:
            reasons_fail.append(
                f'Stop ${stop:.2f} is above support ${support:.2f} — not protected by a level'
            )
        elif stop < support * 0.94:
            reasons_fail.append(
                f'Stop ${stop:.2f} is far below support ${support:.2f} — risk too wide (>6%)'
            )
        else:
            reasons_pass.append(
                f'Stop ${stop:.2f} protected below support ${support:.2f}'
            )
    else:
        reasons_fail.append('No clear support level found for stop protection')

    # ── 7. R:R check (strategy-aware minimums) ────────────────────────────────
    risk   = entry - stop
    reward = adjusted_target - entry
    rr     = round(reward / risk, 2) if risk > 0 else 0.0

    min_rr = MIN_RR_BREAKOUT if strategy_type == 'breakout' else MIN_RR_PULLBACK
    if rr >= 2.5:
        reasons_pass.append(f'Excellent R:R = {rr:.1f} (min {min_rr})')
    elif rr >= min_rr:
        reasons_pass.append(f'Good R:R = {rr:.1f} (min {min_rr})')
    else:
        reasons_fail.append(
            f'R:R too low: {rr:.1f} — minimum {min_rr} required for {strategy_type}'
        )

    # ── 8. Status determination ────────────────────────────────────────────────
    n_fail = len(reasons_fail)
    if n_fail == 0:
        status, is_valid = 'VALID', True
    elif n_fail <= 2:
        status, is_valid = 'WATCHLIST', False
    else:
        status, is_valid = 'INVALID', False

    # ── 9. Score (penalties, then status-based clamping) ──────────────────────
    score, breakdown = compute_validated_score(
        df, struct_ok, spy_data, rr, support, resistance,
        rvol=rvol, entry=entry, target=adjusted_target, strategy_type=strategy_type,
    )

    if status == 'VALID':
        score = max(score, 70)
    elif status == 'WATCHLIST':
        score = max(40, min(score, 70))
    else:
        score = min(score, 39)

    breakdown['final_score'] = score

    logger.info('%s | %s | %s | R:R %.1f | RVOL %.2f | score %d | pass %d | fail %d',
                ticker.symbol, strategy_type, status, rr, rvol, score,
                len(reasons_pass), n_fail)

    return {
        'is_valid':         is_valid,
        'status':           status,
        'reasons_pass':     reasons_pass,
        'reasons_fail':     reasons_fail,
        'strategy_type':    strategy_type,
        'resistance_level': resistance,
        'support_level':    support,
        'spy_alignment':    spy_ok,
        'adjusted_target':  adjusted_target,
        'adjusted_stop':    stop,
        'rr':               rr,
        'adjusted_score':   score,
        'score_breakdown':  breakdown,
        'current_volume':   current_vol,
        'avg_volume_20':    avg_vol_20,
        'relative_volume':  rvol,
        'volume_quality':   vol_quality,
    }


def _error_result(msg: str) -> dict:
    _bd = {'trend': 0, 'structure': 0, 'rsi': 0, 'volume': 0, 'sr': 0, 'spy': 0,
           'base_score': 0, 'penalty_factor': 0.0, 'issues': [], 'final_score': 0}
    return {
        'is_valid': False, 'status': 'INVALID',
        'reasons_pass': [], 'reasons_fail': [msg],
        'strategy_type': 'pullback',
        'resistance_level': None, 'support_level': None,
        'spy_alignment': True, 'adjusted_target': 0,
        'adjusted_stop': 0, 'rr': 0, 'adjusted_score': 0, 'score_breakdown': _bd,
        'current_volume': 0, 'avg_volume_20': 0.0,
        'relative_volume': 0.0, 'volume_quality': '',
    }
