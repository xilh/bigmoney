from django.contrib.auth.decorators import login_required
from django.urls import path
from . import views

app_name = 'assets'


def _protected(view_func):
    """Wrap view with login_required. AUTH_REQUIRED=False in settings disables this."""
    from django.conf import settings
    if getattr(settings, 'AUTH_REQUIRED', True):
        return login_required(view_func)
    return view_func


urlpatterns = [
    # 页面
    path('', _protected(views.dashboard), name='dashboard'),
    path('holdings/', _protected(views.holdings_page), name='holdings'),
    path('upload/', _protected(views.upload_page), name='upload'),
    path('rebalance/', _protected(views.rebalance_page), name='rebalance'),
    path('history/', _protected(views.history_page), name='history'),
    path('cashflow/', _protected(views.cashflow_page), name='cashflow'),
    path('checklist/', _protected(views.checklist_page), name='checklist'),
    path('settings/', _protected(views.settings_page), name='settings'),
    path('advisor/', _protected(views.advisor_page), name='advisor'),
    path('advisor/history/', _protected(views.advisor_history), name='advisor_history'),
    path('advisor/history/<int:report_id>/', _protected(views.advisor_report_detail), name='advisor_report_detail'),

    # API
    path('api/holding/create/', _protected(views.api_holding_create), name='api_holding_create'),
    path('api/transaction/create/', _protected(views.api_transaction_create), name='api_transaction_create'),
    path('api/transaction/<int:tx_id>/update/', _protected(views.api_transaction_update), name='api_transaction_update'),
    path('api/transaction/<int:tx_id>/delete/', _protected(views.api_transaction_delete), name='api_transaction_delete'),
    path('api/holding/<int:holding_id>/update/', _protected(views.api_holding_update), name='api_holding_update'),
    path('api/holding/<int:holding_id>/delete/', _protected(views.api_holding_delete), name='api_holding_delete'),
    path('api/upload/', _protected(views.api_upload_screenshot), name='api_upload'),
    path('api/upload/confirm/', _protected(views.api_confirm_upload), name='api_confirm_upload'),
    path('api/snapshot/create/', _protected(views.api_snapshot_create), name='api_snapshot_create'),
    path('api/snapshot/<int:snapshot_id>/delete/', _protected(views.api_snapshot_delete), name='api_snapshot_delete'),
    path('api/snapshot/compare/', _protected(views.api_snapshot_compare), name='api_snapshot_compare'),
    path('api/checklist/complete/', _protected(views.api_checklist_complete), name='api_checklist_complete'),
    path('api/settings/save/', _protected(views.api_settings_save), name='api_settings_save'),
    path('api/export/', _protected(views.api_export_data), name='api_export'),
    path('api/import/', _protected(views.api_import_data), name='api_import'),
    path('api/performance/', _protected(views.api_performance_calc), name='api_performance_calc'),
    path('api/advisor/evaluate/', _protected(views.api_advisor_evaluate), name='api_advisor_evaluate'),
    path('api/test-llm/', _protected(views.api_test_llm), name='api_test_llm'),
    path('api/cashflow/confirm/', _protected(views.api_cashflow_confirm), name='api_cashflow_confirm'),
]
