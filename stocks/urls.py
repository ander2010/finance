from django.urls import path
from . import views

urlpatterns = [
    path('dashboard/',                     views.dashboard,      name='dashboard'),
    path('add/',                           views.add_ticker,     name='add_ticker'),
    path('remove/<int:entry_id>/',         views.remove_ticker,  name='remove_ticker'),
    path('analyze/<int:entry_id>/',        views.analyze_ticker, name='analyze_ticker'),
    path('analyze-all/',                   views.analyze_all,    name='analyze_all'),
    path('trades/',                        views.trade_plans,    name='trade_plans'),
    path('performance/',                   views.performance,    name='performance'),
    path('detail/<int:entry_id>/',         views.stock_detail,   name='stock_detail'),
    # AJAX
    path('company-info/<str:symbol>/',     views.company_info,      name='company_info'),
    path('smart-money/<str:symbol>/',      views.smart_money,       name='smart_money'),
    path('accumulation-scan/',             views.accumulation_scan, name='accumulation_scan'),
    # Lists
    path('lists/create/',                  views.create_list,    name='create_list'),
    path('lists/delete/<int:list_id>/',    views.delete_list,    name='delete_list'),
    path('lists/assign/<int:entry_id>/',   views.assign_to_list, name='assign_to_list'),
]
