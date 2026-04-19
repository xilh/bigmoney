import json
import os
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

import logging

from django.conf import settings as django_settings
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_http_methods

logger = logging.getLogger(__name__)

from .models import (
    AssetLayer, Holding, Snapshot, Transaction,
    Upload, ChecklistRecord, Setting, ASSET_TYPE_CHOICES,
    EvaluationReport, AssetEvaluation,
)
from .services.ocr import recognize_screenshot
from .services.rebalance import (
    calculate_rebalance, DRAWDOWN_PROTOCOLS, generate_investment_plan
)
from .services.advisor import evaluate_portfolio, evaluate_asset
from .services.ledger import snapshot_holding, record_holding_change, record_holding_removal


class DecimalEncoder(json.JSONEncoder):
    """JSON 编码器：将 Decimal 转为 float 以保持前端兼容"""
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)

def _get_dismissed_deviations(cutoff):
    """获取 cutoff 之后被忽略的层级偏差名称集合"""
    raw = Setting.get('dismissed_deviation_alerts', '{}')
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return set()
    cutoff_iso = cutoff.isoformat()
    return {name for name, ts in data.items() if ts >= cutoff_iso}


def _get_layers_summary():
    """获取各层级汇总数据"""
    layers = AssetLayer.objects.all()
    total_value = Decimal('0')
    layers_data = []

    for layer in layers:
        value = layer.total_market_value
        total_value += value
        layers_data.append({
            'id': layer.id,
            'name': layer.name,
            'description': layer.description,
            'target_ratio': layer.target_ratio,
            'actual_value': value,
            'color': layer.color,
            'order': layer.order,
            'holdings_count': layer.holdings.count(),
        })

    # 计算实际比例
    for ld in layers_data:
        if total_value > 0:
            ld['actual_ratio'] = float((ld['actual_value'] / total_value * 100).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
        else:
            ld['actual_ratio'] = 0.0
        ld['target_ratio'] = float(ld['target_ratio'])
        ld['actual_value'] = float(ld['actual_value'])
        ld['deviation'] = round(ld['actual_ratio'] - ld['target_ratio'], 2)

    return layers_data, float(total_value)


# ==================== 页面视图 ====================
from django.db.models import Sum

def dashboard(request):
    """仪表盘"""
    layers_data, total_value = _get_layers_summary()
    recent_transactions = Transaction.objects.all()[:5]

    # 一次查询所有持仓，后续复用
    holdings = list(Holding.objects.select_related('layer').all())

    # 计算总盈亏
    total_profit = sum((h.profit_loss or Decimal('0')) for h in holdings)
    total_cost = sum(
        (h.cost_price or Decimal('0')) * h.quantity for h in holdings
        if h.cost_price and h.quantity
    )
    total_profit_pct = float(total_profit / total_cost * 100) if total_cost > 0 else 0

    from .services.rebalance import calculate_risk_alerts
    from .models import AlertAction

    # 7 天内已处置的预警 key（holding_id:alert_type）
    dismiss_cutoff = timezone.now() - timezone.timedelta(days=7)
    acked_keys = set(
        AlertAction.objects.filter(created_at__gte=dismiss_cutoff)
        .values_list('holding_id', 'alert_type')
    )
    acked_keys = {f"{hid}:{atype}" for hid, atype in acked_keys}

    # 层级偏差 7 天内已忽略的
    dismissed_deviations = _get_dismissed_deviations(dismiss_cutoff)

    # 偏差警告（过滤已忽略的）
    deviation_alerts = [
        ld for ld in layers_data
        if abs(ld['deviation']) > 5 and ld['name'] not in dismissed_deviations
    ]

    # 风险预警（过滤已处置的）
    risk_alerts = calculate_risk_alerts(holdings, total_value, acknowledged_keys=acked_keys)

    # 平台分布
    platform_distribution = list(Holding.objects.values('platform')
        .annotate(total=Sum('market_value'))
        .order_by('-total'))
    
    platforms_data = []
    for item in platform_distribution:
        platform_name = item['platform'].strip() if item['platform'] else '其他/未知'
        platforms_data.append({
            'name': platform_name,
            'value': float(item['total'] or 0)
        })

    # 本月资金流入/流出（从交易记录推算）
    month_start = date.today().replace(day=1)
    month_transfers = Transaction.objects.filter(
        action='transfer', date__gte=month_start,
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
    month_withdrawals = Transaction.objects.filter(
        action='withdraw', date__gte=month_start,
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
    month_net_flow = month_transfers - month_withdrawals

    context = {
        'total_value': total_value,
        'total_profit': total_profit,
        'total_profit_pct': total_profit_pct,
        'layers_data': layers_data,
        'layers_json': json.dumps(layers_data, cls=DecimalEncoder, ensure_ascii=False),
        'platforms_json': json.dumps(platforms_data, ensure_ascii=False),
        'recent_transactions': recent_transactions,
        'deviation_alerts': deviation_alerts,
        'risk_alerts': risk_alerts,
        'holdings_count': Holding.objects.count(),
        'month_net_flow': month_net_flow,
    }
    return render(request, 'assets/dashboard.html', context)


def holdings_page(request):
    """资产管理页面"""
    layers = AssetLayer.objects.prefetch_related('holdings').all()
    layers_data, total_value = _get_layers_summary()

    context = {
        'layers': layers,
        'total_value': total_value,
        'layers_data': layers_data,
        'asset_type_choices': ASSET_TYPE_CHOICES,
    }
    return render(request, 'assets/holdings.html', context)


def upload_page(request):
    """截图上传页面"""
    uploads = Upload.objects.all()[:20]
    provider = Setting.get('llm_provider', 'openai_compatible')
    api_key = Setting.get('llm_api_key', '')
    api_url = Setting.get('llm_api_url', '')
    if provider == 'anthropic':
        has_api_config = bool(api_key) or bool(api_url)
    else:
        has_api_config = bool(api_url)
    layers = AssetLayer.objects.all()

    existing_platforms = list(
        Holding.objects.exclude(platform='')
        .values_list('platform', flat=True)
        .distinct()
        .order_by('platform')
    )

    context = {
        'uploads': uploads,
        'has_api_key': has_api_config,
        'layers': layers,
        'asset_type_choices': ASSET_TYPE_CHOICES,
        'existing_platforms': existing_platforms,
    }
    return render(request, 'assets/upload.html', context)


def rebalance_page(request):
    """再平衡页面"""
    layers_data, total_value = _get_layers_summary()
    rebalance_result = calculate_rebalance(layers_data, total_value)

    from .services.rebalance import calculate_risk_alerts
    holdings = list(Holding.objects.select_related('layer').all())
    # 往再平衡的分析里追加持股风控预警
    risk_alerts = calculate_risk_alerts(holdings, total_value)
    # prepend risk alerts so they are highly visible
    rebalance_result['alerts'] = risk_alerts + rebalance_result.get('alerts', [])

    # 生成具体投资执行计划
    investment_plan = generate_investment_plan(layers_data, holdings, total_value, rebalance_result)

    context = {
        'total_value': total_value,
        'rebalance': rebalance_result,
        'rebalance_json': json.dumps(rebalance_result, cls=DecimalEncoder, ensure_ascii=False),
        'layers_data': layers_data,
        'layers_json': json.dumps(layers_data, cls=DecimalEncoder, ensure_ascii=False),
        'drawdown_protocols': DRAWDOWN_PROTOCOLS,
        'investment_plan': investment_plan,
    }
    return render(request, 'assets/rebalance.html', context)


def history_page(request):
    """历史记录页面"""
    snapshots = Snapshot.objects.all()[:60]
    transactions = Transaction.objects.all()[:50]
    layers = AssetLayer.objects.all()

    snapshots_json = json.dumps([
        {
            'date': timezone.localtime(s.date).strftime('%Y-%m-%d %H:%M:%S'),
            'total_value': s.total_value,
            'layer_values': s.layer_values,
            'layer_ratios': s.layer_ratios,
        }
        for s in reversed(list(snapshots))
    ], cls=DecimalEncoder, ensure_ascii=False)

    target_ratios_json = json.dumps({
        l.name: float(l.target_ratio) for l in layers
    }, ensure_ascii=False)

    context = {
        'snapshots': snapshots,
        'transactions': transactions,
        'snapshots_json': snapshots_json,
        'target_ratios_json': target_ratios_json,
    }
    return render(request, 'assets/history.html', context)


def checklist_page(request):
    """检视清单页面"""
    # 预设清单项
    checklists = {
        'weekly': {
            'name': '周检视',
            'period': '每周日晚间 · 约10分钟',
            'items': [
                {'text': '查看各账户总体净值变化', 'auto_id': None},
                {'text': '确认本周定投扣款是否成功执行（DCA阶段）', 'auto_id': None},
                {'text': '浏览宏观新闻标题，判断是否有重大事件', 'auto_id': None},
                {'text': '提醒：周检视不做任何买卖操作', 'auto_id': None},
            ],
        },
        'monthly': {
            'name': '月检视',
            'period': '每月第一个周末 · 约30分钟',
            'items': [
                {'text': '记录各层级实际比例 vs. 目标比例', 'auto_id': 'deviation_check'},
                {'text': '检查货币基金收益率是否异常偏低', 'auto_id': 'money_fund_check'},
                {'text': '确认债券基金是否有异常波动（单月跌幅>1%即为异常）', 'auto_id': 'bond_fund_check'},
                {'text': '审视单只个股仓位是否因涨跌突破5%红线', 'auto_id': 'concentration_check'},
                {'text': '如存在单笔个股超过5%，在本月内分批减仓至目标比例', 'auto_id': None},
            ],
        },
        'quarterly': {
            'name': '季度检视',
            'period': '季末最后一周 · 约1-2小时',
            'items': [
                {'text': '全面审视五层配置比例，判断是否需要小幅再平衡', 'auto_id': 'deviation_check'},
                {'text': '检查各基金产品同类排名（连续两季后25%应考虑替换）', 'auto_id': None},
                {'text': '审视行业暴露：检查股票持仓是否在某一行业过度集中', 'auto_id': None},
                {'text': '审视第五层卫星仓位的每笔投资投资逻辑是否仍然成立', 'auto_id': 'satellite_check'},
                {'text': '记录本季度总回报和各层级回报，建立历史记录', 'auto_id': None},
            ],
        },
        'yearly': {
            'name': '年度大检',
            'period': '12月或次年1月初 · 约2-3小时',
            'items': [
                {'text': '各层级实际比例 vs. 目标比例，执行强制再平衡', 'auto_id': 'deviation_check'},
                {'text': '单只个股是否有超过5%的情况', 'auto_id': 'concentration_check'},
                {'text': '基金产品同类排名审视，替换连续落后的基金', 'auto_id': None},
                {'text': '保险保障是否充足，受益人是否正确', 'auto_id': None},
                {'text': '税务优化：股息持有期、个税扣除项是否充分利用', 'auto_id': None},
                {'text': '第五层卫星仓位每笔投资逻辑重新评估', 'auto_id': 'satellite_check'},
                {'text': '风险偏好是否需要调整（家庭、事业、健康变化）', 'auto_id': None},
                {'text': '下一年度目标配置比例是否需要微调（年龄因素）', 'auto_id': None},
                {'text': '遗嘱、家族信托、子女教育基金进展审视', 'auto_id': None},
                {'text': '记录本年度总回报、各层级回报、重大决策日志', 'auto_id': None},
            ],
        },
    }

    # 执行自动化检查
    layers_data, total_value = _get_layers_summary()
    holdings = Holding.objects.select_related('layer').all()
    from .services.rebalance import calculate_risk_alerts
    risk_alerts = calculate_risk_alerts(holdings, total_value)
    
    # 集中度检查
    concentration_alerts = [a for a in risk_alerts if '单票集中度过高' in a['message']]
    # 卫星仓位检查
    satellite_alerts = [a for a in risk_alerts if '单票集中度过高' not in a['message']]
    # 偏差检查
    deviation_alerts = [ld for ld in layers_data if abs(ld['deviation']) > 5]
    # 债基检查
    bond_alerts = [h for h in holdings if h.asset_type == 'bond_fund' and (h.profit_loss_pct or 0) < -1.0]

    automated_results = {
        'deviation_check': {
            'pass': len(deviation_alerts) == 0,
            'message': '所有层级均在5%偏差范围内' if len(deviation_alerts) == 0 else f'发现 {len(deviation_alerts)} 个层级偏差超标'
        },
        'concentration_check': {
            'pass': len(concentration_alerts) == 0,
            'message': '无单票违反5%集中度红线' if len(concentration_alerts) == 0 else f'发现 {len(concentration_alerts)} 只票突破5%红线'
        },
        'satellite_check': {
            'pass': len(satellite_alerts) == 0,
            'message': '卫星仓位暂无止盈止损触发' if len(satellite_alerts) == 0 else f'发现 {len(satellite_alerts)} 个卫星仓位触发预警'
        },
        'bond_fund_check': {
            'pass': len(bond_alerts) == 0,
            'message': '未发现明显亏损债基' if len(bond_alerts) == 0 else f'发现 {len(bond_alerts)} 只债基亏损超过1% (仅参考总收益)'
        },
        'money_fund_check': {
            'pass': True,
            'message': '需登录相关平台确认最新七日年化'
        }
    }


    # 获取各类型最近一次完成记录
    last_records = {}
    for period_type in checklists.keys():
        record = ChecklistRecord.objects.filter(
            period_type=period_type
        ).first()
        last_records[period_type] = record

    context = {
        'checklists': checklists,
        'last_records': last_records,
        'automated_results': automated_results,
    }
    return render(request, 'assets/checklist.html', context)


def settings_page(request):
    """设置页面"""
    layers = AssetLayer.objects.all()

    def mask_key(key):
        if not key:
            return ''
        return key[:8] + '...' + key[-4:] if len(key) > 12 else '***'

    # OCR config (unified keys, fallback to old keys for migration)
    llm_mode = Setting.get('llm_mode', 'cloud')
    provider = Setting.get('llm_provider', '') or Setting.get('llm_provider', 'openai_compatible')
    api_url = Setting.get('llm_api_url', '') or Setting.get('local_api_url', '') or Setting.get('anthropic_base_url', '')
    api_key = Setting.get('llm_api_key', '') or Setting.get('anthropic_api_key', '') or Setting.get('local_api_key', '')
    model = Setting.get('llm_model', '') or Setting.get('anthropic_model', '') or Setting.get('local_model', '')
    llm_max_tokens = Setting.get('llm_max_tokens', '2048')
    llm_cloud_provider = Setting.get('llm_cloud_provider', '')

    # Advisor config (unified keys, fallback to old keys)
    advisor_mode = Setting.get('advisor_mode', 'cloud')
    advisor_provider = Setting.get('advisor_llm_provider', 'openai_compatible')
    advisor_api_url = Setting.get('advisor_api_url', '') or Setting.get('advisor_anthropic_base_url', '') or Setting.get('advisor_local_api_url', '')
    advisor_api_key = Setting.get('advisor_api_key', '') or Setting.get('advisor_anthropic_api_key', '') or Setting.get('advisor_local_api_key', '')
    advisor_model = Setting.get('advisor_model', '') or Setting.get('advisor_anthropic_model', '') or Setting.get('advisor_local_model', '')
    advisor_cloud_provider = Setting.get('advisor_cloud_provider', '')
    advisor_max_tokens = Setting.get('advisor_max_tokens', '8192')

    context = {
        'layers': layers,
        'llm_mode': llm_mode,
        'llm_provider': provider,
        'llm_api_url': api_url,
        'has_api_key': bool(api_key),
        'masked_api_key': mask_key(api_key),
        'llm_model': model,
        'llm_max_tokens': llm_max_tokens,
        'llm_cloud_provider': llm_cloud_provider,
        # Advisor
        'advisor_mode': advisor_mode,
        'advisor_provider': advisor_provider,
        'advisor_api_url': advisor_api_url,
        'has_advisor_key': bool(advisor_api_key),
        'masked_advisor_key': mask_key(advisor_api_key),
        'advisor_model': advisor_model,
        'advisor_cloud_provider': advisor_cloud_provider,
        'advisor_max_tokens': advisor_max_tokens,
    }
    return render(request, 'assets/settings.html', context)


# ==================== API 端点 ====================

@require_POST
def api_holding_create(request):
    """创建持仓"""
    try:
        data = json.loads(request.body)
        layer = get_object_or_404(AssetLayer, id=data['layer_id'])

        quantity = Decimal(str(data.get('quantity', 0)))
        cost_price = Decimal(str(data['cost_price'])) if data.get('cost_price') else None
        current_price = Decimal(str(data['current_price'])) if data.get('current_price') else None
        market_value = Decimal(str(data.get('market_value', 0)))

        # Validate non-negative
        if quantity < 0 or market_value < 0:
            return JsonResponse({'success': False, 'error': '数量和市值不能为负数'}, status=400)
        if cost_price is not None and cost_price < 0:
            return JsonResponse({'success': False, 'error': '成本价不能为负数'}, status=400)

        holding = Holding(
            layer=layer,
            name=data['name'],
            code=data.get('code', ''),
            asset_type=data.get('asset_type', 'other'),
            quantity=quantity,
            cost_price=cost_price,
            current_price=current_price,
            market_value=market_value,
            source=data.get('source', 'manual'),
            platform=data.get('platform', ''),
            notes=data.get('notes', ''),
        )
        # save() will auto-calculate P&L if price info exists;
        # if no price info, the directly-provided market_value is preserved.
        holding.save()

        record_holding_change(holding, old_data=None, source='manual')

        return JsonResponse({'success': True, 'id': holding.id})
    except (ValueError, KeyError) as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)
    except Exception as e:
        logger.exception("api_holding_create failed")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@require_http_methods(["PUT"])
def api_holding_update(request, holding_id):
    """更新持仓"""
    try:
        holding = get_object_or_404(Holding, id=holding_id)
        data = json.loads(request.body)

        old_data = snapshot_holding(holding)

        if 'layer_id' in data:
            holding.layer = get_object_or_404(AssetLayer, id=data['layer_id'])
        if 'name' in data:
            holding.name = data['name']
        if 'code' in data:
            holding.code = data['code']
        if 'asset_type' in data:
            holding.asset_type = data['asset_type']
        if 'quantity' in data:
            holding.quantity = Decimal(str(data['quantity'])) if data['quantity'] else Decimal('0')
        if 'cost_price' in data:
            holding.cost_price = Decimal(str(data['cost_price'])) if data['cost_price'] else None
        if 'current_price' in data:
            holding.current_price = Decimal(str(data['current_price'])) if data['current_price'] else None
        if 'market_value' in data:
            holding.market_value = Decimal(str(data['market_value'])) if data['market_value'] else Decimal('0')
        if 'notes' in data:
            holding.notes = data['notes']
        if 'platform' in data:
            holding.platform = data['platform']

        holding.save()

        record_holding_change(holding, old_data, source='manual')

        return JsonResponse({'success': True})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


@require_http_methods(["DELETE"])
def api_holding_delete(request, holding_id):
    """删除持仓"""
    try:
        holding = get_object_or_404(Holding, id=holding_id)
        record_holding_removal(holding, source='manual')
        holding.delete()
        return JsonResponse({'success': True})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


@require_POST
def api_transaction_create(request):
    """记录一笔资金出入"""
    try:
        data = json.loads(request.body)
        action = data.get('action')
        asset_name = data.get('asset_name')
        amount = Decimal(str(data.get('amount', 0)))
        tx_date = data.get('date') or date.today().isoformat()

        if not action or not asset_name or amount <= 0:
            return JsonResponse({'success': False, 'error': '请提供完整的资金记录信息'}, status=400)

        parsed_date = date.fromisoformat(tx_date)
        if parsed_date > date.today():
            return JsonResponse({'success': False, 'error': '操作日期不能是未来日期'}, status=400)

        Transaction.objects.create(
            action=action,
            asset_name=asset_name,
            amount=amount,
            date=parsed_date,
        )
        return JsonResponse({'success': True})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


@require_POST
def api_transaction_update(request, tx_id):
    """更新操作日志"""
    try:
        tx = get_object_or_404(Transaction, id=tx_id)
        data = json.loads(request.body)

        if 'action' in data:
            tx.action = data['action']
        if 'asset_name' in data:
            tx.asset_name = data['asset_name']
        if 'amount' in data:
            tx.amount = Decimal(str(data['amount']))
        if 'date' in data:
            parsed_date = date.fromisoformat(data['date'])
            if parsed_date > date.today():
                return JsonResponse({'success': False, 'error': '操作日期不能是未来日期'}, status=400)
            tx.date = parsed_date
        if 'notes' in data:
            tx.notes = data['notes']

        tx.save()
        return JsonResponse({'success': True})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


@require_http_methods(["DELETE"])
def api_transaction_delete(request, tx_id):
    """删除操作日志"""
    try:
        tx = get_object_or_404(Transaction, id=tx_id)
        tx.delete()
        return JsonResponse({'success': True})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


@require_POST
def api_upload_screenshot(request):
    """上传并识别截图"""
    try:
        if 'image' not in request.FILES:
            return JsonResponse({'success': False, 'error': '未上传图片'}, status=400)

        image_file = request.FILES['image']
        upload = Upload.objects.create(
            image=image_file,
            status='processing',
        )

        # 获取 LLM 配置（统一键名）
        provider = Setting.get('llm_provider', 'openai_compatible')
        api_key = Setting.get('llm_api_key', '')
        api_url = Setting.get('llm_api_url', '')
        model = Setting.get('llm_model', '')

        if provider == 'anthropic' and not api_key and not api_url:
            upload.status = 'failed'
            upload.error_message = '未配置 AI 模型，请先在设置中配置'
            upload.save()
            return JsonResponse({'success': False, 'error': '未配置 AI 模型，请先在设置中配置'}, status=400)
        elif provider == 'openai_compatible' and not api_url:
            upload.status = 'failed'
            upload.error_message = '未配置 API 地址，请先在设置中配置'
            upload.save()
            return JsonResponse({'success': False, 'error': '未配置 API 地址，请先在设置中配置'}, status=400)

        # 获取保存后的文件路径
        image_path = os.path.join(django_settings.MEDIA_ROOT, upload.image.name)
        llm_max_tokens = int(Setting.get('llm_max_tokens', '2048'))

        # 查询已有持仓数据，传给 LLM 做去重匹配
        existing_holdings = list(
            Holding.objects.values('name', 'platform', 'asset_type').distinct()
        )

        result = recognize_screenshot(
            image_path, api_key,
            provider=provider,
            api_url=api_url,
            model=model,
            max_tokens=llm_max_tokens,
            existing_holdings=existing_holdings,
        )

        if result['success']:
            upload.recognized_data = result['data']
            upload.platform = result.get('platform', '')
            upload.status = 'recognized'
        else:
            upload.status = 'failed'
            upload.error_message = result['error']

        upload.save()

        return JsonResponse({
            'success': result['success'],
            'upload_id': upload.id,
            'data': result['data'],
            'platform': result.get('platform', ''),
            'error': result.get('error', ''),
            'existing_holdings': existing_holdings,
        })
    except Exception as e:
        logger.exception("api_upload_screenshot failed")
        return JsonResponse({'success': False, 'error': f'识别过程出错: {type(e).__name__}'}, status=500)


@require_POST
def api_confirm_upload(request):
    """确认截图识别结果，写入持仓"""
    try:
        data = json.loads(request.body)
        upload_id = data.get('upload_id')
        items = data.get('items', [])
        platform = data.get('platform', '')

        if upload_id:
            upload = get_object_or_404(Upload, id=upload_id)
            if upload.status == 'confirmed':
                return JsonResponse({
                    'success': False,
                    'error': '该截图已确认过，不可重复确认。如需更新数据请重新上传。',
                }, status=400)

        created_count = 0
        updated_count = 0

        with transaction.atomic():
            if upload_id:
                upload.status = 'confirmed'
                if platform:
                    upload.platform = platform
                upload.save()

            for item in items:
                layer_id = item.get('layer_id')
                if not layer_id:
                    suggested = item.get('suggested_layer', 1)
                    layer = AssetLayer.objects.filter(order=suggested).first()
                    if not layer:
                        layer = AssetLayer.objects.first()
                else:
                    layer = get_object_or_404(AssetLayer, id=layer_id)

                item_platform = item.get('platform', platform)

                holding = Holding.objects.filter(name=item['name'], platform=item_platform).first()
                holding_existed = holding is not None

                code = item.get('code', '')
                asset_type = item.get('asset_type', 'other')
                quantity = Decimal(str(item.get('quantity', 0)))
                cost_price = Decimal(str(item['cost_price'])) if item.get('cost_price') else None
                current_price = Decimal(str(item['current_price'])) if item.get('current_price') else None
                market_value = Decimal(str(item.get('market_value', 0)))
                profit_loss = Decimal(str(item.get('profit_loss', 0)))
                profit_loss_pct = Decimal(str(item.get('profit_loss_pct', 0)))

                has_price_info = bool(cost_price and current_price and quantity)

                if holding:
                    old_data = snapshot_holding(holding)
                    if code:
                        holding.code = code
                    holding.asset_type = asset_type
                    holding.quantity = quantity
                    holding.cost_price = cost_price
                    holding.current_price = current_price
                    if not has_price_info:
                        holding.market_value = market_value
                        holding.profit_loss = profit_loss
                        holding.profit_loss_pct = profit_loss_pct
                    holding.source = 'screenshot'
                    holding.save()
                    if not has_price_info and item.get('market_value'):
                        # save() 可能已从 market_value/profit_loss 反推了成本和收益率
                        # 仅回写 save() 不会覆盖的原始 market_value 和 profit_loss
                        update_fields = {'market_value': market_value, 'profit_loss': profit_loss}
                        if not holding.cost_price:
                            # save() 没能反推成本（如 profit_loss 为 0），保留 OCR 提供的收益率
                            update_fields['profit_loss_pct'] = profit_loss_pct
                        Holding.objects.filter(pk=holding.pk).update(**update_fields)
                    record_holding_change(holding, old_data, source='ocr')
                    updated_count += 1
                else:
                    holding = Holding(
                        layer=layer,
                        name=item['name'],
                        code=code,
                        asset_type=asset_type,
                        quantity=quantity,
                        cost_price=cost_price,
                        current_price=current_price,
                        market_value=market_value,
                        profit_loss=profit_loss,
                        profit_loss_pct=profit_loss_pct,
                        source='screenshot',
                        platform=item_platform,
                    )
                    holding.save()
                    if not has_price_info and item.get('market_value'):
                        update_fields = {'market_value': market_value, 'profit_loss': profit_loss}
                        if not holding.cost_price:
                            update_fields['profit_loss_pct'] = profit_loss_pct
                        Holding.objects.filter(pk=holding.pk).update(**update_fields)
                    record_holding_change(holding, old_data=None, source='ocr')
                    created_count += 1

            # Auto-remove holdings that disappeared from OCR results for this platform
            auto_remove = data.get('auto_remove_missing', False)
            if auto_remove and platform:
                ocr_names = {item['name'] for item in items}
                missing = Holding.objects.filter(
                    platform=platform, source='screenshot',
                ).exclude(name__in=ocr_names)
                removed_count = 0
                for h in missing:
                    record_holding_removal(h, source='ocr')
                    h.delete()
                    removed_count += 1
            else:
                removed_count = 0

        result = {'success': True, 'created': created_count, 'updated': updated_count}
        if removed_count:
            result['removed'] = removed_count
        return JsonResponse(result)
    except (ValueError, KeyError) as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)
    except Exception as e:
        logger.exception("api_confirm_upload failed")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@require_POST
def api_snapshot_create(request):
    """创建资产快照"""
    try:
        layers_data, total_value = _get_layers_summary()
        layer_values = {ld['name']: ld['actual_value'] for ld in layers_data}
        layer_ratios = {ld['name']: ld['actual_ratio'] for ld in layers_data}

        # 保存每笔持仓明细，用于快照对比
        holdings = Holding.objects.select_related('layer').all()
        holdings_data = [
            {
                'id': h.id,
                'name': h.name,
                'code': h.code,
                'layer': h.layer.name,
                'platform': h.platform,
                'market_value': float(h.market_value or 0),
                'profit_loss': float(h.profit_loss or 0),
                'profit_loss_pct': float(h.profit_loss_pct or 0),
            }
            for h in holdings
        ]

        # 完整性校验：持仓明细合计 vs 层级合计
        holdings_sum = sum(h['market_value'] for h in holdings_data)
        layers_sum = sum(layer_values.values())
        integrity_ok = abs(holdings_sum - layers_sum) < 1.0  # 允许1元舍入误差

        data = json.loads(request.body) if request.body else {}

        snapshot = Snapshot.objects.create(
            date=data.get('date') or timezone.now(),
            total_value=total_value,
            layer_values=layer_values,
            layer_ratios=layer_ratios,
            holdings_data=holdings_data,
            notes=data.get('notes', ''),
        )

        warning = None
        if not integrity_ok:
            diff = holdings_sum - layers_sum
            warning = f'数据完整性警告：持仓明细合计与层级合计差 ¥{diff:,.0f}，可能存在重复或遗漏持仓'
            logger.warning("Snapshot integrity check failed: holdings_sum=%.2f, layers_sum=%.2f, diff=%.2f",
                           holdings_sum, layers_sum, diff)

        result = {
            'success': True,
            'id': snapshot.id,
            'total_value': total_value,
        }
        if warning:
            result['warning'] = warning
        return JsonResponse(result)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


@require_http_methods(["DELETE"])
def api_snapshot_delete(request, snapshot_id):
    """删除资产快照"""
    try:
        snapshot = get_object_or_404(Snapshot, id=snapshot_id)
        snapshot.delete()
        return JsonResponse({'success': True})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


def api_snapshot_compare(request):
    """对比两个快照，找出持仓变动"""
    try:
        old_id = request.GET.get('old')
        new_id = request.GET.get('new')
        if not old_id or not new_id:
            return JsonResponse({'success': False, 'error': '需要提供 old 和 new 快照ID'}, status=400)

        old_snap = get_object_or_404(Snapshot, id=old_id)
        new_snap = get_object_or_404(Snapshot, id=new_id)

        old_holdings = {h['name']: h for h in (old_snap.holdings_data or [])}
        new_holdings = {h['name']: h for h in (new_snap.holdings_data or [])}

        all_names = sorted(set(list(old_holdings.keys()) + list(new_holdings.keys())))

        changes = []
        for name in all_names:
            old_h = old_holdings.get(name)
            new_h = new_holdings.get(name)

            old_val = old_h['market_value'] if old_h else 0
            new_val = new_h['market_value'] if new_h else 0
            diff = new_val - old_val

            if old_h and not new_h:
                status = 'removed'
            elif new_h and not old_h:
                status = 'added'
            elif abs(diff) < 0.01:
                status = 'unchanged'
            else:
                status = 'changed'

            changes.append({
                'name': name,
                'layer': (new_h or old_h).get('layer', ''),
                'platform': (new_h or old_h).get('platform', ''),
                'old_value': round(old_val, 2),
                'new_value': round(new_val, 2),
                'diff': round(diff, 2),
                'status': status,
            })

        # 按变动金额排序，亏损最多的排前面
        changes.sort(key=lambda x: x['diff'])

        return JsonResponse({
            'success': True,
            'old_date': timezone.localtime(old_snap.date).strftime('%Y-%m-%d %H:%M'),
            'new_date': timezone.localtime(new_snap.date).strftime('%Y-%m-%d %H:%M'),
            'old_total': float(old_snap.total_value),
            'new_total': float(new_snap.total_value),
            'total_diff': float(new_snap.total_value - old_snap.total_value),
            'changes': changes,
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


@require_POST
def api_checklist_complete(request):
    """保存检视清单完成记录"""
    try:
        data = json.loads(request.body)
        record = ChecklistRecord.objects.create(
            period_type=data['period_type'],
            completed_items=data.get('completed_items', []),
            total_items=data.get('total_items', 0),
            notes=data.get('notes', ''),
        )
        return JsonResponse({'success': True, 'id': record.id})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


@require_POST
def api_settings_save(request):
    """保存设置"""
    try:
        data = json.loads(request.body)

        # OCR LLM 配置（统一键名）
        for key in ('llm_mode', 'llm_provider', 'llm_api_url', 'llm_api_key',
                     'llm_model', 'llm_cloud_provider'):
            if key in data:
                Setting.set(key, data[key])
        if 'llm_max_tokens' in data:
            Setting.set('llm_max_tokens', str(data['llm_max_tokens']))

        # AI 顾问配置（统一键名）
        for key in ('advisor_mode', 'advisor_llm_provider', 'advisor_api_url',
                     'advisor_api_key', 'advisor_model', 'advisor_cloud_provider'):
            if key in data:
                Setting.set(key, data[key])
        if 'advisor_max_tokens' in data:
            Setting.set('advisor_max_tokens', str(data['advisor_max_tokens']))

        if 'layers' in data:
            for layer_data in data['layers']:
                layer = get_object_or_404(AssetLayer, id=layer_data['id'])
                layer.target_ratio = float(layer_data['target_ratio'])
                layer.save()

        return JsonResponse({'success': True})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


def api_performance_calc(request):
    """计算指定区间的投资回报率"""
    try:
        from .services.performance import calculate_interval_performance
        
        start_date = request.GET.get('start_date')
        end_date = request.GET.get('end_date')

        if not start_date or not end_date:
            return JsonResponse({'success': False, 'error': 'Missing start_date or end_date'}, status=400)

        result = calculate_interval_performance(start_date, end_date)
        return JsonResponse({'success': True, 'data': result})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


def api_export_data(request):
    """导出全部数据为 JSON"""
    data = {
        'export_date': timezone.now().isoformat(),
        'layers': list(AssetLayer.objects.values()),
        'holdings': list(Holding.objects.values()),
        'snapshots': [
            {
                **{k: v for k, v in s.items() if k != 'date'},
                'date': s['date'].isoformat(),
            }
            for s in Snapshot.objects.values()
        ],
        'transactions': [
            {
                **{k: v for k, v in t.items() if k != 'date'},
                'date': t['date'].isoformat(),
            }
            for t in Transaction.objects.values()
        ],
    }
    response = JsonResponse(data, encoder=DecimalEncoder, json_dumps_params={'ensure_ascii': False, 'indent': 2})
    response['Content-Disposition'] = f'attachment; filename="bigbill_export_{date.today()}.json"'
    return response


@require_POST
def api_import_data(request):
    """从 JSON 导入数据"""
    try:
        data = json.loads(request.body)

        # 导入层级设置
        if 'layers' in data:
            for ld in data['layers']:
                AssetLayer.objects.update_or_create(
                    id=ld['id'],
                    defaults={
                        'name': ld['name'],
                        'target_ratio': ld['target_ratio'],
                        'description': ld.get('description', ''),
                        'color': ld.get('color', '#3b82f6'),
                        'order': ld.get('order', 0),
                    }
                )

        # 导入持仓
        if 'holdings' in data:
            for hd in data['holdings']:
                layer = AssetLayer.objects.filter(id=hd.get('layer_id')).first()
                if layer:
                    Holding.objects.create(
                        layer=layer,
                        name=hd['name'],
                        code=hd.get('code', ''),
                        asset_type=hd.get('asset_type', 'other'),
                        quantity=hd.get('quantity', 0),
                        cost_price=hd.get('cost_price'),
                        current_price=hd.get('current_price'),
                        market_value=hd.get('market_value', 0),
                        profit_loss=hd.get('profit_loss', 0),
                        profit_loss_pct=hd.get('profit_loss_pct', 0),
                        source=hd.get('source', 'manual'),
                        notes=hd.get('notes', ''),
                    )

        return JsonResponse({'success': True})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


def advisor_page(request):
    """AI 投资顾问页面"""
    layers_data, total_value = _get_layers_summary()
    holdings_count = Holding.objects.count()

    provider = Setting.get('advisor_llm_provider', 'openai_compatible')
    advisor_key = Setting.get('advisor_api_key', '')
    advisor_url = Setting.get('advisor_api_url', '')
    if provider == 'anthropic':
        has_api_config = bool(advisor_key) or bool(advisor_url)
    else:
        has_api_config = bool(advisor_url)

    # Fetch latest report to load automatically
    latest_report = EvaluationReport.objects.first()
    latest_report_json = None
    if latest_report:
        latest_report_json = json.dumps({
            'portfolio_summary': latest_report.summary_data,
            'holdings': latest_report.holdings_data,
            'date': latest_report.date.isoformat()
        })

    context = {
        'total_value': total_value,
        'layers_data': layers_data,
        'holdings_count': holdings_count,
        'has_api_config': has_api_config,
        'latest_report_json': latest_report_json,
    }
    return render(request, 'assets/advisor.html', context)

def advisor_history(request):
    """AI 顾问历史报告列表"""
    reports = EvaluationReport.objects.all()
    return render(request, 'assets/advisor_history.html', {'reports': reports})

def advisor_report_detail(request, report_id):
    """AI 顾问单独报告详情页（可以复用 advisor.html 或独立的只读页面）"""
    report = get_object_or_404(EvaluationReport, id=report_id)
    report_json = json.dumps({
        'portfolio_summary': report.summary_data,
        'holdings': report.holdings_data,
        'date': report.date.isoformat()
    })
    
    # Passing flag to template so we can render it in read-only mode
    context = {
        'report': report,
        'report_json': report_json,
        'is_history_detail': True
    }
    return render(request, 'assets/advisor.html', context)


@require_POST
def api_advisor_evaluate(request):
    """调用 AI 顾问评估投资组合"""
    try:
        layers_data, total_value = _get_layers_summary()

        if total_value <= 0:
            return JsonResponse({'success': False, 'error': '当前无持仓数据，请先添加持仓'}, status=400)

        # Build holdings data for the advisor
        holdings = Holding.objects.select_related('layer').all()
        holdings_data = []
        for h in holdings:
            asset_type_display = dict(ASSET_TYPE_CHOICES).get(h.asset_type, h.asset_type)
            holdings_data.append({
                'name': h.name,
                'code': h.code,
                'asset_type': h.asset_type,
                'asset_type_display': asset_type_display,
                'layer_name': h.layer.name,
                'platform': h.platform,
                'market_value': float(h.market_value or 0),
                'profit_loss': float(h.profit_loss or 0),
                'profit_loss_pct': float(h.profit_loss_pct or 0),
                'quantity': float(h.quantity or 0),
                'cost_price': float(h.cost_price) if h.cost_price else None,
                'current_price': float(h.current_price) if h.current_price else None,
            })

        result = evaluate_portfolio(layers_data, holdings_data, total_value)

        # 成功时保存报告
        if result.get('success') and 'data' in result:
            data = result['data']
            summary_data = data.get('portfolio_summary', {})
            holdings_d = data.get('holdings', [])
            score = summary_data.get('score', 0)
            overall_health = summary_data.get('overall_health', 'unknown')

            report = EvaluationReport.objects.create(
                total_value=total_value,
                score=score,
                overall_health=overall_health,
                summary_data=summary_data,
                holdings_data=holdings_d
            )
            result['data']['date'] = report.date.isoformat()

        return JsonResponse(result)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@require_POST
def api_asset_evaluate(request):
    """调用 AI 对单个资产进行深度评估"""
    try:
        body = json.loads(request.body)
        asset_name = body.get('asset_name', '')
        if not asset_name:
            return JsonResponse({'success': False, 'error': '缺少资产名称'}, status=400)

        # 查找持仓
        holding = Holding.objects.select_related('layer').filter(name=asset_name).first()

        # 构建完整组合数据（与组合评估一致）
        layers_data, total_value = _get_layers_summary()
        all_holdings = Holding.objects.select_related('layer').all()
        all_holdings_data = []
        for h in all_holdings:
            atd = dict(ASSET_TYPE_CHOICES).get(h.asset_type, h.asset_type)
            all_holdings_data.append({
                'name': h.name,
                'code': h.code,
                'asset_type': h.asset_type,
                'asset_type_display': atd,
                'layer_name': h.layer.name,
                'platform': h.platform,
                'market_value': float(h.market_value or 0),
                'profit_loss': float(h.profit_loss or 0),
                'profit_loss_pct': float(h.profit_loss_pct or 0),
                'quantity': float(h.quantity or 0),
                'cost_price': float(h.cost_price) if h.cost_price else None,
                'current_price': float(h.current_price) if h.current_price else None,
            })

        # 构建目标资产信息
        asset_info = {'name': asset_name, 'is_active': holding is not None}
        if holding:
            asset_type_display = dict(ASSET_TYPE_CHOICES).get(holding.asset_type, holding.asset_type)
            pct_of_total = float(holding.market_value or 0) / float(total_value) * 100 if total_value > 0 else 0
            asset_info.update({
                'code': holding.code,
                'asset_type': holding.asset_type,
                'asset_type_display': asset_type_display,
                'layer_name': holding.layer.name,
                'platform': holding.platform,
                'market_value': float(holding.market_value or 0),
                'quantity': float(holding.quantity or 0),
                'cost_price': float(holding.cost_price) if holding.cost_price else None,
                'current_price': float(holding.current_price) if holding.current_price else None,
                'profit_loss': float(holding.profit_loss or 0),
                'profit_loss_pct': float(holding.profit_loss_pct or 0),
                'pct_of_total': pct_of_total,
            })
        else:
            tx_sample = Transaction.objects.filter(asset_name=asset_name).first()
            asset_info.update({
                'code': '',
                'asset_type_display': '未知',
                'layer_name': '未知',
                'platform': tx_sample.platform if tx_sample else '',
            })

        # 交易记录
        action_labels = dict(Transaction.ACTION_CHOICES)
        transactions = list(
            Transaction.objects.filter(asset_name=asset_name)
            .order_by('-date', '-created_at')
            .values('action', 'quantity', 'price', 'amount', 'date', 'realized_pnl')
        )
        for tx in transactions:
            tx['action_display'] = action_labels.get(tx['action'], tx['action'])
            tx['date'] = tx['date'].strftime('%Y-%m-%d') if hasattr(tx['date'], 'strftime') else str(tx['date'])
            tx['quantity'] = float(tx['quantity']) if tx['quantity'] else None
            tx['price'] = float(tx['price']) if tx['price'] else None
            tx['amount'] = float(tx['amount'] or 0)
            tx['realized_pnl'] = float(tx['realized_pnl']) if tx['realized_pnl'] else None

        # 历史市值
        snapshots = Snapshot.objects.order_by('date').values('date', 'holdings_data')
        value_history = []
        for snap in snapshots:
            for h in (snap['holdings_data'] or []):
                if h.get('name') == asset_name:
                    value_history.append({
                        'date': snap['date'].strftime('%Y-%m-%d'),
                        'market_value': float(h.get('market_value', 0)),
                        'profit_loss': float(h.get('profit_loss', 0)),
                    })
                    break

        result = evaluate_asset(
            asset_info, transactions, value_history,
            layers_data=layers_data, holdings_data=all_holdings_data, total_value=total_value,
        )

        # 保存评估记录
        if result.get('success') and result.get('data'):
            data = result['data']
            ev = AssetEvaluation.objects.create(
                asset_name=asset_name,
                score=data.get('score', 0),
                signal=data.get('signal', ''),
                signal_reason=data.get('signal_reason', ''),
                risk_level=data.get('risk_level', ''),
                analysis_data=data.get('analysis', {}),
                action_plan=data.get('action_plan', ''),
                risks=data.get('risks', []),
                highlights=data.get('highlights', []),
            )
            result['data']['date'] = ev.date.isoformat()

        return JsonResponse(result)
    except Exception as e:
        logger.exception("api_asset_evaluate failed")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@require_POST
def api_test_llm(request):
    """测试 LLM 连接"""
    import httpx

    try:
        data = json.loads(request.body)
        provider = data.get('provider', 'openai_compatible')
        api_url = data.get('api_url', '')
        api_key = data.get('api_key', '')
        target = data.get('target', '')

        if not api_key:
            if target == 'ocr':
                api_key = Setting.get('llm_api_key', '') or Setting.get('anthropic_api_key', '') or Setting.get('local_api_key', '')
            elif target == 'advisor':
                api_key = Setting.get('advisor_api_key', '') or Setting.get('advisor_anthropic_api_key', '') or Setting.get('advisor_local_api_key', '')

        if provider == 'openai_compatible':
            if not api_url:
                return JsonResponse({'success': False, 'error': '请输入 API 地址'})

            url = api_url.rstrip('/')
            models_url = url + '/models' if url.endswith('/v1') else url + '/v1/models'

            headers = {}
            if api_key:
                headers['Authorization'] = f'Bearer {api_key}'

            resp = httpx.get(models_url, headers=headers, timeout=10.0)
            resp.raise_for_status()
            result = resp.json()
            models = [m.get('id', '未知') for m in result.get('data', [])]

            return JsonResponse({
                'success': True,
                'message': f'连接成功！可用模型: {", ".join(models) if models else "未返回模型列表"}',
            })

        elif provider == 'anthropic':
            if api_url:
                # 本地 Anthropic 协议 — 测试连通性
                headers = {}
                if api_key:
                    headers['Authorization'] = f'Bearer {api_key}'
                url = api_url.rstrip('/')
                models_url = url + '/models' if url.endswith('/v1') else url + '/v1/models'
                try:
                    resp = httpx.get(models_url, headers=headers, timeout=5.0)
                    if resp.status_code == 200:
                        result = resp.json()
                        models = [m.get('id', '未知') for m in result.get('data', [])]
                        return JsonResponse({
                            'success': True,
                            'message': f'连接成功！可用模型: {", ".join(models) if models else "服务已响应"}',
                        })
                except Exception:
                    pass
                # 退回到基本连通测试
                try:
                    resp = httpx.get(url, timeout=5.0, follow_redirects=True)
                    return JsonResponse({
                        'success': True,
                        'message': f'服务已响应（HTTP {resp.status_code}）',
                    })
                except httpx.ConnectError:
                    return JsonResponse({'success': False, 'error': f'无法连接到 {api_url}，请检查服务是否已启动'})
            else:
                # 云端 Anthropic — 校验 Key 格式
                if not api_key:
                    return JsonResponse({'success': False, 'error': '请输入 API Key'})
                if api_key.startswith('sk-ant-'):
                    return JsonResponse({
                        'success': True,
                        'message': 'API Key 格式正确，将在首次使用时验证连接',
                    })
                else:
                    return JsonResponse({
                        'success': False,
                        'error': 'Anthropic API Key 应以 sk-ant- 开头',
                    })

        else:
            return JsonResponse({'success': False, 'error': f'未知协议: {provider}'})

    except httpx.ConnectError:
        return JsonResponse({'success': False, 'error': '无法连接，请检查服务是否已启动'})
    except httpx.HTTPStatusError as e:
        return JsonResponse({'success': False, 'error': f'HTTP {e.response.status_code}: {e.response.text[:200]}'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


# ==================== 资产详情 ====================

def asset_list_page(request):
    """所有资产列表（含已清仓）"""
    # 当前持仓
    current_holdings = list(
        Holding.objects.select_related('layer').all()
        .values('id', 'name', 'code', 'asset_type', 'platform',
                'market_value', 'profit_loss', 'profit_loss_pct', 'layer__name')
    )
    current_names = {h['name'] for h in current_holdings}

    # 从交易记录中找到已清仓的资产
    from django.db.models import Max, Sum, Q
    historical = list(
        Transaction.objects.exclude(asset_name__in=current_names)
        .exclude(action__in=['transfer', 'withdraw'])
        .values('asset_name', 'platform')
        .annotate(
            last_date=Max('date'),
            total_buy=Sum('amount', filter=Q(action='buy')),
            total_sell=Sum('amount', filter=Q(action='sell')),
            realized_pnl=Sum('realized_pnl', filter=Q(action='sell')),
        )
        .order_by('-last_date')
    )

    asset_type_labels = dict(ASSET_TYPE_CHOICES)
    for h in current_holdings:
        h['asset_type_display'] = asset_type_labels.get(h['asset_type'], h['asset_type'])
        h['status'] = 'active'

    for h in historical:
        h['status'] = 'sold'
        h['name'] = h['asset_name']

    context = {
        'current_holdings': current_holdings,
        'historical_assets': historical,
    }
    return render(request, 'assets/asset_list.html', context)


def asset_detail_by_name(request):
    """通过 asset_name 查询资产详情（用于已清仓资产）"""
    name = request.GET.get('name', '')
    if not name:
        from django.http import Http404
        raise Http404
    # 尝试匹配当前持仓
    holding = Holding.objects.filter(name=name).first()
    if holding:
        from django.shortcuts import redirect
        return redirect('assets:asset_detail', holding_id=holding.id)
    # 已清仓 — 直接渲染
    return _render_asset_detail(request, holding=None, asset_name=name)


def asset_detail_page(request, holding_id):
    """资产详情页（当前持仓）"""
    holding = get_object_or_404(Holding, id=holding_id)
    return _render_asset_detail(request, holding=holding, asset_name=holding.name)


def _render_asset_detail(request, holding, asset_name):
    """渲染资产详情页的共用逻辑"""
    # 1. 交易记录
    transactions = list(
        Transaction.objects.filter(asset_name=asset_name)
        .order_by('-date', '-created_at')
        .values('id', 'action', 'asset_name', 'quantity', 'price',
                'amount', 'date', 'source', 'realized_pnl', 'platform', 'notes')
    )
    action_labels = dict(Transaction.ACTION_CHOICES)
    source_labels = dict(Transaction.SOURCE_CHOICES)
    for tx in transactions:
        tx['action_display'] = action_labels.get(tx['action'], tx['action'])
        tx['source_display'] = source_labels.get(tx['source'], tx['source'])

    # 2. 从快照中提取该资产的历史市值
    snapshots = Snapshot.objects.order_by('date').values('date', 'holdings_data')
    value_history = []
    for snap in snapshots:
        for h in (snap['holdings_data'] or []):
            if h.get('name') == asset_name:
                value_history.append({
                    'date': snap['date'].strftime('%Y-%m-%d'),
                    'market_value': h.get('market_value', 0),
                    'profit_loss': h.get('profit_loss', 0),
                    'profit_loss_pct': h.get('profit_loss_pct', 0),
                })
                break

    # 3. AI 评估历史
    reports = EvaluationReport.objects.order_by('-date').values('date', 'holdings_data', 'score')
    evaluations = []
    for rpt in reports:
        for h in (rpt['holdings_data'] or []):
            if h.get('name') == asset_name:
                evaluations.append({
                    'date': timezone.localtime(rpt['date']).strftime('%Y-%m-%d %H:%M'),
                    'signal': h.get('signal', ''),
                    'signal_reason': h.get('signal_reason', ''),
                    'risk_level': h.get('risk_level', ''),
                    'comment': h.get('comment', ''),
                    'portfolio_score': rpt['score'],
                })
                break

    # 4. 汇总统计
    total_bought = sum(tx['amount'] for tx in transactions if tx['action'] == 'buy')
    total_sold = sum(tx['amount'] for tx in transactions if tx['action'] == 'sell')
    total_realized = sum(
        (tx['realized_pnl'] or 0) for tx in transactions if tx['action'] == 'sell'
    )
    is_active = holding is not None

    # 当前持仓信息
    holding_data = None
    if holding:
        holding_data = {
            'id': holding.id,
            'name': holding.name,
            'code': holding.code,
            'asset_type': holding.asset_type,
            'asset_type_display': dict(ASSET_TYPE_CHOICES).get(holding.asset_type, holding.asset_type),
            'layer_name': holding.layer.name,
            'platform': holding.platform,
            'quantity': holding.quantity,
            'cost_price': holding.cost_price,
            'current_price': holding.current_price,
            'market_value': holding.market_value,
            'cost_total': (holding.cost_price * holding.quantity) if (holding.cost_price and holding.quantity) else (holding.market_value - holding.profit_loss) if (holding.market_value and holding.profit_loss is not None) else 0,
            'profit_loss': holding.profit_loss,
            'profit_loss_pct': holding.profit_loss_pct,
        }

    # 5. 单资产深度评估历史
    asset_evals = list(
        AssetEvaluation.objects.filter(asset_name=asset_name)
        .order_by('-date')[:10]
        .values('id', 'date', 'score', 'signal', 'signal_reason',
                'risk_level', 'analysis_data', 'action_plan', 'risks', 'highlights')
    )
    latest_asset_eval_json = None
    if asset_evals:
        latest = asset_evals[0]
        latest_asset_eval_json = json.dumps({
            'score': latest['score'],
            'signal': latest['signal'],
            'signal_reason': latest['signal_reason'],
            'risk_level': latest['risk_level'],
            'analysis': latest['analysis_data'],
            'action_plan': latest['action_plan'],
            'risks': latest['risks'],
            'highlights': latest['highlights'],
            'date': latest['date'].isoformat(),
        }, ensure_ascii=False)

    # 6. 合并所有评估记录为统一时间线
    all_evaluations = []
    for ev in evaluations:
        all_evaluations.append({
            'date': ev['date'],  # already formatted
            'date_sort': ev['date'],
            'source': 'portfolio',
            'signal': ev['signal'],
            'signal_reason': ev['signal_reason'],
            'risk_level': ev['risk_level'],
            'comment': ev['comment'],
        })
    for ae in asset_evals:
        local_date = timezone.localtime(ae['date'])
        all_evaluations.append({
            'date': local_date.strftime('%Y-%m-%d %H:%M'),
            'date_sort': local_date.strftime('%Y-%m-%d %H:%M'),
            'source': 'deep',
            'signal': ae['signal'],
            'signal_reason': ae['signal_reason'],
            'risk_level': ae['risk_level'],
            'score': ae['score'],
            'action_plan': ae['action_plan'],
            'analysis_json': json.dumps({
                'score': ae['score'],
                'signal': ae['signal'],
                'signal_reason': ae['signal_reason'],
                'risk_level': ae['risk_level'],
                'analysis': ae['analysis_data'],
                'action_plan': ae['action_plan'],
                'risks': ae['risks'],
                'highlights': ae['highlights'],
            }, ensure_ascii=False),
        })
    all_evaluations.sort(key=lambda x: x['date_sort'], reverse=True)

    # 检查 AI 顾问配置
    provider = Setting.get('advisor_llm_provider', 'openai_compatible')
    advisor_key = Setting.get('advisor_api_key', '')
    advisor_url = Setting.get('advisor_api_url', '')
    if provider == 'anthropic':
        has_api_config = bool(advisor_key) or bool(advisor_url)
    else:
        has_api_config = bool(advisor_url)

    has_any_deep_eval = bool(asset_evals)

    context = {
        'asset_name': asset_name,
        'holding': holding_data,
        'is_active': is_active,
        'transactions': transactions,
        'value_history_json': json.dumps(value_history, ensure_ascii=False),
        'all_evaluations': all_evaluations,
        'has_any_deep_eval': has_any_deep_eval,
        'latest_asset_eval_json': latest_asset_eval_json,
        'total_bought': total_bought,
        'total_sold': total_sold,
        'total_realized': total_realized,
        'tx_count': len(transactions),
        'has_api_config': has_api_config,
    }
    return render(request, 'assets/asset_detail.html', context)


# ==================== 资金流向 ====================

def cashflow_page(request):
    """资金流向分析页面 — 从快照和交易记录自动推算"""
    from .services.cashflow import analyze_portfolio_flows
    analysis = analyze_portfolio_flows()
    context = {
        'summary': analysis['summary'],
        'periods': analysis['periods'],
        'recent_transactions': analysis['recent_transactions'],
        'monthly_json': json.dumps(analysis['monthly_chart'], cls=DecimalEncoder, ensure_ascii=False),
        'action_json': json.dumps(analysis['action_chart'], cls=DecimalEncoder, ensure_ascii=False),
    }
    return render(request, 'assets/cashflow.html', context)


@require_POST
def api_cashflow_confirm(request):
    """确认推算的资金流向 — 创建 transfer/withdraw 交易记录"""
    try:
        data = json.loads(request.body)
        action = data.get('action')  # 'transfer' or 'withdraw'
        amount = Decimal(str(data.get('amount', 0)))
        tx_date = data.get('date') or date.today().isoformat()
        notes = data.get('notes', '')

        if action not in ('transfer', 'withdraw'):
            return JsonResponse({'success': False, 'error': '类型必须为转入或转出'}, status=400)
        if amount <= 0:
            return JsonResponse({'success': False, 'error': '金额必须大于0'}, status=400)

        tx = Transaction.objects.create(
            action=action,
            asset_name='资金转入' if action == 'transfer' else '资金转出',
            amount=amount,
            date=date.fromisoformat(tx_date),
            source='manual',
            notes=notes or '用户确认推算',
        )
        return JsonResponse({'success': True, 'id': tx.id})
    except (ValueError, KeyError) as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)
    except Exception as e:
        logger.exception("api_cashflow_confirm failed")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@require_POST
def api_alert_dismiss(request):
    """忽略预警（7天内不再显示）"""
    from .models import AlertAction
    try:
        data = json.loads(request.body)
        alert_type = data.get('alert_type', '')
        holding_id = data.get('holding_id')
        layer_name = data.get('layer_name')  # for deviation alerts

        if layer_name:
            # 层级偏差预警 → 存入 Setting
            raw = Setting.get('dismissed_deviation_alerts', '{}')
            try:
                dismissed = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                dismissed = {}
            dismissed[layer_name] = timezone.now().isoformat()
            Setting.set('dismissed_deviation_alerts', json.dumps(dismissed, ensure_ascii=False))
            return JsonResponse({'success': True})

        if holding_id and alert_type:
            # 持仓级风险预警 → AlertAction 记录
            AlertAction.objects.create(
                holding_id=holding_id,
                alert_type=alert_type,
                action='dismissed',
            )
            return JsonResponse({'success': True})

        return JsonResponse({'success': False, 'error': '缺少参数'}, status=400)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)
