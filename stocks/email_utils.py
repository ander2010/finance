from datetime import date, timedelta
from django.core.mail import send_mail
from django.db.models import Max, Min


def _signal_color(signal):
    return {'BUY': '#22C55E', 'WATCH': '#F59E0B', 'NO_BUY': '#EF4444'}.get(signal, '#94A3B8')


def _signal_bg(signal):
    return {'BUY': 'rgba(34,197,94,0.15)', 'WATCH': 'rgba(245,158,11,0.15)',
            'NO_BUY': 'rgba(239,68,68,0.15)'}.get(signal, 'rgba(148,163,184,0.15)')


def _fmt(value, prefix='$', decimals=2):
    if value is None:
        return '—'
    try:
        return f"{prefix}{float(value):.{decimals}f}"
    except (TypeError, ValueError):
        return '—'


def _w52(ticker):
    one_year_ago = date.today() - timedelta(days=365)
    agg = ticker.prices.filter(date__gte=one_year_ago).aggregate(h=Max('high'), l=Min('low'))
    return _fmt(agg['h']), _fmt(agg['l'])


def _build_analysis_table(analysis_rows):
    th = "padding:9px 12px;color:#64748B;font-weight:600;font-size:0.78em;text-transform:uppercase;letter-spacing:.05em;white-space:nowrap;"
    header = f"""
    <thead style="background:#0F172A;">
      <tr>
        <th style="{th}text-align:left;">Ticker</th>
        <th style="{th}text-align:left;">Signal</th>
        <th style="{th}text-align:right;">Score</th>
        <th style="{th}text-align:right;">Entry</th>
        <th style="{th}text-align:right;">Stop</th>
        <th style="{th}text-align:right;">Target</th>
        <th style="{th}text-align:right;">R:R</th>
        <th style="{th}text-align:left;">Strategies</th>
        <th style="{th}text-align:right;">52W High</th>
        <th style="{th}text-align:right;">52W Low</th>
      </tr>
    </thead>"""

    rows = ''
    for row in analysis_rows:
        ticker   = row['ticker']
        analysis = row['analysis']
        if not analysis:
            continue

        color      = _signal_color(analysis.signal)
        bg         = _signal_bg(analysis.signal)
        strategies = ', '.join(analysis.strategies_triggered) if analysis.strategies_triggered else '—'
        w52h, w52l = _w52(ticker)
        entry_str  = _fmt(analysis.entry_price)
        stop_str   = _fmt(analysis.stop_loss)
        target_str = _fmt(analysis.take_profit)
        rr_str     = _fmt(analysis.risk_reward_ratio, prefix='', decimals=1) if analysis.risk_reward_ratio else '—'
        score_str  = f"{analysis.confidence_score}%"

        rows += f"""
      <tr style="border-bottom:1px solid #1E293B;">
        <td style="padding:9px 12px;font-weight:700;color:{color};">{ticker.symbol}</td>
        <td style="padding:9px 12px;">
          <span style="padding:2px 8px;border-radius:9999px;font-size:0.78em;font-weight:700;background:{bg};color:{color};">{analysis.signal}</span>
        </td>
        <td style="padding:9px 12px;text-align:right;font-weight:600;color:#F8FAFC;">{score_str}</td>
        <td style="padding:9px 12px;text-align:right;color:#F8FAFC;">{entry_str}</td>
        <td style="padding:9px 12px;text-align:right;color:#EF4444;">{stop_str}</td>
        <td style="padding:9px 12px;text-align:right;color:#22C55E;">{target_str}</td>
        <td style="padding:9px 12px;text-align:right;color:#F8FAFC;">{rr_str}</td>
        <td style="padding:9px 12px;color:#94A3B8;font-size:0.8em;">{strategies}</td>
        <td style="padding:9px 12px;text-align:right;color:#22C55E;font-size:0.85em;">{w52h}</td>
        <td style="padding:9px 12px;text-align:right;color:#EF4444;font-size:0.85em;">{w52l}</td>
      </tr>"""

    if not rows:
        rows = '<tr><td colspan="10" style="padding:16px;color:#94A3B8;text-align:center;">No data</td></tr>'

    return f'<table style="width:100%;border-collapse:collapse;font-size:0.85em;">{header}<tbody>{rows}</tbody></table>'


def _build_trade_table(trade_list):
    th = "padding:9px 12px;color:#64748B;font-weight:600;font-size:0.78em;text-transform:uppercase;letter-spacing:.05em;white-space:nowrap;"
    header = f"""
    <thead style="background:#0F172A;">
      <tr>
        <th style="{th}text-align:left;">Ticker</th>
        <th style="{th}text-align:left;">Strategy</th>
        <th style="{th}text-align:right;">Entry</th>
        <th style="{th}text-align:right;">Stop</th>
        <th style="{th}text-align:right;">Target</th>
        <th style="{th}text-align:right;">Shares</th>
        <th style="{th}text-align:right;">Risk $</th>
        <th style="{th}text-align:right;">Reward $</th>
        <th style="{th}text-align:right;">R:R</th>
        <th style="{th}text-align:center;">Action</th>
      </tr>
    </thead>"""

    rows = ''
    for trade in trade_list:
        action        = trade.action_label
        action_color  = '#22C55E' if action == 'BUY' else '#F59E0B' if action == 'WAIT' else '#EF4444'
        rr_color      = '#22C55E' if trade.rr_ratio >= 2.0 else '#F59E0B' if trade.rr_ratio >= 1.5 else '#EF4444'
        entry_str     = _fmt(trade.entry_price)
        stop_str      = _fmt(trade.stop_loss)
        target_str    = _fmt(trade.target_price) if trade.target_price else '—'
        risk_str      = _fmt(trade.risk_amount, decimals=0)
        reward_str    = _fmt(trade.reward_amount, decimals=0) if trade.reward_amount else '—'
        rr_str        = f"{trade.rr_ratio:.1f}"
        strategy_name = trade.get_strategy_type_display()

        rows += f"""
      <tr style="border-bottom:1px solid #1E293B;">
        <td style="padding:9px 12px;font-weight:700;color:#F8FAFC;">{trade.ticker.symbol}</td>
        <td style="padding:9px 12px;color:#94A3B8;font-size:0.85em;">{strategy_name}</td>
        <td style="padding:9px 12px;text-align:right;color:#F8FAFC;">{entry_str}</td>
        <td style="padding:9px 12px;text-align:right;color:#EF4444;">{stop_str}</td>
        <td style="padding:9px 12px;text-align:right;color:#22C55E;">{target_str}</td>
        <td style="padding:9px 12px;text-align:right;color:#F8FAFC;">{trade.position_size}</td>
        <td style="padding:9px 12px;text-align:right;color:#EF4444;">{risk_str}</td>
        <td style="padding:9px 12px;text-align:right;color:#22C55E;">{reward_str}</td>
        <td style="padding:9px 12px;text-align:right;font-weight:700;color:{rr_color};">{rr_str}</td>
        <td style="padding:9px 12px;text-align:center;">
          <span style="padding:2px 8px;border-radius:9999px;font-size:0.78em;font-weight:700;
            color:{action_color};background:{action_color}22;">{action}</span>
        </td>
      </tr>"""

        if trade.status == 'invalid' and trade.rejection_reason:
            rows += f"""
      <tr style="background:rgba(239,68,68,0.04);">
        <td colspan="10" style="padding:4px 12px 8px 24px;font-size:0.78em;color:#EF444499;font-style:italic;">
          ↳ {trade.rejection_reason}
        </td>
      </tr>"""

    if not rows:
        rows = '<tr><td colspan="10" style="padding:16px;color:#94A3B8;text-align:center;">No trade plans — run Analyze All first</td></tr>'

    return f'<table style="width:100%;border-collapse:collapse;font-size:0.85em;">{header}<tbody>{rows}</tbody></table>'


def send_analysis_report_email(user, analysis_rows, trade_plans):
    if not user.email:
        return

    today_str    = date.today().strftime('%B %d, %Y')
    display_name = user.get_full_name() or user.username
    trade_list   = list(trade_plans)

    buy_rows       = [r for r in analysis_rows if r['analysis'] and r['analysis'].signal == 'BUY']
    valid_trades   = [t for t in trade_list if t.status == 'pending']
    n_buy          = len(buy_rows)
    n_valid_trades = len(valid_trades)

    analysis_table = _build_analysis_table(buy_rows)
    trade_table    = _build_trade_table(valid_trades)

    section_label = "padding:0 0 10px 0;font-size:0.8em;font-weight:700;color:#64748B;text-transform:uppercase;letter-spacing:.08em;"

    html_body = f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#020617;font-family:'Segoe UI',Arial,sans-serif;color:#F8FAFC;">
<div style="max-width:980px;margin:0 auto;padding:28px 16px;">
<div style="background:#1E293B;border:1px solid #334155;border-radius:12px;overflow:hidden;">

  <div style="padding:22px 28px;border-bottom:1px solid #334155;background:rgba(34,197,94,0.07);">
    <span style="font-size:1.3em;font-weight:800;">Stock<span style="color:#22C55E;">Analyzer</span></span>
    <p style="margin:4px 0 0;font-size:0.85em;color:#94A3B8;">Analysis Report — {today_str}</p>
  </div>

  <div style="padding:18px 28px;border-bottom:1px solid #334155;">
    <p style="margin:0 0 4px;font-size:0.95em;">Hi {display_name},</p>
    <p style="margin:0;font-size:0.85em;color:#94A3B8;">
      <strong style="color:#22C55E;">{n_buy} BUY</strong> signal(s) —
      <strong style="color:#22C55E;">{n_valid_trades} valid</strong> trade plan(s).
    </p>
  </div>

  <div style="padding:20px 16px 8px;">
    <p style="{section_label}">BUY Signals ({n_buy})</p>
    <div style="overflow-x:auto;">{analysis_table}</div>
  </div>

  <div style="padding:20px 16px 8px;border-top:1px solid #334155;">
    <p style="{section_label}">Valid Trade Plans ({n_valid_trades})</p>
    <div style="overflow-x:auto;">{trade_table}</div>
  </div>

  <div style="padding:16px 28px;margin-top:8px;border-top:1px solid #334155;background:#0F172A;">
    <p style="margin:0;font-size:0.75em;color:#475569;">
      Generated by StockAnalyzer · Not financial advice · Toggle this email in your Profile settings.
    </p>
  </div>

</div>
</div>
</body>
</html>"""

    send_mail(
        subject=f'[StockAnalyzer] {n_buy} BUY · {n_valid_trades} Trade Plan(s) · {today_str}',
        message=f'StockAnalyzer report {today_str}: {n_buy} BUY signals, {n_valid_trades} valid trades.',
        from_email=None,
        recipient_list=[user.email],
        html_message=html_body,
        fail_silently=False,
    )
