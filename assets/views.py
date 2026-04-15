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
    Upload, ChecklistRecord, Setting, ASSET_TYPE_CHOICES
)
from .services.ocr import recognize_screenshot
from .services.rebalance import (
    calculate_rebalance, allocate_new_funds, DRAWDOWN_PROTOCOLS
)


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

    # 偏差警告
    alerts = [ld for ld in layers_data if abs(ld['deviation']) > 5]

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
        'alerts': alerts,
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
    provider = Setting.get('llm_provider', 'anthropic')
    if provider == 'anthropic':
        has_api_config = bool(Setting.get('anthropic_api_key'))
    else:
        has_api_config = bool(Setting.get('local_api_url'))
    layers = AssetLayer.objects.all()

    context = {
        'uploads': uploads,
        'has_api_key': has_api_config,
        'layers': layers,
        'asset_type_choices': ASSET_TYPE_CHOICES,
    }
    return render(request, 'assets/upload.html', context)


def rebalance_page(request):
    """再平衡页面"""
    layers_data, total_value = _get_layers_summary()
    rebalance_result = calculate_rebalance(layers_data, total_value)

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

    snapshots_json = json.dumps([
        {
            'date': timezone.localtime(s.date).strftime('%Y-%m-%d %H:%M:%S'),
            'total_value': s.total_value,
            'layer_values': s.layer_values,
        }
        for s in reversed(list(snapshots))
    ], cls=DecimalEncoder, ensure_ascii=False)

    context = {
        'snapshots': snapshots,
        'transactions': transactions,
        'snapshots_json': snapshots_json,
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
                '查看各账户总体净值变化',
                '确认本周定投扣款是否成功执行（DCA阶段）',
                '浏览宏观新闻标题，判断是否有重大事件',
                '提醒：周检视不做任何买卖操作',
            ],
        },
        'monthly': {
            'name': '月检视',
            'period': '每月第一个周末 · 约30分钟',
            'items': [
                '记录各层级实际比例 vs 目标比例',
                '检查货币基金收益率是否异常偏低',
                '确认债券基金是否有异常波动（单月跌幅>1%即为异常）',
                '审视单只个股仓位是否突破5%红线',
                '如有超5%个股，本月内分批减仓至目标比例',
            ],
        },
        'quarterly': {
            'name': '季度检视',
            'period': '季末最后一周 · 约1-2小时',
            'items': [
                '全面审视五层配置比例，判断是否需要再平衡',
                '检查各基金产品同类排名（连续两季后25%应考虑替换）',
                '审视行业暴露：检查股票持仓是否在某一行业过度集中',
                '审视第五层卫星仓位每笔投资逻辑是否仍然成立',
                '记录本季度总回报和各层级回报',
            ],
        },
        'yearly': {
            'name': '年度大检',
            'period': '12月或次年1月初 · 约2-3小时',
            'items': [
                '各层级实际比例 vs. 目标比例，执行强制再平衡',
                '单只个股是否有超过5%的情况',
                '基金产品同类排名审视，替换连续落后的基金',
                '保险保障是否充足，受益人是否正确',
                '税务优化：股息持有期、个税扣除项是否充分利用',
                '第五层卫星仓位每笔投资逻辑重新评估',
                '风险偏好是否需要调整（家庭、事业、健康变化）',
                '下一年度目标配置比例是否需要微调（年龄因素）',
                '遗嘱、家族信托、子女教育基金进展审视',
                '记录本年度总回报、各层级回报、重大决策日志',
            ],
        },
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
    }
    return render(request, 'assets/checklist.html', context)


def settings_page(request):
    """设置页面"""
    layers = AssetLayer.objects.all()

    # LLM 配置
    provider = Setting.get('llm_provider', 'anthropic')
    anthropic_key = Setting.get('anthropic_api_key', '')
    local_api_url = Setting.get('local_api_url', '')
    local_api_key = Setting.get('local_api_key', '')
    local_model = Setting.get('local_model', '')

    # Mask keys for display
    def mask_key(key):
        if not key:
            return ''
        return key[:8] + '...' + key[-4:] if len(key) > 12 else '***'

    context = {
        'layers': layers,
        'llm_provider': provider,
        'has_anthropic_key': bool(anthropic_key),
        'masked_anthropic_key': mask_key(anthropic_key),
        'local_api_url': local_api_url,
        'has_local_key': bool(local_api_key),
        'masked_local_key': mask_key(local_api_key),
        'local_model': local_model,
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

        # 获取 LLM 配置
        provider = Setting.get('llm_provider', 'anthropic')
        if provider == 'anthropic':
            api_key = Setting.get('anthropic_api_key')
            api_url = ''
            model = ''
            if not api_key:
                upload.status = 'failed'
                upload.error_message = '未配置 Anthropic API Key'
                upload.save()
                return JsonResponse({'success': False, 'error': '未配置 Anthropic API Key，请先在设置中配置'}, status=400)
        else:
            api_key = Setting.get('local_api_key', '')
            api_url = Setting.get('local_api_url', '')
            model = Setting.get('local_model', '')
            if not api_url:
                upload.status = 'failed'
                upload.error_message = '未配置本地模型 API 地址'
                upload.save()
                return JsonResponse({'success': False, 'error': '未配置本地模型 API 地址，请先在设置中配置'}, status=400)

        # 获取保存后的文件路径
        image_path = os.path.join(django_settings.MEDIA_ROOT, upload.image.name)

        result = recognize_screenshot(
            image_path, api_key,
            provider=provider,
            api_url=api_url,
            model=model,
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
                # 更新现有持仓
                holding.layer = layer
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

        return JsonResponse({'success': True, 'created': created_count})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


@require_POST
def api_snapshot_create(request):
    """创建资产快照"""
    try:
        layers_data, total_value = _get_layers_summary()
        layer_values = {ld['name']: ld['actual_value'] for ld in layers_data}
        layer_ratios = {ld['name']: ld['actual_ratio'] for ld in layers_data}

        data = json.loads(request.body) if request.body else {}

        snapshot = Snapshot.objects.create(
            date=data.get('date') or timezone.now(),
            total_value=total_value,
            layer_values=layer_values,
            layer_ratios=layer_ratios,
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


@require_POST
def api_allocate_funds(request):
    """新增资金分配计算"""
    try:
        data = json.loads(request.body)
        new_amount = Decimal(str(data.get('amount', 0)))
        layers_data, total_value = _get_layers_summary()
        allocations = allocate_new_funds(layers_data, total_value, new_amount)
        return JsonResponse({'success': True, 'allocations': allocations})
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

        # LLM 提供商
        if 'llm_provider' in data:
            Setting.set('llm_provider', data['llm_provider'])

        # Anthropic API Key
        if 'anthropic_api_key' in data:
            Setting.set('anthropic_api_key', data['anthropic_api_key'])

        # 本地模型配置
        if 'local_api_url' in data:
            Setting.set('local_api_url', data['local_api_url'])
        if 'local_api_key' in data:
            Setting.set('local_api_key', data['local_api_key'])
        if 'local_model' in data:
            Setting.set('local_model', data['local_model'])

        # 兼容旧字段
        if 'api_key' in data:
            Setting.set('anthropic_api_key', data['api_key'])

        if 'layers' in data:
            for layer_data in data['layers']:
                layer = get_object_or_404(AssetLayer, id=layer_data['id'])
                layer.target_ratio = float(layer_data['target_ratio'])
                layer.save()

        return JsonResponse({'success': True})
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


@require_POST
def api_test_llm(request):
    """测试 LLM 连接（通过后端代理，避免浏览器跨域问题）"""
    import httpx

    try:
        data = json.loads(request.body)
        provider = data.get('provider', 'anthropic')

        if provider == 'openai_compatible':
            api_url = data.get('api_url', '')
            api_key = data.get('api_key', '')

            if not api_url:
                return JsonResponse({'success': False, 'error': '请输入 API 地址'})

            # 请求 /v1/models 端点
            url = api_url.rstrip('/')
            if url.endswith('/v1'):
                models_url = url + '/models'
            else:
                models_url = url + '/v1/models'

            headers = {}
            if api_key:
                headers['Authorization'] = f'Bearer {api_key}'

            resp = httpx.get(models_url, headers=headers, timeout=10.0)
            resp.raise_for_status()
            result = resp.json()

            models = []
            if 'data' in result:
                models = [m.get('id', '未知') for m in result['data']]

            return JsonResponse({
                'success': True,
                'models': models,
                'message': f'连接成功！可用模型: {", ".join(models) if models else "未返回模型列表"}',
            })

        elif provider == 'anthropic':
            api_key = data.get('api_key', '')
            if not api_key:
                return JsonResponse({'success': False, 'error': '请输入 API Key'})

            # 简单校验 key 格式
            if api_key.startswith('sk-ant-'):
                return JsonResponse({
                    'success': True,
                    'models': ['claude-sonnet-4-20250514'],
                    'message': 'API Key 格式正确，将在首次截图识别时验证连接',
                })
            else:
                return JsonResponse({
                    'success': False,
                    'error': 'API Key 格式不正确，应以 sk-ant- 开头',
                })

        else:
            return JsonResponse({'success': False, 'error': f'未知提供商: {provider}'})

    except httpx.ConnectError:
        return JsonResponse({'success': False, 'error': f'无法连接到 {api_url}，请检查服务是否已启动'})
    except httpx.HTTPStatusError as e:
        return JsonResponse({'success': False, 'error': f'HTTP {e.response.status_code}: {e.response.text[:200]}'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})
