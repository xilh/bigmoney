from django.urls import path
from . import views

app_name = 'assets'

urlpatterns = [
    # 页面
    path('', views.dashboard, name='dashboard'),
    path('holdings/', views.holdings_page, name='holdings'),
    path('upload/', views.upload_page, name='upload'),
    path('rebalance/', views.rebalance_page, name='rebalance'),
    path('history/', views.history_page, name='history'),
    path('checklist/', views.checklist_page, name='checklist'),
    path('settings/', views.settings_page, name='settings'),
    path('advisor/', views.advisor_page, name='advisor'),
    path('advisor/history/', views.advisor_history, name='advisor_history'),
    path('advisor/history/<int:report_id>/', views.advisor_report_detail, name='advisor_report_detail'),

    # API
    path('api/holding/create/', views.api_holding_create, name='api_holding_create'),
    path('api/transaction/create/', views.api_transaction_create, name='api_transaction_create'),
    path('api/transaction/<int:tx_id>/update/', views.api_transaction_update, name='api_transaction_update'),
    path('api/transaction/<int:tx_id>/delete/', views.api_transaction_delete, name='api_transaction_delete'),
    path('api/holding/<int:holding_id>/update/', views.api_holding_update, name='api_holding_update'),
    path('api/holding/<int:holding_id>/delete/', views.api_holding_delete, name='api_holding_delete'),
    path('api/upload/', views.api_upload_screenshot, name='api_upload'),
    path('api/upload/confirm/', views.api_confirm_upload, name='api_confirm_upload'),
    path('api/snapshot/create/', views.api_snapshot_create, name='api_snapshot_create'),
    path('api/snapshot/<int:snapshot_id>/delete/', views.api_snapshot_delete, name='api_snapshot_delete'),
    path('api/snapshot/compare/', views.api_snapshot_compare, name='api_snapshot_compare'),
    path('api/checklist/complete/', views.api_checklist_complete, name='api_checklist_complete'),
    path('api/settings/save/', views.api_settings_save, name='api_settings_save'),
    path('api/export/', views.api_export_data, name='api_export'),
    path('api/import/', views.api_import_data, name='api_import'),
    path('api/performance/', views.api_performance_calc, name='api_performance_calc'),
    path('api/advisor/evaluate/', views.api_advisor_evaluate, name='api_advisor_evaluate'),
    path('api/test-llm/', views.api_test_llm, name='api_test_llm'),
]
