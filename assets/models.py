import json
from decimal import Decimal
from django.db import models
from django.utils import timezone


class AssetLayer(models.Model):
    """资产层级（五层配置）"""
    name = models.CharField('层级名称', max_length=50)
    description = models.TextField('描述', blank=True)
    target_ratio = models.DecimalField('目标比例(%)', max_digits=6, decimal_places=2, default=0)
    color = models.CharField('颜色代码', max_length=20, default='#3b82f6')
    order = models.IntegerField('排序', default=0)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)

    class Meta:
        ordering = ['order']
        verbose_name = '资产层级'
        verbose_name_plural = '资产层级'

    def __str__(self):
        return f'{self.name} ({self.target_ratio}%)'

    @property
    def total_market_value(self):
        return sum(h.market_value or Decimal('0') for h in self.holdings.all())


ASSET_TYPE_CHOICES = [
    ('cash', '现金'),
    ('money_fund', '货币基金'),
    ('bank_product', '银行理财'),
    ('deposit', '存款/大额存单'),
    ('bond_fund', '债券基金'),
    ('convertible_bond', '可转债基金'),
    ('index_fund', '指数基金'),
    ('stock', '股票'),
    ('etf', 'ETF'),
    ('dividend_stock', '红利股'),
    ('gold', '黄金'),
    ('qdii', 'QDII基金'),
    ('hk_stock', '港股'),
    ('other', '其他'),
]


class Holding(models.Model):
    """持仓记录"""
    layer = models.ForeignKey(
        AssetLayer, on_delete=models.CASCADE,
        related_name='holdings', verbose_name='所属层级'
    )
    name = models.CharField('名称', max_length=200)
    code = models.CharField('代码', max_length=20, blank=True)
    asset_type = models.CharField(
        '资产类型', max_length=30,
        choices=ASSET_TYPE_CHOICES, default='other'
    )
    quantity = models.DecimalField('数量/份额', max_digits=18, decimal_places=4, default=0)
    cost_price = models.DecimalField('成本价/买入均价', max_digits=14, decimal_places=4, null=True, blank=True)
    current_price = models.DecimalField('当前价/净值', max_digits=14, decimal_places=4, null=True, blank=True)
    market_value = models.DecimalField('市值(元)', max_digits=18, decimal_places=2, default=0)
    profit_loss = models.DecimalField('盈亏(元)', max_digits=18, decimal_places=2, default=0)
    profit_loss_pct = models.DecimalField('盈亏比例(%)', max_digits=10, decimal_places=4, default=0)
    source = models.CharField(
        '数据来源', max_length=20, default='manual',
        choices=[('manual', '手动输入'), ('screenshot', '截图识别')]
    )
    platform = models.CharField('来源平台', max_length=100, blank=True,
        help_text='如：招商银行、支付宝、天天基金、雪球等')
    notes = models.TextField('备注', blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        ordering = ['-market_value']
        verbose_name = '持仓'
        verbose_name_plural = '持仓'

    def __str__(self):
        return f'{self.name} ({self.code})' if self.code else self.name

    def save(self, *args, **kwargs):
        """自动计算盈亏"""
        if self.cost_price and self.current_price and self.quantity:
            cost_total = self.cost_price * self.quantity
            self.market_value = self.current_price * self.quantity
            self.profit_loss = self.market_value - cost_total
            self.profit_loss_pct = (self.profit_loss / cost_total * 100) if cost_total else Decimal('0')
        super().save(*args, **kwargs)


class Snapshot(models.Model):
    """资产快照（用于历史记录）"""
    date = models.DateTimeField('快照时间', default=timezone.now)
    total_value = models.DecimalField('总资产(元)', max_digits=18, decimal_places=2, default=0)
    layer_values = models.JSONField('各层级市值', default=dict)
    layer_ratios = models.JSONField('各层级比例', default=dict)
    notes = models.TextField('备注', blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)

    class Meta:
        ordering = ['-date', '-id']
        verbose_name = '资产快照'
        verbose_name_plural = '资产快照'

    def __str__(self):
        return f'{self.date} - ¥{self.total_value:,.0f}'


class Transaction(models.Model):
    """交易/操作记录"""
    ACTION_CHOICES = [
        ('buy', '买入'),
        ('sell', '卖出'),
        ('dividend', '分红'),
        ('rebalance', '再平衡'),
        ('transfer', '转入'),
        ('withdraw', '转出'),
    ]
    holding = models.ForeignKey(
        Holding, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='transactions', verbose_name='关联持仓'
    )
    action = models.CharField('操作类型', max_length=20, choices=ACTION_CHOICES)
    asset_name = models.CharField('资产名称', max_length=200)
    quantity = models.DecimalField('数量', max_digits=18, decimal_places=4, default=0)
    price = models.DecimalField('价格', max_digits=14, decimal_places=4, default=0)
    amount = models.DecimalField('金额(元)', max_digits=18, decimal_places=2, default=0)
    date = models.DateField('操作日期', default=timezone.now)
    notes = models.TextField('备注', blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)

    class Meta:
        ordering = ['-date', '-created_at']
        verbose_name = '交易记录'
        verbose_name_plural = '交易记录'

    def __str__(self):
        return f'{self.get_action_display()} {self.asset_name} ¥{self.amount:,.0f}'


class Upload(models.Model):
    """截图上传记录"""
    STATUS_CHOICES = [
        ('pending', '待识别'),
        ('processing', '识别中'),
        ('recognized', '已识别'),
        ('confirmed', '已确认'),
        ('failed', '识别失败'),
    ]
    image = models.ImageField('截图', upload_to='uploads/%Y/%m/')
    platform = models.CharField('来源平台', max_length=100, blank=True,
        help_text='截图所属的 App/平台')
    recognized_data = models.JSONField('识别结果', default=list, blank=True)
    status = models.CharField(
        '状态', max_length=20,
        choices=STATUS_CHOICES, default='pending'
    )
    error_message = models.TextField('错误信息', blank=True)
    created_at = models.DateTimeField('上传时间', auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = '截图上传'
        verbose_name_plural = '截图上传'

    def __str__(self):
        return f'上传 #{self.pk} - {self.get_status_display()}'


class ChecklistRecord(models.Model):
    """检视清单记录"""
    PERIOD_CHOICES = [
        ('weekly', '周检视'),
        ('monthly', '月检视'),
        ('quarterly', '季度检视'),
        ('yearly', '年度检视'),
    ]
    period_type = models.CharField('检视类型', max_length=20, choices=PERIOD_CHOICES)
    completed_items = models.JSONField('已完成项', default=list)
    total_items = models.IntegerField('总项数', default=0)
    completed_at = models.DateTimeField('完成时间', default=timezone.now)
    notes = models.TextField('备注', blank=True)

    class Meta:
        ordering = ['-completed_at']
        verbose_name = '检视记录'
        verbose_name_plural = '检视记录'

    def __str__(self):
        return f'{self.get_period_type_display()} - {self.completed_at.strftime("%Y-%m-%d")}'


class Setting(models.Model):
    """全局设置"""
    key = models.CharField('键', max_length=100, unique=True)
    value = models.TextField('值', blank=True)

    class Meta:
        verbose_name = '设置'
        verbose_name_plural = '设置'

    def __str__(self):
        return self.key

    @classmethod
    def get(cls, key, default=''):
        try:
            return cls.objects.get(key=key).value
        except cls.DoesNotExist:
            return default

    @classmethod
    def set(cls, key, value):
        obj, _ = cls.objects.update_or_create(key=key, defaults={'value': value})
        return obj


class EvaluationReport(models.Model):
    """AI顾问评估报告"""
    date = models.DateTimeField('评估时间', default=timezone.now)
    total_value = models.DecimalField('总资产(元)', max_digits=18, decimal_places=2, default=0)
    score = models.IntegerField('健康评分', default=0)
    overall_health = models.CharField('整体健康度', max_length=50)
    summary_data = models.JSONField('总结数据', default=dict)
    holdings_data = models.JSONField('持仓评估', default=list)

    class Meta:
        ordering = ['-date', '-id']
        verbose_name = '评估报告'
        verbose_name_plural = '评估报告'

    def __str__(self):
        return f'{self.date.strftime("%Y-%m-%d %H:%M")} - 分数: {self.score}'
