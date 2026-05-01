"""
Accumulation Zone analysis — completely separate from the main strategy pipeline.

Goal: detect stocks that are BEFORE the move, not after.
The system fires when a stock is:
  - Near SMA200 (at support) → price hasn't bounced yet
  - RSI deeply oversold (20-40) → not yet recovering
  - High volume on down days → institutions accumulating quietly
  - Tight candle ranges (optional) → consolidation / base building

This is intentionally a "pre-signal" — no confirmation needed, which means
more false positives but captures setups BEFORE they become obvious.
"""

import pandas as pd
import ta
from datetime import date

from .models import Ticker, AccumulationSignal


def analyze_accumulation_for_ticker(ticker: Ticker, force: bool = False):
    """
    Returns AccumulationSignal if the ticker is in the accumulation zone, else None.
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

    df['date'] = pd.to_datetime(df['date'])
    close  = df['close'].astype(float)
    volume = df['volume'].astype(float)

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

    dist_pct = (price - sma200) / sma200 * 100   # % from SMA200 (+above / -below)

    # ── Condition 1 (CORE): Price at SMA200 zone  ─5% to +3% ─────────────────
    # Catches both: testing from above and slight breaches of support
    at_sma200 = -5.0 <= dist_pct <= 3.0

    # ── Condition 2 (CORE): RSI oversold, not yet recovering ──────────────────
    rsi_oversold = 20.0 <= rsi <= 40.0

    # Both core conditions are required
    if not (at_sma200 and rsi_oversold):
        return None

    # ── Condition 3 (BONUS): Accumulation volume on down days (last 7 days) ───
    recent7   = df.tail(7)
    down_days = recent7[recent7['close'] < recent7['open']]
    accum_vol = (
        not down_days.empty and
        bool((down_days['volume'] > down_days['avg_vol20'] * 1.3).any())
    )

    # ── Condition 4 (BONUS): Tight candle ranges → consolidation ──────────────
    last5     = df.tail(5)
    avg_range = float(((last5['high'] - last5['low']) / last5['close']).mean())
    consolidating = avg_range < 0.025   # avg daily range < 2.5 %

    # Need at least one bonus condition
    if not (accum_vol or consolidating):
        return None

    # ── Score 0-100 ────────────────────────────────────────────────────────────
    score = 0

    # Closeness to SMA200 (tighter = more reliable support)
    abs_dist = abs(dist_pct)
    if abs_dist <= 1.0:
        score += 40
    elif abs_dist <= 2.0:
        score += 30
    elif abs_dist <= 3.0:
        score += 20
    else:
        score += 10

    # RSI depth (deeper = more room to recover)
    if rsi <= 25:
        score += 30
    elif rsi <= 30:
        score += 25
    elif rsi <= 35:
        score += 15
    else:
        score += 5

    if accum_vol:
        score += 20
    if consolidating:
        score += 10

    score = max(0, min(score, 100))

    # ── Human-readable notes ───────────────────────────────────────────────────
    arrow = '↓' if dist_pct < 0 else '↑'
    parts = [
        f'SMA200 {arrow}{abs_dist:.1f}%',
        f'RSI {rsi:.1f}',
    ]
    if accum_vol:
        parts.append('Accum vol')
    if consolidating:
        parts.append(f'Consolidating ({avg_range * 100:.1f}% range)')

    signal, _ = AccumulationSignal.objects.update_or_create(
        ticker=ticker,
        date=date.today(),
        defaults={
            'price':              round(price, 2),
            'rsi':                round(rsi, 1),
            'dist_from_sma200_pct': round(dist_pct, 2),
            'vol_ratio':          round(vol / avg_vol, 2),
            'score':              score,
            'notes':              ' · '.join(parts),
        },
    )
    return signal
