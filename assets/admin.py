from django.contrib import admin
from .models import AssetLayer, Holding, Snapshot, Transaction, Upload, ChecklistRecord, Setting

@admin.register(AssetLayer)
class AssetLayerAdmin(admin.ModelAdmin):
    list_display = ['name', 'target_ratio', 'order']
    ordering = ['order']

@admin.register(Holding)
class HoldingAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'asset_type', 'layer', 'market_value', 'profit_loss_pct', 'updated_at']
    list_filter = ['layer', 'asset_type', 'source']
    search_fields = ['name', 'code']

@admin.register(Snapshot)
class SnapshotAdmin(admin.ModelAdmin):
    list_display = ['date', 'total_value']

@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ['date', 'action', 'asset_name', 'amount']
    list_filter = ['action']

@admin.register(Upload)
class UploadAdmin(admin.ModelAdmin):
    list_display = ['id', 'status', 'created_at']
    list_filter = ['status']

@admin.register(ChecklistRecord)
class ChecklistRecordAdmin(admin.ModelAdmin):
    list_display = ['period_type', 'completed_at']
    list_filter = ['period_type']

@admin.register(Setting)
class SettingAdmin(admin.ModelAdmin):
    list_display = ['key', 'value']
