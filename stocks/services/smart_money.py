import yfinance as yf


def _fmt_shares(val):
    if val is None:
        return '—'
    try:
        n = int(float(val))
        if n >= 1_000_000:
            return f'{n/1_000_000:.2f}M'
        if n >= 1_000:
            return f'{n/1_000:.1f}K'
        return f'{n:,}'
    except (TypeError, ValueError):
        return str(val)


def _fmt_value(val):
    if val is None:
        return '—'
    try:
        v = float(val)
        if v >= 1_000_000_000:
            return f'${v/1_000_000_000:.2f}B'
        if v >= 1_000_000:
            return f'${v/1_000_000:.2f}M'
        if v >= 1_000:
            return f'${v/1_000:.1f}K'
        return f'${v:,.0f}'
    except (TypeError, ValueError):
        return str(val)


def _fmt_pct(val):
    """val is already a decimal fraction (e.g. 0.0972 = 9.72%)."""
    if val is None:
        return '—'
    try:
        return f'{float(val) * 100:.2f}%'
    except (TypeError, ValueError):
        return str(val)


def get_smart_money(symbol: str) -> dict:
    yf_ticker = yf.Ticker(symbol)

    # ── Institutional holders ───────────────────────────────────────────────────
    institutions = []
    try:
        df = yf_ticker.institutional_holders
        if df is not None and not df.empty:
            df.columns = [str(c).strip() for c in df.columns]
            for _, row in df.head(15).iterrows():
                holder     = str(row.get('Holder') or '—')
                shares     = row.get('Shares')
                pct_held   = row.get('pctHeld') or row.get('% Out') or row.get('% of Shares Outstanding')
                value      = row.get('Value')
                pct_change = row.get('pctChange') or row.get('% Change')
                date_rep   = row.get('Date Reported')

                # Direction from pctChange (decimal fraction)
                try:
                    chg = float(pct_change) if pct_change is not None else None
                except (TypeError, ValueError):
                    chg = None

                if chg is None:
                    direction = 'neutral'
                    chg_label = '—'
                elif chg > 0.001:
                    direction = 'buy'
                    chg_label = f'+{chg*100:.2f}%'
                elif chg < -0.001:
                    direction = 'sell'
                    chg_label = f'{chg*100:.2f}%'
                else:
                    direction = 'neutral'
                    chg_label = '~0%'

                date_str = str(date_rep)[:10] if date_rep is not None else '—'

                institutions.append({
                    'holder':    holder,
                    'shares':    _fmt_shares(shares),
                    'pct_held':  _fmt_pct(pct_held),
                    'value':     _fmt_value(value),
                    'change':    chg_label,
                    'direction': direction,
                    'date':      date_str,
                })
    except Exception:
        pass

    # ── Insider transactions ────────────────────────────────────────────────────
    insiders = []
    try:
        df = yf_ticker.insider_transactions
        if df is not None and not df.empty:
            df.columns = [str(c).strip() for c in df.columns]
            for _, row in df.head(20).iterrows():
                insider  = str(row.get('Insider') or '—')
                position = str(row.get('Position') or row.get('Relation') or '—')
                txn_code = str(row.get('Transaction') or '').strip().upper()
                text     = str(row.get('Text') or '').strip()
                shares   = row.get('Shares')
                value    = row.get('Value')
                txn_date = row.get('Start Date') or row.get('Date')

                date_str = str(txn_date)[:10] if txn_date is not None else '—'

                # Transaction codes: D = Disposition (sell), A = Acquisition (buy)
                # Also handle text-based fallback
                txt_lower = text.lower()
                if txn_code == 'A' or any(k in txt_lower for k in ('purchase', 'acquisition', 'bought')):
                    direction = 'buy'
                    direction_label = 'COMPRA'
                elif txn_code == 'D' or any(k in txt_lower for k in ('sale', 'sell', 'sold', 'disposition')):
                    direction = 'sell'
                    direction_label = 'VENTA'
                else:
                    direction = 'other'
                    direction_label = txn_code or 'OTRO'

                insiders.append({
                    'insider':          insider,
                    'position':         position,
                    'date':             date_str,
                    'direction':        direction,
                    'direction_label':  direction_label,
                    'shares':           _fmt_shares(shares),
                    'value':            _fmt_value(value),
                    'text':             text[:80] if text else '',
                })
    except Exception:
        pass

    return {
        'insiders':     insiders,
        'institutions': institutions,
    }
