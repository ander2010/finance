from django.urls import path
from . import views_paper

urlpatterns = [
    path('',                              views_paper.paper_portfolio,   name='paper_portfolio'),
    path('setup/',                        views_paper.paper_setup,       name='paper_setup'),
    path('add-funds/',                    views_paper.paper_add_funds,   name='paper_add_funds'),
    path('execute/',                      views_paper.paper_execute,     name='paper_execute'),
    path('close/<int:trade_id>/',         views_paper.paper_close,       name='paper_close'),
    path('price/<str:symbol>/',           views_paper.paper_ticker_price,name='paper_ticker_price'),
]
