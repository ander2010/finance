from django import forms
from .models import StockList, TradingProfile


class AddTickersForm(forms.Form):
    tickers = forms.CharField(
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': 'e.g. AAPL  — or type to filter watchlist',
            'autocomplete': 'off',
        }),
        label='Tickers',
    )

    def clean_tickers(self):
        raw = self.cleaned_data['tickers']
        tickers = [t.strip().upper() for t in raw.split(',') if t.strip()]
        if not tickers:
            raise forms.ValidationError('Enter at least one ticker.')
        invalid = [t for t in tickers if not t.isalpha() or len(t) > 10]
        if invalid:
            raise forms.ValidationError(f'Invalid tickers: {", ".join(invalid)}')
        return tickers


class CreateListForm(forms.ModelForm):
    class Meta:
        model  = StockList
        fields = ['name']
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': 'e.g. Swing, Tech, Finanzas',
                'maxlength': 50,
                'autocomplete': 'off',
            }),
        }

    def clean_name(self):
        return self.cleaned_data['name'].strip()


class AssignListForm(forms.Form):
    stock_list = forms.ModelChoiceField(
        queryset=StockList.objects.none(),
        required=False,
        empty_label='— General (no list) —',
        widget=forms.Select(attrs={'class': 'form-input'}),
    )

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['stock_list'].queryset = StockList.objects.filter(user=user)


class TradingProfileForm(forms.ModelForm):
    account_size = forms.FloatField(
        min_value=100,
        label='Account Size ($)',
        widget=forms.NumberInput(attrs={
            'class': 'form-input', 'placeholder': '10000', 'step': '100',
        }),
    )
    risk_per_trade_pct = forms.FloatField(
        min_value=0.1, max_value=10,
        label='Risk per Trade (%)',
        widget=forms.NumberInput(attrs={
            'class': 'form-input', 'placeholder': '1.0', 'step': '0.1',
        }),
    )

    send_analysis_email = forms.BooleanField(
        required=False,
        label='Send email report after Analyze All',
        widget=forms.CheckboxInput(attrs={'class': 'w-4 h-4 accent-green-500'}),
    )

    class Meta:
        model  = TradingProfile
        fields = ['account_size', 'risk_per_trade_pct', 'send_analysis_email']
