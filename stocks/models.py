from django.db import models
from django.contrib.auth.models import User


class StockList(models.Model):
    """User-defined portfolio category (e.g. "Swing", "Tech", "Finanzas")."""
    user       = models.ForeignKey(User, on_delete=models.CASCADE, related_name='stock_lists')
    name       = models.CharField(max_length=50)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'name')
        ordering = ['name']

    def __str__(self):
        return f"{self.user.username} / {self.name}"


class Ticker(models.Model):
    """A stock symbol — shared across all users."""
    symbol             = models.CharField(max_length=10, unique=True, db_index=True)
    last_price_update  = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['symbol']

    def __str__(self):
        return self.symbol

    def latest_analysis(self):
        return self.analyses.order_by('-date').first()

    def latest_price(self):
        return self.prices.order_by('-date').first()


class TickerPrice(models.Model):
    """OHLCV — shared, downloaded once per ticker."""
    ticker = models.ForeignKey(Ticker, on_delete=models.CASCADE, related_name='prices')
    date   = models.DateField(db_index=True)
    open   = models.FloatField()
    high   = models.FloatField()
    low    = models.FloatField()
    close  = models.FloatField()
    volume = models.BigIntegerField()

    class Meta:
        unique_together = ('ticker', 'date')
        ordering = ['-date']

    def __str__(self):
        return f"{self.ticker.symbol} {self.date} close={self.close}"


class TickerAnalysis(models.Model):
    """Technical analysis — shared, computed once per ticker per day."""
    SIGNAL_CHOICES = [
        ('BUY',    'BUY'),
        ('WATCH',  'WATCH'),
        ('NO_BUY', 'NO BUY'),
    ]

    ticker               = models.ForeignKey(Ticker, on_delete=models.CASCADE, related_name='analyses')
    date                 = models.DateField(db_index=True)
    signal               = models.CharField(max_length=10, choices=SIGNAL_CHOICES, default='NO_BUY')
    confidence_score     = models.IntegerField(default=0)
    current_price        = models.FloatField(null=True, blank=True)
    entry_price          = models.FloatField(null=True, blank=True)
    stop_loss            = models.FloatField(null=True, blank=True)
    take_profit          = models.FloatField(null=True, blank=True)
    risk_reward_ratio    = models.FloatField(null=True, blank=True)
    strategies_triggered = models.JSONField(default=list)
    sma9_data            = models.JSONField(null=True, blank=True)
    market_structure     = models.JSONField(null=True, blank=True)   # {bullish, hh_ratio, hl_ratio, spy_above_sma200}
    explanation          = models.TextField(blank=True)
    created_at           = models.DateTimeField(auto_now_add=True)
    updated_at           = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('ticker', 'date')
        ordering = ['-date']

    def __str__(self):
        return f"{self.ticker.symbol} {self.date} → {self.signal} ({self.confidence_score}%)"

    @property
    def signal_color(self):
        return {'BUY': '#22C55E', 'WATCH': '#F59E0B', 'NO_BUY': '#EF4444'}.get(self.signal, '#EF4444')

    @property
    def sma9_valid(self):
        return bool(self.sma9_data and self.sma9_data.get('valid'))


class TradingProfile(models.Model):
    """Per-user account and risk settings for position sizing."""
    user               = models.OneToOneField(User, on_delete=models.CASCADE, related_name='trading_profile')
    account_size        = models.FloatField(default=10000.0)
    risk_per_trade_pct  = models.FloatField(default=1.0)   # 1.0 = 1 %
    send_analysis_email = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.user.username} — ${self.account_size:,.0f} @ {self.risk_per_trade_pct}%"

    @property
    def dollar_risk(self):
        return round(self.account_size * self.risk_per_trade_pct / 100, 2)


class Watchlist(models.Model):
    """Per-user ticker tracking — optionally assigned to a StockList."""
    user       = models.ForeignKey(User, on_delete=models.CASCADE, related_name='watchlist')
    ticker     = models.ForeignKey(Ticker, on_delete=models.CASCADE, related_name='watchers')
    stock_list = models.ForeignKey(
        StockList, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='entries'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'ticker')
        ordering = ['ticker__symbol']

    def __str__(self):
        return f"{self.user.username} → {self.ticker.symbol}"

    @property
    def list_name(self):
        return self.stock_list.name if self.stock_list else 'General'


class AccumulationSignal(models.Model):
    """
    Accumulation zone detection — shared per ticker per day, completely
    separate from TickerAnalysis / existing strategy pipeline.

    signal_type:
      ACCUM        — in the zone, waiting (RSI low, near SMA200, no breakout yet)
      READY_TO_BUY — confirmed setup: RSI>40, breakout, volume spike, above SMA9/SMA20
    """
    TYPE_CHOICES = [('ACCUM', 'Accumulation'), ('READY_TO_BUY', 'Ready to Buy')]

    ticker               = models.ForeignKey(Ticker, on_delete=models.CASCADE, related_name='accumulation_signals')
    date                 = models.DateField(db_index=True)
    signal_type          = models.CharField(max_length=15, choices=TYPE_CHOICES, default='ACCUM')
    price                = models.FloatField()
    rsi                  = models.FloatField()
    dist_from_sma200_pct = models.FloatField()   # positive = above SMA200, negative = below
    vol_ratio            = models.FloatField()    # today vol / 20-day avg vol
    resistance_level     = models.FloatField(null=True, blank=True)
    entry_price          = models.FloatField(null=True, blank=True)  # populated on READY_TO_BUY
    stop_loss            = models.FloatField(null=True, blank=True)
    target_price         = models.FloatField(null=True, blank=True)
    score                = models.IntegerField(default=0)
    notes                = models.TextField(blank=True)
    created_at           = models.DateTimeField(auto_now_add=True)
    updated_at           = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('ticker', 'date')
        ordering = ['-date', '-score']

    def __str__(self):
        return f"{self.ticker.symbol} {self.date} {self.signal_type} score={self.score}"


class Trade(models.Model):
    """Per-user, per-ticker trade plan generated from TickerAnalysis + TradingProfile."""
    STRATEGY_CHOICES = [
        ('pullback',      'Pullback'),
        ('breakout',      'Breakout'),
        ('sma9_pullback', 'SMA9 Pullback'),
        ('multi',         'Multi-Strategy'),
    ]
    STATUS_CHOICES = [
        ('pending',     'Pending'),
        ('active',      'Active'),
        ('closed_win',  'Closed – Win'),
        ('closed_loss', 'Closed – Loss'),
        ('invalid',     'Invalid'),
    ]
    PAPER_STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('ACTIVE',  'Active'),
        ('CLOSED',  'Closed'),
    ]
    OUTCOME_CHOICES = [
        ('WIN',       'Win'),
        ('LOSS',      'Loss'),
        ('BREAKEVEN', 'Breakeven'),
    ]

    user             = models.ForeignKey(User, on_delete=models.CASCADE, related_name='trades')
    ticker           = models.ForeignKey(Ticker, on_delete=models.CASCADE, related_name='trades')
    analysis         = models.ForeignKey(TickerAnalysis, on_delete=models.SET_NULL, null=True, blank=True)
    strategy_type    = models.CharField(max_length=20, choices=STRATEGY_CHOICES, default='pullback')
    entry_price      = models.FloatField()
    stop_loss        = models.FloatField()
    target_price     = models.FloatField(default=0)
    position_size    = models.IntegerField(default=0)
    risk_amount      = models.FloatField(default=0)
    reward_amount    = models.FloatField(default=0)
    rr_ratio         = models.FloatField(default=0)
    confidence_score = models.IntegerField(default=0)
    status             = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    rejection_reason   = models.TextField(blank=True)
    # Validation engine fields
    VALIDATION_CHOICES = [('VALID','Valid'), ('WATCHLIST','Watchlist'), ('INVALID','Invalid')]
    validation_status  = models.CharField(max_length=20, choices=VALIDATION_CHOICES, default='INVALID')
    validation_reasons = models.JSONField(default=dict)   # {'pass': [...], 'fail': [...]}
    resistance_level   = models.FloatField(null=True, blank=True)
    support_level      = models.FloatField(null=True, blank=True)
    spy_alignment      = models.BooleanField(default=True)
    score_breakdown    = models.JSONField(null=True, blank=True)
    # Volume metrics
    current_volume     = models.BigIntegerField(null=True, blank=True)
    avg_volume_20      = models.FloatField(null=True, blank=True)
    relative_volume    = models.FloatField(null=True, blank=True)
    volume_quality     = models.CharField(max_length=10, blank=True)
    # Paper trading / performance tracking
    paper_status  = models.CharField(max_length=10, choices=PAPER_STATUS_CHOICES, default='PENDING')
    outcome       = models.CharField(max_length=10, choices=OUTCOME_CHOICES, blank=True)
    entry_date    = models.DateField(null=True, blank=True)
    exit_date     = models.DateField(null=True, blank=True)
    exit_price    = models.FloatField(null=True, blank=True)
    pnl_dollars   = models.FloatField(null=True, blank=True)
    pnl_percent   = models.FloatField(null=True, blank=True)
    max_drawdown  = models.FloatField(null=True, blank=True)
    max_profit    = models.FloatField(null=True, blank=True)
    days_held     = models.IntegerField(null=True, blank=True)
    created_at    = models.DateTimeField(auto_now_add=True)
    updated_at    = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('user', 'ticker', 'analysis')
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user.username} | {self.ticker.symbol} | {self.get_strategy_type_display()} | {self.status}"

    @property
    def action_label(self):
        if self.validation_status == 'VALID':
            return 'BUY'
        if self.validation_status == 'WATCHLIST':
            return 'WAIT'
        return 'REJECT'
