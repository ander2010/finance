from django import forms
from .models import StockList


class AddTickersForm(forms.Form):
    tickers = forms.CharField(
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': 'e.g. AAPL, TSLA, NVDA, AAL',
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
