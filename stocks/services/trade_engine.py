import logging
import pandas as pd
from ..models import Trade, TradingProfile

logger = logging.getLogger('stocks.trade_engine')


def generate_trade_plan(user, ticker, analysis):
    """
    Generate (or update) a validated Trade plan for *user*.

    Flow:
      1. Rebuild price/indicator df from DB (already stored by analysis)
      2. Run the full validation engine (structure, S/R, SPY, R:R)
      3. Compute position size based on user's TradingProfile
      4. Persist Trade with all validation metadata
    """
    if not analysis or not analysis.entry_price or not analysis.stop_loss:
        logger.debug('%s: skipping — missing entry/stop', ticker.symbol)
        return None

    entry  = analysis.entry_price
    stop   = analysis.stop_loss
    target = analysis.take_profit or 0

    if entry - stop <= 0:
        logger.warning('%s: non-positive risk', ticker.symbol)
        return None

    # ── Build df and run validation ───────────────────────────────────────────
    try:
        from ..analysis import _build_df_from_db, compute_indicators, get_spy_filter
        from .validator import validate_trade

        df_raw = _build_df_from_db(ticker)
        if df_raw.empty or len(df_raw) < 30:
            raise ValueError('Not enough price history')
        df  = compute_indicators(df_raw)
        spy = get_spy_filter()

        validation = validate_trade(
            ticker=ticker,
            entry=entry, stop=stop, target=target,
            strategies=analysis.strategies_triggered or [],
            df=df, spy_data=spy,
        )
    except Exception as exc:
        logger.exception('Validation error for %s: %s', ticker.symbol, exc)
        validation = {
            'is_valid': False, 'status': 'INVALID',
            'reasons_pass': [], 'reasons_fail': [f'Validation error: {exc}'],
            'strategy_type': 'pullback',
            'resistance_level': None, 'support_level': None,
            'spy_alignment': True,
            'adjusted_target': target, 'adjusted_stop': stop,
            'rr': analysis.risk_reward_ratio or 0,
            'adjusted_score': analysis.confidence_score,
            'score_breakdown': None,
        }

    # ── Extract validated values ───────────────────────────────────────────────
    adj_target  = validation['adjusted_target'] or target
    adj_stop    = validation['adjusted_stop']   or stop
    adj_rr      = validation['rr']
    adj_score   = validation['adjusted_score']
    strategy    = validation['strategy_type']
    val_status  = validation['status']
    pass_list   = validation['reasons_pass']
    fail_list   = validation['reasons_fail']
    current_vol = validation.get('current_volume', 0)
    avg_vol_20  = validation.get('avg_volume_20', 0.0)
    rel_vol     = validation.get('relative_volume', 0.0)
    vol_quality = validation.get('volume_quality', '')

    # ── Position sizing ────────────────────────────────────────────────────────
    profile, _    = TradingProfile.objects.get_or_create(user=user)
    dollar_risk   = profile.account_size * profile.risk_per_trade_pct / 100
    risk_per_share = entry - adj_stop
    if risk_per_share <= 0:
        return None

    position_size = max(1, int(dollar_risk / risk_per_share))
    risk_amount   = round(risk_per_share * position_size, 2)
    reward_amount = round(max(0.0, (adj_target - entry)) * position_size, 2)

    # exec status mirrors validation: VALID → pending, others → invalid
    exec_status = 'pending' if val_status == 'VALID' else 'invalid'

    # Human-readable summary for rejection_reason field
    lines = ['✓ ' + r for r in pass_list] + ['✗ ' + r for r in fail_list]
    reason_text = '\n'.join(lines)

    logger.info('%s | %s | %s | entry %.2f | stop %.2f | target %.2f | %d sh | R:R %.1f | %d pts',
                ticker.symbol, strategy, val_status,
                entry, adj_stop, adj_target, position_size, adj_rr, adj_score)

    trade, _ = Trade.objects.update_or_create(
        user=user, ticker=ticker, analysis=analysis,
        defaults={
            'strategy_type':    strategy,
            'entry_price':      entry,
            'stop_loss':        adj_stop,
            'target_price':     adj_target,
            'position_size':    position_size,
            'risk_amount':      risk_amount,
            'reward_amount':    reward_amount,
            'rr_ratio':         adj_rr,
            'confidence_score': adj_score,
            'status':           exec_status,
            'rejection_reason': reason_text,
            'validation_status':  val_status,
            'validation_reasons': {'pass': pass_list, 'fail': fail_list},
            'resistance_level':   validation['resistance_level'],
            'support_level':      validation['support_level'],
            'spy_alignment':      validation['spy_alignment'],
            'score_breakdown':    validation['score_breakdown'],
            'current_volume':     current_vol,
            'avg_volume_20':      avg_vol_20,
            'relative_volume':    rel_vol,
            'volume_quality':     vol_quality,
        }
    )
    return trade
