import json
import logging
from decimal import Decimal, ROUND_HALF_UP
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

logger = logging.getLogger(__name__)


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
        from django.db.models import Sum
        result = self.holdings.aggregate(total=Sum('market_value'))
        return result['total'] or Decimal('0')


# 层内子类别（v3.4 §2.2/§2.4）。留空=按 asset_type 自动归类；显式设置可消除
# asset_type 的歧义（如 dividend_stock 既可能是「红利ETF」也可能是「红利个股」）。
SUB_CATEGORY_CHOICES = [
    ('', '自动(按类型)'),
    ('broad', '宽基指数'),       # T3
    ('dividend', '红利/价值锚'),  # T3
    ('pick', '精选个股'),        # T3（计入单行业≤50%红线）
    ('gold', '黄金'),            # T4
    ('hk', '港股通'),            # T5
    ('qdii', 'QDII'),           # T5
    ('theme', '主题'),           # T5
]


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
        AssetLayer, on_delete=models.PROTECT,
        related_name='holdings', verbose_name='所属层级'
    )
    name = models.CharField('名称', max_length=200)
    code = models.CharField('代码', max_length=20, blank=True)
    asset_type = models.CharField(
        '资产类型', max_length=30,
        choices=ASSET_TYPE_CHOICES, default='other'
    )
    sub_category = models.CharField(
        '层内子类别', max_length=20, blank=True, default='',
        choices=SUB_CATEGORY_CHOICES,
        help_text='留空则按资产类型自动归类；显式设置用于消歧（如区分红利ETF与红利个股）'
    )
    quantity = models.DecimalField('数量/份额', max_digits=18, decimal_places=4, default=0)
    cost_price = models.DecimalField('成本价/买入均价', max_digits=14, decimal_places=4, null=True, blank=True)
    current_price = models.DecimalField('当前价/净值', max_digits=14, decimal_places=4, null=True, blank=True)
    market_value = models.DecimalField('市值(元)', max_digits=18, decimal_places=2, default=0, db_index=True)
    profit_loss = models.DecimalField('盈亏(元)', max_digits=18, decimal_places=2, default=0)
    profit_loss_pct = models.DecimalField('盈亏比例(%)', max_digits=16, decimal_places=4, default=0)
    source = models.CharField(
        '数据来源', max_length=20, default='manual',
        choices=[('manual', '手动输入'), ('screenshot', '截图识别')]
    )
    platform = models.CharField('来源平台', max_length=100, blank=True,
        help_text='如：招商银行、支付宝、天天基金、雪球等')
    is_reserve = models.BooleanField('干火药储备', default=False,
        help_text='标记为应急储备/干火药，不参与常规再平衡部署')
    industry = models.CharField('行业/板块', max_length=50, blank=True, db_index=True,
        help_text='如：白酒、银行、科技、医药、电力等。用于行业集中度分析与「个人财富—企业风险脱钩」检视')
    buy_thesis = models.TextField('买入逻辑', blank=True,
        help_text='为什么买这只资产（护城河/现金流/估值等）。卖出时用于校验「投资逻辑是否破坏」')
    notes = models.TextField('备注', blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        ordering = ['-market_value']
        verbose_name = '持仓'
        verbose_name_plural = '持仓'

    def __str__(self):
        return f'{self.name} ({self.code})' if self.code else self.name

    def clean(self):
        if self.cost_price is not None and self.cost_price < 0:
            raise ValidationError({'cost_price': '成本价不能为负数'})
        if self.current_price is not None and self.current_price < 0:
            raise ValidationError({'current_price': '当前价不能为负数'})
        if self.quantity is not None and self.quantity < 0:
            raise ValidationError({'quantity': '数量不能为负数'})

    def save(self, *args, **kwargs):
        """自动计算盈亏，必要时从市值和盈亏反推成本"""
        if self.cost_price and self.current_price and self.quantity:
            # 有完整价格信息，精确计算
            cost_total = self.cost_price * self.quantity
            self.market_value = (self.current_price * self.quantity).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            self.profit_loss = (self.market_value - cost_total).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            if cost_total > 0:
                raw_pct = (self.profit_loss / cost_total * 100).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)
                # Clamp to fit DecimalField(max_digits=16, decimal_places=4)
                _MAX_PCT = Decimal('999999999999.9999')
                self.profit_loss_pct = max(-_MAX_PCT, min(_MAX_PCT, raw_pct))
            else:
                self.profit_loss_pct = Decimal('0')
        elif not self.cost_price and self.market_value and self.profit_loss:
            # 无成本价但有市值和非零盈亏，反推成本并计算收益率
            # 注意：此为估算路径，非真实成本。专业理财场景下应优先以截图/手工录入的真实成本为准。
            cost_total = self.market_value - self.profit_loss
            if cost_total > 0:
                raw_pct = (self.profit_loss / cost_total * 100).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)
                _MAX_PCT = Decimal('999999999999.9999')
                self.profit_loss_pct = max(-_MAX_PCT, min(_MAX_PCT, raw_pct))
                if self.quantity and self.quantity > 0:
                    self.cost_price = (cost_total / self.quantity).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)
                    if self.market_value and self.quantity:
                        self.current_price = (self.market_value / self.quantity).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)
                    logger.warning(
                        "Holding '%s' cost_price reverse-derived from market_value/profit_loss "
                        "(cost=%.4f, pct=%.4f). 仅作估算，建议补录真实成本。",
                        self.name, self.cost_price, self.profit_loss_pct,
                    )
        super().save(*args, **kwargs)


class Snapshot(models.Model):
    """资产快照（用于历史记录）"""
    date = models.DateTimeField('快照时间', default=timezone.now, db_index=True)
    total_value = models.DecimalField('总资产(元)', max_digits=18, decimal_places=2, default=0)
    layer_values = models.JSONField('各层级市值', default=dict)
    layer_ratios = models.JSONField('各层级比例', default=dict)
    holdings_data = models.JSONField('持仓明细', default=list, blank=True)
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
    SOURCE_CHOICES = [
        ('manual', '手动'),
        ('auto', '自动生成'),
        ('ocr', 'OCR识别'),
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
    date = models.DateField('操作日期', default=timezone.now, db_index=True)
    source = models.CharField('来源', max_length=20, default='manual', choices=SOURCE_CHOICES)
    realized_pnl = models.DecimalField('已实现盈亏', max_digits=18, decimal_places=2,
        null=True, blank=True, help_text='卖出时的已实现盈亏')
    platform = models.CharField('平台', max_length=100, blank=True)
    notes = models.TextField('备注', blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True, null=True, blank=True)

    class Meta:
        ordering = ['-date', '-created_at']
        verbose_name = '交易记录'
        verbose_name_plural = '交易记录'

    def clean(self):
        if self.date and self.date > timezone.localdate():
            raise ValidationError({'date': '操作日期不能是未来日期'})

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


class AssetEvaluation(models.Model):
    """单资产 AI 深度评估"""
    date = models.DateTimeField('评估时间', default=timezone.now)
    asset_name = models.CharField('资产名称', max_length=200, db_index=True)
    score = models.IntegerField('健康评分', default=0)
    signal = models.CharField('操作信号', max_length=20)
    signal_reason = models.CharField('信号理由', max_length=500)
    risk_level = models.CharField('风险等级', max_length=20)
    analysis_data = models.JSONField('分析详情', default=dict)
    action_plan = models.TextField('操作建议', blank=True, default='')
    risks = models.JSONField('风险点', default=list)
    highlights = models.JSONField('亮点', default=list)

    class Meta:
        ordering = ['-date', '-id']
        verbose_name = '资产评估'
        verbose_name_plural = '资产评估'

    def __str__(self):
        return f'{self.asset_name} - {self.date.strftime("%Y-%m-%d %H:%M")} - {self.signal}'


class AlertAction(models.Model):
    """风控预警处置记录"""
    ACTION_CHOICES = [
        ('acknowledged', '已知悉'),
        ('acted', '已执行操作'),
        ('dismissed', '暂不处理'),
    ]
    holding = models.ForeignKey(
        Holding, on_delete=models.CASCADE,
        related_name='alert_actions', verbose_name='关联持仓'
    )
    alert_type = models.CharField('预警类型', max_length=50,
        help_text='如：concentration_5pct, satellite_stop_loss_30, satellite_take_profit_50')
    action = models.CharField('处置方式', max_length=20, choices=ACTION_CHOICES)
    notes = models.TextField('处置备注', blank=True)
    created_at = models.DateTimeField('处置时间', auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = '预警处置'
        verbose_name_plural = '预警处置'

    def __str__(self):
        return f'{self.holding.name} - {self.alert_type} - {self.get_action_display()}'

class SystemBackup(models.Model):
    """系统数据备份（用于恢复持仓状态）"""
    name = models.CharField('备份名称', max_length=100)
    data = models.JSONField('备份数据')
    is_auto = models.BooleanField('是否自动备份', default=False)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = '系统备份'
        verbose_name_plural = '系统备份'

    def __str__(self):
        return f'{self.name} - {self.created_at.strftime("%Y-%m-%d %H:%M")}'

