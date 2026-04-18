import json
import os
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings as django_settings
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_http_methods

from .models import (
    AssetLayer, Holding, Snapshot, Transaction,
    Upload, ChecklistRecord, Setting, ASSET_TYPE_CHOICES,
    EvaluationReport
)
from .services.ocr import recognize_screenshot
from .services.rebalance import (
    calculate_rebalance, DRAWDOWN_PROTOCOLS
)
from .services.advisor import evaluate_portfolio


class DecimalEncoder(json.JSONEncoder):
    """JSON 编码器：将 Decimal 转为 float 以保持前端兼容"""
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)

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

    # 计算总盈亏
    total_profit = sum((h.profit_loss or Decimal('0')) for h in Holding.objects.all())
    total_cost = sum(
        (h.cost_price or Decimal('0')) * h.quantity for h in Holding.objects.all()
        if h.cost_price and h.quantity
    )
    total_profit_pct = float(total_profit / total_cost * 100) if total_cost > 0 else 0

    from .services.rebalance import calculate_risk_alerts

    # 偏差警告
    deviation_alerts = [ld for ld in layers_data if abs(ld['deviation']) > 5]
    
    # 风险预警
    holdings = Holding.objects.all()
    risk_alerts = calculate_risk_alerts(holdings, total_value)

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
    holdings = Holding.objects.all()
    # 往再平衡的分析里追加持股风控预警
    risk_alerts = calculate_risk_alerts(holdings, total_value)
    # prepend risk alerts so they are highly visible
    rebalance_result['alerts'] = risk_alerts + rebalance_result.get('alerts', [])

    context = {
        'total_value': total_value,
        'rebalance': rebalance_result,
        'rebalance_json': json.dumps(rebalance_result, cls=DecimalEncoder, ensure_ascii=False),
        'layers_data': layers_data,
        'layers_json': json.dumps(layers_data, cls=DecimalEncoder, ensure_ascii=False),
        'drawdown_protocols': DRAWDOWN_PROTOCOLS,
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
    holdings = Holding.objects.all()
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
        holding = Holding.objects.create(
            layer=layer,
            name=data['name'],
            code=data.get('code', ''),
            asset_type=data.get('asset_type', 'other'),
            quantity=Decimal(str(data.get('quantity', 0))),
            cost_price=Decimal(str(data['cost_price'])) if data.get('cost_price') else None,
            current_price=Decimal(str(data['current_price'])) if data.get('current_price') else None,
            market_value=Decimal(str(data.get('market_value', 0))),
            source=data.get('source', 'manual'),
            platform=data.get('platform', ''),
            notes=data.get('notes', ''),
        )
        # If market_value was directly provided and no price info, use it directly
        if not holding.current_price and data.get('market_value'):
            holding.market_value = Decimal(str(data['market_value']))
            Holding.objects.filter(pk=holding.pk).update(market_value=holding.market_value)

        return JsonResponse({'success': True, 'id': holding.id})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


@require_http_methods(["PUT"])
def api_holding_update(request, holding_id):
    """更新持仓"""
    try:
        holding = get_object_or_404(Holding, id=holding_id)
        data = json.loads(request.body)

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
        return JsonResponse({'success': True})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


@require_http_methods(["DELETE"])
def api_holding_delete(request, holding_id):
    """删除持仓"""
    try:
        holding = get_object_or_404(Holding, id=holding_id)
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

        Transaction.objects.create(
            action=action,
            asset_name=asset_name,
            amount=amount,
            date=tx_date,
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
            tx.date = data['date']
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
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


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
            upload.status = 'confirmed'
            if platform:
                upload.platform = platform
            upload.save()

        created_count = 0
        updated_count = 0
        for item in items:
            layer_id = item.get('layer_id')
            if not layer_id:
                # 根据 suggested_layer 分配
                suggested = item.get('suggested_layer', 1)
                layer = AssetLayer.objects.filter(order=suggested).first()
                if not layer:
                    layer = AssetLayer.objects.first()
            else:
                layer = get_object_or_404(AssetLayer, id=layer_id)

            # 使用 item 级别的 platform，或全局 platform
            item_platform = item.get('platform', platform)

            # 查找同名且同平台的持仓，如果存在则更新
            holding = Holding.objects.filter(name=item['name'], platform=item_platform).first()
            
            # 准备数据
            code = item.get('code', '')
            asset_type = item.get('asset_type', 'other')
            quantity = Decimal(str(item.get('quantity', 0)))
            cost_price = Decimal(str(item['cost_price'])) if item.get('cost_price') else None
            current_price = Decimal(str(item['current_price'])) if item.get('current_price') else None
            market_value = Decimal(str(item.get('market_value', 0)))
            profit_loss = Decimal(str(item.get('profit_loss', 0)))
            profit_loss_pct = Decimal(str(item.get('profit_loss_pct', 0)))

            if holding:
                # 更新现有持仓（保留用户已分配的层级）
                if code:
                    holding.code = code
                holding.asset_type = asset_type
                holding.quantity = quantity
                holding.cost_price = cost_price
                holding.current_price = current_price
                holding.market_value = market_value
                holding.profit_loss = profit_loss
                holding.profit_loss_pct = profit_loss_pct
                holding.source = 'screenshot'
                holding.save()
                updated_count += 1
            else:
                # 创建新持仓
                holding = Holding.objects.create(
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

            # Preserve exact screenshot values if missing prices (overrides recalculation if happened)
            if not holding.current_price and item.get('market_value'):
                Holding.objects.filter(pk=holding.pk).update(
                    market_value=market_value,
                    profit_loss=profit_loss,
                    profit_loss_pct=profit_loss_pct,
                )

            created_count += 1

        return JsonResponse({'success': True, 'created': created_count, 'updated': updated_count})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


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

        data = json.loads(request.body) if request.body else {}

        snapshot = Snapshot.objects.create(
            date=data.get('date') or timezone.now(),
            total_value=total_value,
            layer_values=layer_values,
            layer_ratios=layer_ratios,
            holdings_data=holdings_data,
            notes=data.get('notes', ''),
        )
        return JsonResponse({
            'success': True,
            'id': snapshot.id,
            'total_value': total_value,
        })
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
