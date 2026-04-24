from django.contrib import admin
from .models import Ticker, TickerPrice, TickerAnalysis, Watchlist, StockList


@admin.register(Ticker)
class TickerAdmin(admin.ModelAdmin):
    list_display  = ['symbol', 'last_price_update', 'watcher_count']
    search_fields = ['symbol']

    def watcher_count(self, obj):
        return obj.watchers.count()
    watcher_count.short_description = 'Watchers'


@admin.register(TickerPrice)
class TickerPriceAdmin(admin.ModelAdmin):
    list_display  = ['ticker', 'date', 'close', 'volume']
    list_filter   = ['ticker__symbol']
    date_hierarchy = 'date'


@admin.register(TickerAnalysis)
class TickerAnalysisAdmin(admin.ModelAdmin):
    list_display  = ['ticker', 'date', 'signal', 'confidence_score',
                     'current_price', 'entry_price', 'risk_reward_ratio']
    list_filter   = ['signal']
    search_fields = ['ticker__symbol']
    date_hierarchy = 'date'
    readonly_fields = ['strategies_triggered', 'explanation']


@admin.register(Watchlist)
class WatchlistAdmin(admin.ModelAdmin):
    list_display  = ['user', 'ticker', 'stock_list', 'created_at']
    list_filter   = ['user']
    search_fields = ['ticker__symbol', 'user__username']


@admin.register(StockList)
class StockListAdmin(admin.ModelAdmin):
    list_display  = ['user', 'name', 'created_at']
    list_filter   = ['user']
    search_fields = ['name', 'user__username']
