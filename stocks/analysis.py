import yfinance as yf
import pandas as pd
import ta
from datetime import date
from typing import Optional
from django.utils import timezone
from .models import Ticker, TickerPrice, TickerAnalysis


# ─── DATA FETCHING ─────────────────────────────────────────────────────────────

def _build_df_from_db(ticker: Ticker) -> pd.DataFrame:
    qs = ticker.prices.order_by('date').values('date', 'open', 'high', 'low', 'close', 'volume')
    if not qs.exists():
        return pd.DataFrame()
    df = pd.DataFrame.from_records(qs)
    df['date'] = pd.to_datetime(df['date'])
    return df


def fetch_and_store_prices(ticker: Ticker) -> pd.DataFrame:
    db_count = ticker.prices.count()
    if db_count >= 400:
        df_new = yf.download(ticker.symbol, period='30d', progress=False, auto_adjust=True)
    else:
        df_new = yf.download(ticker.symbol, period='2y', progress=False, auto_adjust=True)

    if df_new is None or df_new.empty:
        if db_count >= 200:
            return _build_df_from_db(ticker)
        raise ValueError(f"No market data returned for {ticker.symbol}")

    df_new = df_new.reset_index()
    if isinstance(df_new.columns, pd.MultiIndex):
        df_new.columns = [c[0] for c in df_new.columns]
    df_new.columns = [str(c).lower() for c in df_new.columns]

    required = {'date', 'open', 'high', 'low', 'close', 'volume'}
    if not required.issubset(set(df_new.columns)):
        raise ValueError(f"Unexpected columns for {ticker.symbol}: {df_new.columns.tolist()}")

    existing_dates = set(
        TickerPrice.objects.filter(ticker=ticker).values_list('date', flat=True)
    )
    new_prices = []
    for _, row in df_new.iterrows():
        row_date = row['date'].date() if hasattr(row['date'], 'date') else row['date']
        if pd.isna(row['close']) or row_date in existing_dates:
            continue
        new_prices.append(TickerPrice(
            ticker=ticker,
            date=row_date,
            open=float(row['open']),
            high=float(row['high']),
            low=float(row['low']),
            close=float(row['close']),
            volume=int(row['volume']),
        ))
    if new_prices:
        TickerPrice.objects.bulk_create(new_prices, ignore_conflicts=True)

    ticker.last_price_update = timezone.now()
    ticker.save(update_fields=['last_price_update'])

    return _build_df_from_db(ticker)


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    close  = df['close'].astype(float)
    volume = df['volume'].astype(float)

    df['sma9']      = close.rolling(window=9).mean()
    df['sma20']     = close.rolling(window=20).mean()
    df['sma50']     = close.rolling(window=50).mean()
    df['sma200']    = close.rolling(window=200).mean()
    df['rsi']       = ta.momentum.RSIIndicator(close=close, window=14).rsi()
    df['avg_vol20'] = volume.rolling(window=20).mean()

    df = df.dropna(subset=['sma50', 'rsi', 'avg_vol20'])
    return df.reset_index(drop=True)


# ─── STRATEGIES ────────────────────────────────────────────────────────────────
#
# Philosophy: swing trading from support zones, NOT chasing breakouts.
# The ideal entry is when price is between SMA200 (support) and SMA50 (target),
# RSI is recovering from oversold, and the long-term trend is intact.
# We penalise extended prices and overbought RSI.

def strategy_sweet_spot(df: pd.DataFrame) -> bool:
    """
    The ideal swing buy zone: price is above SMA200 (long-term trend intact)
    but at or below SMA50 (+3% tolerance). RSI recovering but not overbought.
    This is the highest-conviction setup — buying near support with clear upside.
    """
    today = df.iloc[-1]
    if pd.isna(today.get('sma200', float('nan'))) or pd.isna(today.get('sma50', float('nan'))):
        return False

    sma200 = float(today['sma200'])
    sma50  = float(today['sma50'])
    close  = float(today['close'])
    rsi    = float(today['rsi'])

    in_zone       = sma200 < close <= sma50 * 1.03   # between SMA200 and SMA50
    rsi_ok        = 35 <= rsi <= 60                   # recovering, not overbought
    structure_ok  = sma200 < sma50                    # normal bull structure
    vol_ok        = today['volume'] >= today['avg_vol20'] * 0.8  # at least decent volume

    return bool(in_zone and rsi_ok and structure_ok and vol_ok)


def strategy_sma200_bounce(df: pd.DataFrame, window: int = 10) -> bool:
    """
    Price bounced off the SMA200 recently and is now recovering.
    Must still be close to the SMA200 — not already at SMA50.
    Best setup: price dipped below SMA200, bounced, and is now reclaiming it
    but hasn't run far yet (< 5% above SMA50).
    """
    today = df.iloc[-1]
    if pd.isna(today.get('sma200', float('nan'))):
        return False

    sma200 = float(today['sma200'])
    sma50  = float(today['sma50'])
    close  = float(today['close'])
    rsi    = float(today['rsi'])

    # Price must be above SMA200 now (the bounce)
    if close <= sma200:
        return False

    # Must have been below SMA200 recently (the dip that created the setup)
    recent = df.tail(window)
    had_dip = (recent['close'] < recent['sma200']).any()
    if not had_dip:
        return False

    # Must NOT be already extended above SMA50 — that ship has sailed
    if close > sma50 * 1.05:
        return False

    # RSI must not be overbought
    if rsi > 65:
        return False

    return True


def strategy_fresh_sma50_breakout(df: pd.DataFrame, window: int = 3) -> bool:
    """
    FRESH breakout above SMA50 within the last 3 days, on volume.
    RSI must not be overbought at today's close.
    After 3+ days the move is considered extended — no longer a fresh setup.
    """
    if len(df) < window + 1:
        return False

    today_rsi = float(df.iloc[-1]['rsi'])
    if today_rsi > 65:  # If already overbought, it's too late to enter
        return False

    recent = df.tail(window + 1).reset_index(drop=True)
    for i in range(1, len(recent)):
        prev = recent.iloc[i - 1]
        cur  = recent.iloc[i]
        if pd.isna(prev['sma50']) or pd.isna(cur['sma50']):
            continue
        if prev['close'] < prev['sma50'] and cur['close'] > cur['sma50']:
            if cur['volume'] > cur['avg_vol20']:
                return True
    return False


def strategy_rsi_rebound(df: pd.DataFrame, window: int = 14) -> bool:
    """
    RSI was deeply oversold (< 30) recently and has recovered above 40.
    Price must be above SMA200 (we don't buy recoveries in downtrends).
    """
    if len(df) < 2:
        return False
    today = df.iloc[-1]
    current_rsi = float(today['rsi'])
    if pd.isna(current_rsi):
        return False

    # Only valid above SMA200
    if not pd.isna(today.get('sma200', float('nan'))) and today['close'] < today['sma200']:
        return False

    # RSI must not be overbought now
    if current_rsi > 65:
        return False

    was_oversold = (df.tail(window)['rsi'] < 30).any()
    is_recovering = current_rsi > 40
    return bool(was_oversold and is_recovering)


def strategy_capitulation_reversal(df: pd.DataFrame, window: int = 7) -> bool:
    """
    High-volume sell-off followed by a positive close (capitulation reversal).
    Price must be above SMA200 — we don't catch falling knives in downtrends.
    """
    if len(df) < 3:
        return False
    today = df.iloc[-1]

    if not pd.isna(today.get('sma200', float('nan'))) and today['close'] < today['sma200'] * 0.97:
        return False

    recent = df.tail(window + 1).reset_index(drop=True)
    for i in range(1, len(recent) - 1):
        prev = recent.iloc[i - 1]
        day  = recent.iloc[i]
        nxt  = recent.iloc[i + 1]
        if prev['close'] == 0:
            continue
        drop = (day['close'] - prev['close']) / prev['close']
        if drop < -0.05 and day['volume'] > day['avg_vol20'] * 1.5 and nxt['close'] > day['close']:
            return True
    return False


def strategy_sma9_pullback(df: pd.DataFrame, window: int = 20) -> dict:
    """
    9 SMA Pullback swing strategy — three-condition checklist.
    """
    result = {'trend': False, 'pullback': False, 'confirmation': False, 'valid': False}

    if len(df) < max(window, 20):
        return result

    today = df.iloc[-1]
    if pd.isna(today.get('sma9')) or pd.isna(today.get('sma20')):
        return result

    # Trend: close > SMA20, SMA9 > SMA20, HH/HL structure
    trend_ma = bool(today['close'] > today['sma20'] and today['sma9'] > today['sma20'])
    recent = df.tail(window)
    highs  = recent['high'].values
    lows   = recent['low'].values
    hh = sum(1 for i in range(1, len(highs)) if highs[i] > highs[i - 1])
    hl = sum(1 for i in range(1, len(lows))  if lows[i]  > lows[i - 1])
    trend_structure = (hh / (len(highs) - 1) >= 0.5) and (hl / (len(lows) - 1) >= 0.5)
    result['trend'] = bool(trend_ma and trend_structure)

    # Pullback: price touching/near SMA9 from above
    sma9     = float(today['sma9'])
    pct_diff = (float(today['close']) - sma9) / sma9
    result['pullback'] = bool(-0.015 <= pct_diff <= 0.015 and today['close'] >= sma9 * 0.99)

    # Confirmation: bullish candle
    result['confirmation'] = bool(today['close'] > today['open'])

    result['valid'] = bool(result['trend'] and result['pullback'] and result['confirmation'])
    return result


# ─── SCORING ───────────────────────────────────────────────────────────────────

def compute_score(strategies: list, df: pd.DataFrame, sma9_data: dict) -> int:
    today = df.iloc[-1]
    close = float(today['close'])
    rsi   = float(today['rsi'])
    sma50 = float(today['sma50'])
    sma200_raw = today.get('sma200', float('nan'))
    sma200 = float(sma200_raw) if not pd.isna(sma200_raw) else None

    # Base score from strategies
    score = len(strategies) * 25

    # Volume bonus
    if today['volume'] > today['avg_vol20'] * 1.2:
        score += 5
    if len(strategies) >= 2:
        score += 10

    # SMA9 pullback bonus
    if sma9_data.get('valid'):
        score += 20
    elif sum([sma9_data.get('trend', False), sma9_data.get('pullback', False), sma9_data.get('confirmation', False)]) == 2:
        score += 10

    # ── Penalties ──────────────────────────────────────────────────────────────

    # RSI overbought penalty
    if rsi > 70:
        score -= 30
    elif rsi > 65:
        score -= 15

    # Extension above SMA50 penalty — the further above SMA50, the worse the entry
    extension = (close - sma50) / sma50
    if extension > 0.12:    # > 12% above SMA50 — far too extended
        score -= 35
    elif extension > 0.08:  # > 8% above SMA50
        score -= 20
    elif extension > 0.05:  # > 5% above SMA50
        score -= 10

    # Below SMA200 penalty — buying in a confirmed downtrend
    if sma200 and close < sma200:
        score -= 25

    return max(0, min(score, 100))


# ─── TRADING LEVELS ────────────────────────────────────────────────────────────

def compute_trading_levels(df: pd.DataFrame, strategies: list, sma9_data: dict):
    today         = df.iloc[-1]
    close         = float(today['close'])
    sma50         = float(today['sma50'])
    sma200_raw    = today.get('sma200', float('nan'))
    sma200        = float(sma200_raw) if not pd.isna(sma200_raw) else None

    if sma9_data.get('valid'):
        # Tight SMA9 pullback: enter just above today's high
        entry     = round(float(today['high']) * 1.002, 2)
        swing_low = float(df.tail(10)['low'].min())
        stop_loss = round(swing_low * 0.995, 2)
        risk      = entry - stop_loss
        tp        = round(entry + risk * 2.0, 2)
        rr        = round((tp - entry) / risk, 2) if risk > 0 else 0.0
        return entry, stop_loss, tp, rr

    if 'Sweet Spot' in strategies:
        # Buying in the SMA200–SMA50 zone: target is SMA50 breakout + buffer
        entry     = round(close * 1.005, 2)
        stop_loss = round(max(sma200 * 0.99, close * 0.93), 2) if sma200 else round(close * 0.93, 2)
        risk      = entry - stop_loss
        tp        = round(sma50 * 1.06, 2)  # target: SMA50 + 6%
        rr        = round((tp - entry) / risk, 2) if risk > 0 else 0.0
        return entry, stop_loss, tp, rr

    if 'SMA200 Bounce' in strategies:
        entry     = round(close * 1.005, 2)
        stop_loss = round(sma200 * 0.98, 2) if sma200 else round(close * 0.93, 2)
        risk      = entry - stop_loss
        tp        = round(sma50 * 1.04, 2)
        rr        = round((tp - entry) / risk, 2) if risk > 0 else 0.0
        return entry, stop_loss, tp, rr

    if len(strategies) >= 2:
        entry = round(close * 1.003, 2)
    elif 'Fresh SMA50 Breakout' in strategies:
        entry = round(close * 1.005, 2)
    elif close < sma50:
        entry = round(sma50 * 1.002, 2)
    elif sma200 and close < sma200:
        entry = round(sma200 * 1.002, 2)
    else:
        entry = round(close * 1.01, 2)

    stop_pct  = 0.05 if len(strategies) >= 2 else 0.07
    stop_loss = round(entry * (1 - stop_pct), 2)
    risk      = entry - stop_loss
    tp        = round(entry + risk * 2.5, 2)
    rr        = round((tp - entry) / risk, 2) if risk > 0 else 0.0
    return entry, stop_loss, tp, rr


# ─── EXPLANATION ───────────────────────────────────────────────────────────────

def build_explanation(strategies: list, score: int, df: pd.DataFrame, sma9_data: dict) -> str:
    today  = df.iloc[-1]
    close  = float(today['close'])
    rsi    = float(today['rsi'])
    sma50  = float(today['sma50'])
    sma200_raw = today.get('sma200', float('nan'))
    sma200 = float(sma200_raw) if not pd.isna(sma200_raw) else None
    parts  = []

    if 'Sweet Spot' in strategies:
        parts.append(
            f"Price is in the ideal swing buy zone: above SMA200 (${sma200:.2f}) "
            f"and below SMA50 (${sma50:.2f}), with RSI recovering at {rsi:.1f}. "
            "Long-term trend intact, entry near support with SMA50 as upside target."
        )
    if 'SMA200 Bounce' in strategies:
        parts.append(
            f"Price recently dipped below the 200-day SMA (${sma200:.2f}) and bounced back, "
            "signaling the long-term support is holding. Still close to SMA50 — good risk/reward."
        )
    if 'Fresh SMA50 Breakout' in strategies:
        parts.append(
            f"Fresh breakout above SMA50 (${sma50:.2f}) on above-average volume. "
            "RSI is not overbought — the move has room to continue."
        )
    if 'RSI Rebound' in strategies:
        parts.append(
            f"RSI recovered from oversold territory (now {rsi:.1f}), "
            "indicating buyers are stepping in."
        )
    if 'Capitulation + Reversal' in strategies:
        parts.append(
            "A high-volume sell-off was followed by a positive close — capitulation reversal pattern."
        )
    if sma9_data.get('valid'):
        parts.append(
            f"9 SMA Pullback setup active: price touching SMA9 (${float(today['sma9']):.2f}) "
            "in an uptrend with bullish confirmation."
        )

    if not parts:
        ext = ((close - sma50) / sma50) * 100
        sma200_str = f"${sma200:.2f}" if sma200 else 'N/A'
        if rsi > 65:
            parts.append(
                f"RSI at {rsi:.1f} — overbought territory. "
                f"Price is {ext:.1f}% above SMA50 (${sma50:.2f}). "
                "Waiting for a pullback toward the SMA200–SMA50 zone before entering."
            )
        elif ext > 5:
            parts.append(
                f"Price has already extended {ext:.1f}% above SMA50 (${sma50:.2f}). "
                f"Better entry would be on a pullback toward SMA50 or SMA200 (${sma200_str}). "
                f"RSI: {rsi:.1f}."
            )
        else:
            parts.append(
                f"No clear setup yet. Price: ${close:.2f} | SMA50: ${sma50:.2f} | "
                f"SMA200: {sma200_str} | RSI: {rsi:.1f}. "
                "Waiting for price to enter the SMA200–SMA50 support zone."
            )

    label = (
        "Strong conviction" if score >= 70
        else "Moderate conviction" if score >= 40
        else "Low conviction — monitor only"
    )
    parts.append(f"{label} (score: {score}/100).")
    return ' '.join(parts)


# ─── MAIN ENTRY POINT ──────────────────────────────────────────────────────────

def run_analysis_for_ticker(ticker: Ticker, force: bool = False) -> Optional[TickerAnalysis]:
    if not force:
        existing = TickerAnalysis.objects.filter(ticker=ticker, date=date.today()).first()
        if existing:
            return existing

    df_raw = fetch_and_store_prices(ticker)
    df     = compute_indicators(df_raw)

    if df.empty or len(df) < 50:
        raise ValueError(f"No valid indicator data for {ticker.symbol}")

    strategies = []
    if strategy_sweet_spot(df):
        strategies.append('Sweet Spot')
    if strategy_sma200_bounce(df):
        strategies.append('SMA200 Bounce')
    if strategy_fresh_sma50_breakout(df):
        strategies.append('Fresh SMA50 Breakout')
    if strategy_rsi_rebound(df):
        strategies.append('RSI Rebound')
    if strategy_capitulation_reversal(df):
        strategies.append('Capitulation + Reversal')

    sma9_data = strategy_sma9_pullback(df)

    score  = compute_score(strategies, df, sma9_data)
    signal = 'BUY' if score >= 70 else 'WATCH' if score >= 40 else 'NO_BUY'

    today_row     = df.iloc[-1]
    current_price = round(float(today_row['close']), 2)
    entry, stop_loss, take_profit, rr = compute_trading_levels(df, strategies, sma9_data)
    explanation = build_explanation(strategies, score, df, sma9_data)

    analysis, _ = TickerAnalysis.objects.update_or_create(
        ticker=ticker,
        date=date.today(),
        defaults={
            'signal':               signal,
            'confidence_score':     score,
            'current_price':        current_price,
            'entry_price':          entry,
            'stop_loss':            stop_loss,
            'take_profit':          take_profit,
            'risk_reward_ratio':    rr,
            'strategies_triggered': strategies,
            'sma9_data':            sma9_data,
            'explanation':          explanation,
        }
    )
    return analysis
