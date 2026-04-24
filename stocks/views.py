import yfinance as yf
from datetime import date, timedelta
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.core.paginator import Paginator
from django.db.models import Max, Min
from django.views.decorators.http import require_GET, require_POST
from .models import Ticker, Watchlist, TickerAnalysis, StockList
from .forms import AddTickersForm, CreateListForm, AssignListForm
from .analysis import run_analysis_for_ticker
from .services.smart_money import get_smart_money

PAGE_SIZE = 50


@login_required
def dashboard(request):
    user_lists = StockList.objects.filter(user=request.user)

    active_list_id = request.GET.get('list')
    active_signal  = request.GET.get('signal')
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

    # Paginate only when no active filters
    if not active_list_id and not active_signal:
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
        'rows':           rows,
        'page_obj':       page_obj,
        'add_form':       add_form,
        'create_form':    create_form,
        'user_lists':     user_lists,
        'active_list':    active_list,
        'active_list_id': active_list_id,
        'active_signal':  active_signal,
        'list_param':     list_param,
        'total':          len(filtered_rows),
        'total_unfiltered': len(all_rows),
        'buy_count':      buy_count,
        'watch_count':    watch_count,
        'no_buy_count':   no_buy_count,
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
            messages.success(request, f'Analysis updated for {entry.ticker.symbol}.')
        else:
            messages.warning(request, f'Not enough data for {entry.ticker.symbol}.')
    except Exception as e:
        messages.error(request, f'Error analyzing {entry.ticker.symbol}: {e}')
    return redirect('dashboard')


@login_required
def analyze_all(request):
    entries = Watchlist.objects.filter(user=request.user).select_related('ticker')
    force   = request.GET.get('force') == '1'
    seen    = set()
    success = errors = skipped = 0
    for entry in entries:
        if entry.ticker.symbol in seen:
            success += 1
            continue
        seen.add(entry.ticker.symbol)
        try:
            run_analysis_for_ticker(entry.ticker, force=force)
            success += 1
        except Exception:
            errors += 1
    label = 'Re-analyzed' if force else 'Analysis complete'
    messages.success(request, f'{label}: {success} updated, {errors} errors.')
    return redirect('dashboard')


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
