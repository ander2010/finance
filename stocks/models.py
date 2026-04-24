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
    sma9_data            = models.JSONField(null=True, blank=True)   # 9 SMA Pullback checklist
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
