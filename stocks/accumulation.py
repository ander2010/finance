"""
Accumulation Zone analysis — completely separate from the main strategy pipeline.

Two-phase signal:

  ACCUM        → stock is near SMA200 with oversold RSI. Waiting for confirmation.
                 Do NOT enter yet. "Se ve barato" no es razón para entrar.

  READY_TO_BUY → setup confirmed. All four conditions met:
                   1. RSI > 40  (recovering from oversold)
                   2. Price broke above resistance
                   3. Volume spike (> 1.5x avg)
                   4. Price above SMA9 AND SMA20
                 Entry = resistance + 0.5%  (breakout confirmed)
                 Stop  = below SMA20 (−1%)
                 Target = next resistance or +8% from entry
"""

import pandas as pd
import ta
from datetime import date
from typing import Optional

from .models import Ticker, AccumulationSignal


# ─── HELPERS ───────────────────────────────────────────────────────────────────

def _detect_resistance(df: pd.DataFrame, window: int = 30) -> Optional[float]:
    """
    Find the nearest resistance level above current price.
    A resistance is a local high where the candle's high > both neighbors.
    """
    recent = df.tail(window).reset_index(drop=True)
    price  = float(df.iloc[-1]['close'])

    candidates = []
    for i in range(1, len(recent) - 1):
        h      = float(recent.iloc[i]['high'])
        prev_h = float(recent.iloc[i - 1]['high'])
        next_h = float(recent.iloc[i + 1]['high'])
        if h > prev_h and h > next_h and h > price:
            candidates.append(h)

    above = [r for r in candidates if r > price]
    if above:
        return round(min(above), 2)

    # Fallback: use the window's high if it's above price
    window_high = float(recent['high'].max())
    return round(window_high, 2) if window_high > price else None


def _detect_next_resistance(df: pd.DataFrame, entry: float, window: int = 30) -> Optional[float]:
    """Find the next resistance level above the entry price (for target)."""
    recent = df.tail(window).reset_index(drop=True)

    candidates = []
    for i in range(1, len(recent) - 1):
        h      = float(recent.iloc[i]['high'])
        prev_h = float(recent.iloc[i - 1]['high'])
        next_h = float(recent.iloc[i + 1]['high'])
        if h > prev_h and h > next_h and h > entry * 1.01:
            candidates.append(h)

    above = [r for r in candidates if r > entry * 1.01]
    return round(min(above), 2) if above else None


def _is_ready_to_buy(today_row, resistance: Optional[float]) -> bool:
    """All four conditions must be true."""
    rsi   = float(today_row['rsi'])
    price = float(today_row['close'])
    vol   = float(today_row['volume'])
    avgv  = float(today_row['avg_vol20'])

    sma9_raw  = today_row.get('sma9',  float('nan'))
    sma20_raw = today_row.get('sma20', float('nan'))

    rsi_ok    = rsi > 40
    vol_spike = vol > avgv * 1.5
    breakout  = resistance is not None and price > resistance

    sma9_val  = float(sma9_raw)  if not pd.isna(sma9_raw)  else None
    sma20_val = float(sma20_raw) if not pd.isna(sma20_raw) else None
    above_sma = (
        sma9_val  is not None and price > sma9_val and
        sma20_val is not None and price > sma20_val
    )

    return rsi_ok and breakout and vol_spike and above_sma


# ─── MAIN ──────────────────────────────────────────────────────────────────────

def analyze_accumulation_for_ticker(ticker: Ticker, force: bool = False):
    """
    Returns AccumulationSignal or None.
    Reads only from the local DB — no yfinance call needed.
    """
    if not force:
        existing = AccumulationSignal.objects.filter(
            ticker=ticker, date=date.today()
        ).first()
        if existing:
            return existing

    # ── Build dataframe from stored prices ────────────────────────────────────
    qs = ticker.prices.order_by('date').values(
        'date', 'open', 'high', 'low', 'close', 'volume'
    )
    if not qs.exists():
        return None

    df = pd.DataFrame.from_records(qs)
    if len(df) < 50:
        return None

    df['date']   = pd.to_datetime(df['date'])
    close        = df['close'].astype(float)
    volume       = df['volume'].astype(float)

    df['sma9']      = close.rolling(9).mean()
    df['sma20']     = close.rolling(20).mean()
    df['sma200']    = close.rolling(200).mean()
    df['rsi']       = ta.momentum.RSIIndicator(close=close, window=14).rsi()
    df['avg_vol20'] = volume.rolling(20).mean()

    df = df.dropna(subset=['sma200', 'rsi', 'avg_vol20']).reset_index(drop=True)
    if df.empty:
        return None

    today   = df.iloc[-1]
    price   = float(today['close'])
    sma200  = float(today['sma200'])
    rsi     = float(today['rsi'])
    vol     = float(today['volume'])
    avg_vol = float(today['avg_vol20'])

    dist_pct    = (price - sma200) / sma200 * 100
    resistance  = _detect_resistance(df)

    # ── Phase detection ───────────────────────────────────────────────────────
    # READY_TO_BUY check first (more specific)
    if _is_ready_to_buy(today, resistance):
        signal_type = 'READY_TO_BUY'

        sma20_val = float(today['sma20']) if not pd.isna(today.get('sma20', float('nan'))) else price * 0.97
        entry     = round(resistance * 1.005, 2) if resistance else round(price * 1.005, 2)
        stop      = round(sma20_val * 0.99, 2)
        risk      = entry - stop

        # Target: next resistance above entry, else entry + 8%
        next_res = _detect_next_resistance(df, entry)
        target   = next_res if next_res else round(entry * 1.08, 2)

        score = 100  # confirmed setup

        sma9_val  = float(today['sma9'])  if not pd.isna(today.get('sma9',  float('nan'))) else None
        rr        = round((target - entry) / risk, 1) if risk > 0 else 0
        parts     = [
            f'RSI {rsi:.1f}',
            f'Breakout >{resistance:.2f}' if resistance else '',
            f'Vol {vol/avg_vol:.1f}x',
            f'SMA9 {sma9_val:.2f}' if sma9_val else '',
            f'R:R {rr}',
        ]
        notes = ' · '.join(p for p in parts if p)

    else:
        # ── ACCUM phase: must meet core conditions ────────────────────────────
        at_sma200    = -5.0 <= dist_pct <= 3.0
        rsi_oversold = 20.0 <= rsi <= 40.0

        if not (at_sma200 and rsi_oversold):
            return None

        # At least one bonus condition
        recent7   = df.tail(7)
        down_days = recent7[recent7['close'] < recent7['open']]
        accum_vol = (
            not down_days.empty and
            bool((down_days['volume'] > down_days['avg_vol20'] * 1.3).any())
        )
        last5     = df.tail(5)
        avg_range = float(((last5['high'] - last5['low']) / last5['close']).mean())
        consolidating = avg_range < 0.025

        if not (accum_vol or consolidating):
            return None

        signal_type  = 'ACCUM'
        entry = stop = target = None

        # Score
        abs_dist = abs(dist_pct)
        score  = 40 if abs_dist <= 1 else 30 if abs_dist <= 2 else 20 if abs_dist <= 3 else 10
        score += 30 if rsi <= 25 else 25 if rsi <= 30 else 15 if rsi <= 35 else 5
        if accum_vol:    score += 20
        if consolidating: score += 10
        score = max(0, min(score, 100))

        arrow = '↓' if dist_pct < 0 else '↑'
        parts = [f'SMA200 {arrow}{abs_dist:.1f}%', f'RSI {rsi:.1f}']
        if accum_vol:    parts.append('Accum vol')
        if consolidating: parts.append(f'Consolidating ({avg_range*100:.1f}%)')
        if resistance:   parts.append(f'Resist ${resistance:.2f}')
        notes = ' · '.join(parts)

    signal, _ = AccumulationSignal.objects.update_or_create(
        ticker=ticker,
        date=date.today(),
        defaults={
            'signal_type':          signal_type,
            'price':                round(price, 2),
            'rsi':                  round(rsi, 1),
            'dist_from_sma200_pct': round(dist_pct, 2),
            'vol_ratio':            round(vol / avg_vol, 2),
            'resistance_level':     resistance,
            'entry_price':          entry,
            'stop_loss':            stop,
            'target_price':         target,
            'score':                score,
            'notes':                notes,
        },
    )
    return signal
