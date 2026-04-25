from django.shortcuts import render, redirect
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.contrib import messages
from .forms import RegisterForm, ProfileForm
from stocks.models import TradingProfile
from stocks.forms import TradingProfileForm


def register_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    if request.method == 'POST':
        form = RegisterForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, f'Welcome to StockAnalyzer, {user.username}!')
            return redirect('dashboard')
        else:
            messages.error(request, 'Please fix the errors below.')
    else:
        form = RegisterForm()

    return render(request, 'users/register.html', {'form': form})


def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            next_url = request.GET.get('next', 'dashboard')
            return redirect(next_url)
        else:
            messages.error(request, 'Invalid username or password.')
    else:
        form = AuthenticationForm()

    return render(request, 'users/login.html', {'form': form})


def logout_view(request):
    logout(request)
    return redirect('login')


@login_required
def profile_view(request):
    trading_profile, _ = TradingProfile.objects.get_or_create(user=request.user)

    if request.method == 'POST':
        form         = ProfileForm(request.POST, instance=request.user)
        trading_form = TradingProfileForm(request.POST, instance=trading_profile)
        if form.is_valid() and trading_form.is_valid():
            form.save()
            trading_form.save()
            messages.success(request, 'Profile updated successfully.')
            return redirect('profile')
        else:
            messages.error(request, 'Please fix the errors below.')
    else:
        form         = ProfileForm(instance=request.user)
        trading_form = TradingProfileForm(instance=trading_profile)

    return render(request, 'users/profile.html', {
        'form':         form,
        'trading_form': trading_form,
        'profile':      trading_profile,
    })
