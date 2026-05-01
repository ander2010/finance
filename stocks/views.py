import json
import yfinance as yf
from datetime import date, timedelta
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.core.paginator import Paginator
from django.db.models import Max, Min
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST
from .models import Ticker, Watchlist, TickerAnalysis, StockList, Trade, TradingProfile
from .forms import AddTickersForm, CreateListForm, AssignListForm
from .analysis import run_analysis_for_ticker
from .services.smart_money import get_smart_money
from .services.trade_engine import generate_trade_plan
from .services.performance import compute_metrics, compute_equity_curve, compute_breakdown
from .email_utils import send_analysis_report_email

PAGE_SIZE = 50
ANALYZE_ALL_SESSION_KEY = 'stocks_analyze_all_job'


@login_required
def dashboard(request):
    user_lists = StockList.objects.filter(user=request.user)

    active_list_id = request.GET.get('list')
    active_signal  = request.GET.get('signal')
    search_q       = request.GET.get('search', '').strip().upper()
    if active_signal not in ('BUY', 'WATCH', 'NO_BUY'):
        active_signal = None

    active_list = None

    qs = (
        Watchlist.objects
        .filter(user=request.user)
        .select_related('ticker', 'stock_list')
        .prefetch_related('ticker__analyses')
        .order_by('ticker__symbol')
    )

    if active_list_id == '0':
        qs          = qs.filter(stock_list__isnull=True)
        active_list = 'general'
    elif active_list_id:
        try:
            active_list = StockList.objects.get(id=active_list_id, user=request.user)
            qs          = qs.filter(stock_list=active_list)
        except StockList.DoesNotExist:
            active_list    = None
            active_list_id = None

    # Search filter at DB level — case-insensitive prefix match
    if search_q:
        qs = qs.filter(ticker__symbol__istartswith=search_q)

    add_form    = AddTickersForm()
    create_form = CreateListForm()
    all_rows    = []
    buy_count = watch_count = no_buy_count = 0

    for entry in qs:
        analysis = entry.ticker.latest_analysis()
        all_rows.append({'entry': entry, 'ticker': entry.ticker, 'analysis': analysis})
        if analysis:
            if analysis.signal == 'BUY':
                buy_count += 1
            elif analysis.signal == 'WATCH':
                watch_count += 1
            else:
                no_buy_count += 1

    # Apply signal filter AFTER counting (counts always reflect the full list)
    if active_signal:
        filtered_rows = [r for r in all_rows if r['analysis'] and r['analysis'].signal == active_signal]
    else:
        filtered_rows = all_rows

    # Paginate only when no active filters (search disables pagination)
    if not active_list_id and not active_signal and not search_q:
        paginator   = Paginator(filtered_rows, PAGE_SIZE)
        page_number = request.GET.get('page', 1)
        page_obj    = paginator.get_page(page_number)
        rows        = list(page_obj)
    else:
        page_obj = None
        rows     = filtered_rows

    # list_param helps the template build combined ?list=X&signal=Y URLs cleanly
    list_param = f"list={active_list_id}&" if active_list_id else ""

    return render(request, 'stocks/dashboard.html', {
        'rows':             rows,
        'page_obj':         page_obj,
        'add_form':         add_form,
        'create_form':      create_form,
        'user_lists':       user_lists,
        'active_list':      active_list,
        'active_list_id':   active_list_id,
        'active_signal':    active_signal,
        'search_q':         search_q,
        'list_param':       list_param,
        'total':            len(filtered_rows),
        'total_unfiltered': len(all_rows),
        'buy_count':        buy_count,
        'watch_count':      watch_count,
        'no_buy_count':     no_buy_count,
    })


@login_required
def add_ticker(request):
    if request.method != 'POST':
        return redirect('dashboard')

    form = AddTickersForm(request.POST)
    if not form.is_valid():
        for error in form.errors.values():
            messages.error(request, error.as_text())
        return redirect('dashboard')

    tickers  = form.cleaned_data['tickers']
    added, skipped, failed = [], [], []

    for symbol in tickers:
        ticker, _ = Ticker.objects.get_or_create(symbol=symbol)
        _, watch_created = Watchlist.objects.get_or_create(user=request.user, ticker=ticker)
        if not watch_created:
            skipped.append(symbol)
            continue
        try:
            run_analysis_for_ticker(ticker)
            added.append(symbol)
        except Exception as e:
            added.append(symbol)
            failed.append(f'{symbol} (analysis error: {e})')

    if added:
        messages.success(request, f'Added: {", ".join(added)}')
    if skipped:
        messages.warning(request, f'Already in your watchlist: {", ".join(skipped)}')
    if failed:
        messages.error(request, f'Could not fetch data for: {", ".join(failed)}')

    return redirect('dashboard')


@login_required
def remove_ticker(request, entry_id):
    entry = get_object_or_404(Watchlist, id=entry_id, user=request.user)
    symbol = entry.ticker.symbol
    entry.delete()
    messages.success(request, f'{symbol} removed from your watchlist.')
    return redirect('dashboard')


@login_required
def analyze_ticker(request, entry_id):
    entry = get_object_or_404(Watchlist, id=entry_id, user=request.user)
    force = request.GET.get('force') == '1'
    try:
        analysis = run_analysis_for_ticker(entry.ticker, force=force)
        if analysis:
            generate_trade_plan(request.user, entry.ticker, analysis)
            messages.success(request, f'Analysis updated for {entry.ticker.symbol}.')
        else:
            messages.warning(request, f'Not enough data for {entry.ticker.symbol}.')
    except Exception as e:
        messages.error(request, f'Error analyzing {entry.ticker.symbol}: {e}')
    return redirect('dashboard')


@login_required
def analyze_all(request):
    if request.GET.get('cancel') == '1':
        request.session.pop(ANALYZE_ALL_SESSION_KEY, None)
        request.session.modified = True
        messages.warning(request, 'Batch analysis cancelled.')
        return redirect('dashboard')

    if request.GET.get('step') != '1' or ANALYZE_ALL_SESSION_KEY not in request.session:
        return _start_analyze_all_job(request, force=request.GET.get('force') == '1')

    job = request.session.get(ANALYZE_ALL_SESSION_KEY)
    if not job:
        return _start_analyze_all_job(request, force=False)

    ticker_ids = job.get('ticker_ids', [])
    total      = len(ticker_ids)
    index      = int(job.get('index', 0))

    if index >= total:
        return _finish_analyze_all_job(request, job)

    try:
        ticker = Ticker.objects.get(id=ticker_ids[index])
        analysis = run_analysis_for_ticker(ticker, force=job.get('force', False))
        if analysis:
            generate_trade_plan(request.user, ticker, analysis)
            job['success'] = int(job.get('success', 0)) + 1
            job['last_status'] = f'{ticker.symbol}: {analysis.signal} ({analysis.confidence_score}%)'
        else:
            job['errors'] = int(job.get('errors', 0)) + 1
            job['last_status'] = f'{ticker.symbol}: not enough data'
    except Exception as exc:
        symbol = ticker.symbol if 'ticker' in locals() else 'unknown'
        job['errors'] = int(job.get('errors', 0)) + 1
        job['last_status'] = f'{symbol}: error - {exc}'

    job['index'] = index + 1
    request.session[ANALYZE_ALL_SESSION_KEY] = job
    request.session.modified = True

    if job['index'] >= total:
        return _finish_analyze_all_job(request, job)

    return _render_analyze_progress(request, job)


def _start_analyze_all_job(request, force: bool):
    entries = (
        Watchlist.objects
        .filter(user=request.user)
        .select_related('ticker')
        .order_by('ticker__symbol')
    )
    ticker_ids = []
    seen = set()
    for entry in entries:
        if entry.ticker_id in seen:
            continue
        seen.add(entry.ticker_id)
        ticker_ids.append(entry.ticker_id)

    if not ticker_ids:
        messages.warning(request, 'Your watchlist is empty.')
        return redirect('dashboard')

    job = {
        'ticker_ids': ticker_ids,
        'index': 0,
        'success': 0,
        'errors': 0,
        'force': force,
        'last_status': 'Starting batch analysis...',
    }
    request.session[ANALYZE_ALL_SESSION_KEY] = job
    request.session.modified = True
    return _render_analyze_progress(request, job, delay_ms=500)


def _finish_analyze_all_job(request, job):
    email_message = _send_analysis_report_if_enabled(request.user)
    label = 'Force re-analysis complete' if job.get('force') else 'Analysis complete'
    if email_message:
        messages.info(request, email_message)
    messages.success(
        request,
        f"{label}: {job.get('success', 0)} updated, {job.get('errors', 0)} errors."
    )

    request.session.pop(ANALYZE_ALL_SESSION_KEY, None)
    request.session.modified = True
    return _render_analyze_progress(request, job, done=True, delay_ms=1600)


def _send_analysis_report_if_enabled(user):
    profile, _ = TradingProfile.objects.get_or_create(user=user)
    if user.email and profile.send_analysis_email:
        analysis_rows = []
        for entry in Watchlist.objects.filter(user=user).select_related('ticker'):
            analysis = entry.ticker.latest_analysis()
            analysis_rows.append({'ticker': entry.ticker, 'analysis': analysis})

        # Latest trade plan per ticker (Python-level dedup, avoids SQLite timestamp issues)
        seen_tickers = set()
        latest_trades = []
        for trade in Trade.objects.filter(user=user).select_related('ticker', 'analysis').order_by('-created_at'):
            if trade.ticker_id not in seen_tickers:
                seen_tickers.add(trade.ticker_id)
                latest_trades.append(trade)
        latest_trades.sort(key=lambda t: (-t.rr_ratio, -t.confidence_score))

        try:
            send_analysis_report_email(user, analysis_rows, latest_trades)
            return f'Analysis report sent to {user.email}.'
        except Exception as e:
            return f'Could not send email: {e}'
    return ''


def _render_analyze_progress(request, job, done=False, delay_ms=900):
    total = len(job.get('ticker_ids', []))
    completed = min(int(job.get('index', 0)), total)
    progress = int(round((completed / total) * 100)) if total else 0
    next_url = reverse('dashboard') if done else f"{reverse('analyze_all')}?step=1"

    return render(request, 'stocks/analyze_progress.html', {
        'job': job,
        'done': done,
        'total': total,
        'completed': completed,
        'remaining': max(total - completed, 0),
        'progress': progress,
        'next_url': next_url,
        'cancel_url': f"{reverse('analyze_all')}?cancel=1",
        'delay_ms': delay_ms,
        'delay_seconds': max(1, round(delay_ms / 1000)),
    })


@login_required
def stock_detail(request, entry_id):
    entry    = get_object_or_404(Watchlist, id=entry_id, user=request.user)
    ticker   = entry.ticker
    analyses = TickerAnalysis.objects.filter(ticker=ticker).order_by('-date')[:30]
    prices   = ticker.prices.order_by('-date')[:60]

    price_labels = [str(p.date) for p in reversed(list(prices))]
    price_closes = [p.close for p in reversed(list(prices))]

    # 52W high/low from local DB — fast, no network call
    one_year_ago = date.today() - timedelta(days=365)
    agg = ticker.prices.filter(date__gte=one_year_ago).aggregate(h=Max('high'), l=Min('low'))
    w52_high = round(agg['h'], 2) if agg['h'] else None
    w52_low  = round(agg['l'], 2) if agg['l'] else None

    # P/E from yfinance (network — wrapped in try/except)
    pe_ratio = None
    pe_type  = 'Trailing'
    try:
        info = yf.Ticker(ticker.symbol).info or {}
        pe   = info.get('trailingPE')
        if pe:
            pe_ratio = round(float(pe), 2)
            pe_type  = 'Trailing'
        else:
            pe = info.get('forwardPE')
            if pe:
                pe_ratio = round(float(pe), 2)
                pe_type  = 'Forward'
    except Exception:
        pass

    return render(request, 'stocks/detail.html', {
        'entry':        entry,
        'ticker':       ticker,
        'analyses':     analyses,
        'latest':       analyses.first() if analyses else None,
        'price_labels': price_labels,
        'price_closes': price_closes,
        'w52_high':     w52_high,
        'w52_low':      w52_low,
        'pe_ratio':     pe_ratio,
        'pe_type':      pe_type,
    })


# ─── TRADE PLANS ───────────────────────────────────────────────────────────────

@login_required
def trade_plans(request):
    profile, _ = TradingProfile.objects.get_or_create(user=request.user)

    val_filter = request.GET.get('status', '')   # VALID / WATCHLIST / INVALID / ''

    # Latest trade per ticker (Python dedup — avoids SQLite timestamp issues)
    seen_tickers = set()
    latest_ids   = []
    for trade in Trade.objects.filter(user=request.user).order_by('-created_at'):
        if trade.ticker_id not in seen_tickers:
            seen_tickers.add(trade.ticker_id)
            latest_ids.append(trade.id)

    all_latest     = Trade.objects.filter(id__in=latest_ids).select_related('ticker', 'analysis')
    valid_count    = all_latest.filter(validation_status='VALID').count()
    watchlist_count= all_latest.filter(validation_status='WATCHLIST').count()
    invalid_count  = all_latest.filter(validation_status='INVALID').count()
    total_risk     = sum(t.risk_amount for t in all_latest.filter(validation_status='VALID'))

    qs = all_latest
    if val_filter in ('VALID', 'WATCHLIST', 'INVALID'):
        qs = qs.filter(validation_status=val_filter)
    # Sort: VALID first, then WATCHLIST, then INVALID; within each group by score desc
    order_map   = {'VALID': 0, 'WATCHLIST': 1, 'INVALID': 2}
    qs_list     = sorted(qs, key=lambda t: (order_map.get(t.validation_status, 3), -t.confidence_score))

    spy_data = {}
    try:
        from .analysis import get_spy_filter
        spy_data = get_spy_filter()
    except Exception:
        pass

    return render(request, 'stocks/trade_plans.html', {
        'trades':          qs_list,
        'profile':         profile,
        'val_filter':      val_filter,
        'valid_count':     valid_count,
        'watchlist_count': watchlist_count,
        'invalid_count':   invalid_count,
        'total_risk':      round(total_risk, 2),
        'spy_data':        spy_data,
    })


# ─── PERFORMANCE ───────────────────────────────────────────────────────────────

@login_required
def performance(request):
    profile, _ = TradingProfile.objects.get_or_create(user=request.user)

    all_trades = list(
        Trade.objects
        .filter(user=request.user)
        .select_related('ticker')
        .order_by('exit_date')
    )

    # Counts by paper_status
    pending_count = sum(1 for t in all_trades if t.paper_status == 'PENDING' and t.validation_status == 'VALID')
    active_count  = sum(1 for t in all_trades if t.paper_status == 'ACTIVE')
    closed_count  = sum(1 for t in all_trades if t.paper_status == 'CLOSED')

    closed_trades = [t for t in all_trades if t.paper_status == 'CLOSED']

    # Overall metrics
    metrics = compute_metrics(closed_trades)

    # Equity curve
    equity_curve, final_balance = compute_equity_curve(
        closed_trades, starting_capital=profile.account_size
    )
    equity_dates    = [p['date']    for p in equity_curve]
    equity_balances = [p['balance'] for p in equity_curve]

    # Breakdowns
    by_strategy = compute_breakdown(closed_trades, 'strategy_type')
    by_rvol     = compute_breakdown(closed_trades, 'rvol_bucket')
    by_rr       = compute_breakdown(closed_trades, 'rr_bucket')

    # Recent closed trades (last 20, newest first)
    recent_closed = sorted(
        closed_trades,
        key=lambda t: t.exit_date or date.min,
        reverse=True,
    )[:20]

    return render(request, 'stocks/performance.html', {
        'profile':          profile,
        'pending_count':    pending_count,
        'active_count':     active_count,
        'closed_count':     closed_count,
        'metrics':          metrics,
        'final_balance':    final_balance,
        'equity_dates':     json.dumps(equity_dates),
        'equity_balances':  json.dumps(equity_balances),
        'by_strategy':      by_strategy,
        'by_rvol':          by_rvol,
        'by_rr':            by_rr,
        'recent_closed':    recent_closed,
    })


# ─── LISTS ─────────────────────────────────────────────────────────────────────

@login_required
@require_POST
def create_list(request):
    form = CreateListForm(request.POST)
    if form.is_valid():
        name = form.cleaned_data['name']
        _, created = StockList.objects.get_or_create(user=request.user, name=name)
        if created:
            messages.success(request, f'List "{name}" created.')
        else:
            messages.warning(request, f'List "{name}" already exists.')
    else:
        for error in form.errors.values():
            messages.error(request, error.as_text())
    return redirect('dashboard')


@login_required
@require_POST
def delete_list(request, list_id):
    lst = get_object_or_404(StockList, id=list_id, user=request.user)
    name = lst.name
    lst.delete()
    messages.success(request, f'List "{name}" deleted.')
    return redirect('dashboard')


@login_required
@require_POST
def assign_to_list(request, entry_id):
    entry = get_object_or_404(Watchlist, id=entry_id, user=request.user)
    form  = AssignListForm(request.user, request.POST)
    if form.is_valid():
        entry.stock_list = form.cleaned_data['stock_list']
        entry.save(update_fields=['stock_list'])
    return JsonResponse({'ok': True, 'list_name': entry.list_name})


# ─── AJAX ──────────────────────────────────────────────────────────────────────

@login_required
@require_GET
def company_info(request, symbol):
    symbol = symbol.upper()
    if not Watchlist.objects.filter(user=request.user, ticker__symbol=symbol).exists():
        return JsonResponse({'error': 'Not found'}, status=404)

    try:
        yf_ticker = yf.Ticker(symbol)
        info = yf_ticker.info or {}

        company = {
            'name':           info.get('longName') or info.get('shortName') or symbol,
            'sector':         info.get('sector', '—'),
            'industry':       info.get('industry', '—'),
            'country':        info.get('country', '—'),
            'website':        info.get('website', ''),
            'description':    (info.get('longBusinessSummary') or '')[:500],
            'market_cap':     info.get('marketCap'),
            'current_price':  info.get('currentPrice') or info.get('regularMarketPrice'),
            'currency':       info.get('currency', 'USD'),
            'exchange':       info.get('exchange', ''),
            'fifty_two_high': info.get('fiftyTwoWeekHigh'),
            'fifty_two_low':  info.get('fiftyTwoWeekLow'),
        }

        rec_key     = info.get('recommendationKey', '')
        rec_mean    = info.get('recommendationMean')
        n_analysts  = info.get('numberOfAnalystOpinions', 0)
        target_mean = info.get('targetMeanPrice')
        target_high = info.get('targetHighPrice')
        target_low  = info.get('targetLowPrice')

        breakdown = {'strongBuy': 0, 'buy': 0, 'hold': 0, 'sell': 0, 'strongSell': 0}
        try:
            summary = yf_ticker.recommendations_summary
            if summary is not None and not summary.empty:
                row = summary.iloc[0]
                for key in breakdown:
                    breakdown[key] = int(row.get(key, 0))
        except Exception:
            pass

        label_map = {
            'strong_buy':   'Strong Buy',
            'buy':          'Buy',
            'hold':         'Hold',
            'underperform': 'Underperform',
            'sell':         'Sell',
        }
        consensus_label = label_map.get(rec_key, rec_key.replace('_', ' ').title() if rec_key else '—')

        analysts = {
            'consensus':   consensus_label,
            'rec_key':     rec_key,
            'mean_score':  round(rec_mean, 2) if rec_mean else None,
            'n_analysts':  n_analysts,
            'target_mean': round(target_mean, 2) if target_mean else None,
            'target_high': round(target_high, 2) if target_high else None,
            'target_low':  round(target_low, 2) if target_low else None,
            'breakdown':   breakdown,
        }

        return JsonResponse({'company': company, 'analysts': analysts})

    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
@require_POST
def accumulation_scan(request):
    """AJAX: run accumulation zone scan on user's watchlist. Returns JSON."""
    from .accumulation import analyze_accumulation_for_ticker

    force   = request.POST.get('force') == '1'
    entries = (
        Watchlist.objects
        .filter(user=request.user)
        .select_related('ticker', 'stock_list')
        .order_by('ticker__symbol')
    )

    results = []
    errors  = 0

    for entry in entries:
        try:
            signal = analyze_accumulation_for_ticker(entry.ticker, force=force)
            if signal:
                results.append({
                    'symbol':    entry.ticker.symbol,
                    'list_name': entry.list_name,
                    'price':     signal.price,
                    'rsi':       signal.rsi,
                    'dist_pct':  signal.dist_from_sma200_pct,
                    'vol_ratio': signal.vol_ratio,
                    'score':     signal.score,
                    'notes':     signal.notes,
                })
        except Exception:
            errors += 1

    results.sort(key=lambda r: -r['score'])
    return JsonResponse({'results': results, 'errors': errors})


@login_required
@require_GET
def smart_money(request, symbol):
    symbol = symbol.upper()
    if not Watchlist.objects.filter(user=request.user, ticker__symbol=symbol).exists():
        return JsonResponse({'error': 'Not found'}, status=404)
    try:
        data = get_smart_money(symbol)
        return JsonResponse(data)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)
