"""
再平衡计算引擎
基于《资产配置方案》文档中的规则计算再平衡建议
"""
import logging

logger = logging.getLogger(__name__)

# ---- 可调阈值（集中管理，未来可迁移至 Setting 模型） ----
THRESHOLDS = {
    # 层级偏差
    'layer_deviation_warning': 3.0,   # %，接近阈值
    'layer_deviation_critical': 5.0,  # %，立即调整

    # 个股集中度（v3.4：单只 ≤ 总资产 3%）
    'concentration_warning': 3.0,     # %，单票占总资产上限（v3.4 红线）
    'concentration_critical': 5.0,    # %，严重超标

    # 债基异常波动
    'bond_anomaly_warning': -1.0,     # %，盈亏比例
    'bond_anomaly_critical': -3.0,    # %

    # 第三层·权益核心整体回撤协议（距峰值/累计）
    'drawdown_warning': -10.0,
    'drawdown_critical': -20.0,
    'drawdown_extreme': -30.0,
}


def calculate_rebalance(layers_data: list, total_value: float) -> dict:
    """
    计算再平衡建议
    
    Args:
        layers_data: [{"id": int, "name": str, "target_ratio": float, "actual_value": float}]
        total_value: 总资产市值
        
    Returns:
        dict with analysis results
    """
    if total_value <= 0:
        return {
            "total_value": 0,
            "layers": [],
            "alerts": [],
            "suggestions": [],
        }

    results = []
    alerts = []
    suggestions = []

    for layer in layers_data:
        target_ratio = layer['target_ratio']
        actual_value = layer['actual_value']
        actual_ratio = (actual_value / total_value * 100) if total_value > 0 else 0
        deviation = actual_ratio - target_ratio
        target_value = total_value * target_ratio / 100
        adjustment = actual_value - target_value  # 正=需卖出, 负=需买入

        layer_result = {
            "id": layer['id'],
            "name": layer['name'],
            "target_ratio": target_ratio,
            "actual_ratio": round(actual_ratio, 2),
            "actual_value": actual_value,
            "target_value": round(target_value, 2),
            "deviation": round(deviation, 2),
            "adjustment": round(adjustment, 2),
            "status": "balanced",
        }

        # 判断偏差状态
        if abs(deviation) > THRESHOLDS['layer_deviation_critical']:
            layer_result['status'] = 'critical'
            alerts.append({
                "level": "critical",
                "layer": layer['name'],
                "message": f"{layer['name']}偏差 {deviation:+.1f}%，超过{THRESHOLDS['layer_deviation_critical']:.0f}%阈值，建议立即调整",
                "action": f"{'卖出' if adjustment > 0 else '买入'} ¥{abs(adjustment):,.0f}",
            })
        elif abs(deviation) > THRESHOLDS['layer_deviation_warning']:
            layer_result['status'] = 'warning'
            alerts.append({
                "level": "warning",
                "layer": layer['name'],
                "message": f"{layer['name']}偏差 {deviation:+.1f}%，接近阈值，关注中",
                "action": "可在年度再平衡时处理",
            })
        
        results.append(layer_result)

    # 生成建议
    over_layers = [r for r in results if r['adjustment'] > 0]
    under_layers = [r for r in results if r['adjustment'] < 0]
    
    if over_layers:
        over_layers.sort(key=lambda x: x['adjustment'], reverse=True)
        for layer in over_layers:
            suggestions.append({
                "type": "sell",
                "layer": layer['name'],
                "amount": layer['adjustment'],
                "message": f"从{layer['name']}减持 ¥{layer['adjustment']:,.0f}",
            })
    
    if under_layers:
        under_layers.sort(key=lambda x: x['adjustment'])
        for layer in under_layers:
            suggestions.append({
                "type": "buy",
                "layer": layer['name'],
                "amount": abs(layer['adjustment']),
                "message": f"向{layer['name']}增持 ¥{abs(layer['adjustment']):,.0f}",
            })

    return {
        "total_value": total_value,
        "layers": results,
        "alerts": alerts,
        "suggestions": suggestions,
    }


def build_market_history(snapshots, holdings=None, today=None):
    """
    从历史快照构建用于「口径校准」的市场历史，使预警基于 **层级从峰值回撤 / 持仓近一月变动**，
    而非累计成本盈亏（更贴合方案中「跌幅」「单月跌幅」的语义）。

    层级峰值按 **order** 聚合（取快照 holdings_data 中各持仓的 layer_order；旧快照若无该字段，
    则用当前持仓的 id→order 回退）。如此对「层级改名 / 持仓换层」均稳健，不依赖历史层名。

    Args:
        snapshots: 可迭代的快照对象，需含 .date / .holdings_data
        holdings: 当前持仓（用于旧快照缺 layer_order 时按 id 回退到当前 order）
        today: date，默认今日（本地）

    Returns:
        {
            'holding_month_ago_pct': {name: 约30天前的累计盈亏%},
            'layer_peak_value': {order(int): 历史最高层级市值},
            'has_data': bool,
        }
    """
    out = {
        'holding_month_ago_pct': {},
        'layer_peak_value': {},
        'has_data': False,
    }
    snaps = list(snapshots or [])
    if not snaps:
        return out
    out['has_data'] = True

    if today is None:
        try:
            from django.utils import timezone
            today = timezone.localdate()
        except Exception:
            pass

    id_to_order = {h.id: h.layer.order for h in (holdings or [])}

    # 层级峰值（按 order 聚合，含全部历史快照）
    for s in snaps:
        per_order = {}
        for h in (s.holdings_data or []):
            order = h.get('layer_order')
            if order is None:
                order = id_to_order.get(h.get('id'))
            if order is None:
                continue
            per_order[order] = per_order.get(order, 0.0) + float(h.get('market_value') or 0)
        for order, val in per_order.items():
            if val > out['layer_peak_value'].get(order, 0):
                out['layer_peak_value'][order] = val

    # 约 30 天前的快照（用于「单月」口径）：取距今 [7, 60] 天内最接近 30 天的一张
    if today is not None:
        best = None
        best_gap = None
        for s in snaps:
            sdate = s.date.date() if hasattr(s.date, 'date') else s.date
            days_ago = (today - sdate).days
            if days_ago < 7 or days_ago > 60:
                continue
            gap = abs(days_ago - 30)
            if best_gap is None or gap < best_gap:
                best, best_gap = s, gap
        if best is not None:
            for h in (best.holdings_data or []):
                name = h.get('name')
                if name:
                    out['holding_month_ago_pct'][name] = float(h.get('profit_loss_pct') or 0)

    return out


def calculate_risk_alerts(holdings, total_value, acknowledged_keys=None, market_history=None) -> list:
    """
    计算基于全部持仓的逐笔风险预警（v3.4：单只>3%集中度、债基异常、第三层回撤协议）

    Args:
        holdings: QuerySet/list of Holding (需 select_related('layer'))
        total_value: 当前总资产市值
        acknowledged_keys: set of "holding_id:alert_type" strings to skip
        market_history: build_market_history() 的返回值。提供后，债基异常按「近一月」、
            第三层回撤按「距峰值」判断；不提供则回退到累计盈亏口径。

    Returns:
        list of alert dicts, each with: level, source, message, action, alert_type, holding_id
    """
    alerts = []
    if total_value <= 0:
        return alerts

    acked = acknowledged_keys or set()
    mh = market_history or {}
    month_ago_pct = mh.get('holding_month_ago_pct', {})
    layer_peak = mh.get('layer_peak_value', {})   # keyed by layer order (int)

    def _add(level, source, message, action, alert_type, holding_id):
        key = f"{holding_id}:{alert_type}"
        if key not in acked:
            alerts.append({
                "level": level,
                "source": source,
                "message": message,
                "action": action,
                "alert_type": alert_type,
                "holding_id": holding_id,
            })

    # 干火药统计（Layer 1 is_reserve 标记的持仓）
    reserve_value = sum(
        float(h.market_value or 0) for h in holdings
        if h.layer.order == 1 and h.is_reserve
    )

    # 按层级汇总盈亏（用于回撤协议判断）。
    #   优先用「距历史峰值的回撤」（方案「跌幅」语义）；无快照历史时回退到累计成本盈亏。
    layer3_holdings = [h for h in holdings if h.layer.order == 3]
    layer3_total_pl_pct = 0.0          # 负数表示亏损/回撤，复用既有阈值
    layer3_basis = "累计"
    if layer3_holdings:
        layer3_cur_value = sum(float(h.market_value or 0) for h in layer3_holdings)
        l3_peak = layer_peak.get(3, 0)   # 按 order 取峰值，不依赖层名
        if l3_peak > 0 and layer3_cur_value > 0 and l3_peak > layer3_cur_value:
            # 距峰值回撤（转为负数以复用阈值）
            layer3_total_pl_pct = -((l3_peak - layer3_cur_value) / l3_peak * 100)
            layer3_basis = "距峰值"
        else:
            layer3_cost = sum(
                float((h.cost_price or 0) * h.quantity) for h in layer3_holdings
                if h.cost_price and h.quantity
            )
            layer3_pl = sum(float(h.profit_loss or 0) for h in layer3_holdings)
            if layer3_cost > 0:
                layer3_total_pl_pct = layer3_pl / layer3_cost * 100

    for h in holdings:
        h_id = h.id

        # 1. 个股集中度预警（v3.4：单只 ≤ 总资产 3%）
        if h.asset_type in ('stock', 'dividend_stock', 'hk_stock'):
            if h.market_value and total_value > 0:
                pct = float(h.market_value) / total_value * 100
                if pct > THRESHOLDS['concentration_warning']:
                    level = "critical" if pct > THRESHOLDS['concentration_critical'] else "warning"
                    _add(level, h.name,
                         f"单票集中度过高（当前占比 {pct:.1f}%）",
                         f"突破 v3.4 单只 {THRESHOLDS['concentration_warning']:.0f}% 红线，分批减仓至总资产 {THRESHOLDS['concentration_warning']:.0f}% 以下。",
                         "concentration_cap", h_id)

        # 2. 债券基金异常波动预警
        #    优先用「近一月变动」（方案口径：单月跌幅>1%/3%）；无快照历史时回退到累计盈亏。
        if h.asset_type in ('bond_fund', 'convertible_bond'):
            if h.name in month_ago_pct and h.profit_loss_pct is not None:
                metric = float(h.profit_loss_pct) - month_ago_pct[h.name]  # 近一月百分点变动
                basis_label = "近一月"
            elif h.profit_loss_pct:
                metric = float(h.profit_loss_pct)  # 回退：累计
                basis_label = "累计"
            else:
                metric = None
            if metric is not None:
                if metric < THRESHOLDS['bond_anomaly_critical']:
                    _add("critical", h.name,
                         f"债基异常波动（{basis_label} {metric:+.1f}%）",
                         "可能信用事件或极端利率环境，审视持仓是否有信用违约风险，如有则转换为利率债基金或货币基金。",
                         "bond_anomaly_3pct", h_id)
                elif metric < THRESHOLDS['bond_anomaly_warning']:
                    _add("warning", h.name,
                         f"债基波动偏大（{basis_label} {metric:+.1f}%）",
                         "检查原因：若为利率政策冲击，可继续持有；考虑缩短久期，换入更短期限的债基。",
                         "bond_anomaly_1pct", h_id)

        # （v3.4：原「第五层卫星仓位」已重定义为「全球分散」纯DCA层，
        #   不再适用个股式止盈止损阶梯；精选个股退出改由「卖出五大信号 + 决策日志」管理。）

    # 4. 第三层·权益核心整体回撤 → 下跌应对建议
    if layer3_total_pl_pct <= THRESHOLDS['drawdown_extreme']:
        reserve_hint = f"（当前干火药储备 ¥{reserve_value:,.0f}）" if reserve_value > 0 else "（未标记干火药储备）"
        alerts.append({
            "level": "critical",
            "source": "第三层·权益核心",
            "message": f"极端恐慌回撤（{layer3_basis} {layer3_total_pl_pct:.1f}%）",
            "action": f"历史性买入机会！动用全部干火药分批加仓宽基(中证A500)，分3-4批、每批间隔1-2周。{reserve_hint}",
            "alert_type": "drawdown_30", "holding_id": None,
        })
    elif layer3_total_pl_pct <= THRESHOLDS['drawdown_critical']:
        reserve_hint = f"（当前干火药储备 ¥{reserve_value:,.0f}）" if reserve_value > 0 else "（未标记干火药储备）"
        alerts.append({
            "level": "critical",
            "source": "第三层·权益核心",
            "message": f"显著回撤（{layer3_basis} {layer3_total_pl_pct:.1f}%）",
            "action": f"动用第一层干火药储备分三批加仓指数基金，1-2周内启动第一批。{reserve_hint}",
            "alert_type": "drawdown_20", "holding_id": None,
        })
    elif layer3_total_pl_pct <= THRESHOLDS['drawdown_warning']:
        alerts.append({
            "level": "warning",
            "source": "第三层·权益核心",
            "message": f"明显调整（{layer3_basis} {layer3_total_pl_pct:.1f}%）",
            "action": "检查各层级偏差，如触发5%偏差则执行触发式再平衡，季度检视时处理。",
            "alert_type": "drawdown_10", "holding_id": None,
        })

    return alerts


def generate_investment_plan(layers_data, holdings, total_value, rebalance_result):
    """
    基于再平衡分析结果，生成具体的投资执行计划。

    Returns:
        list of plan items, each with: priority, timeline, layer, actions (list), total_amount, reason
    """
    if total_value <= 0:
        return []

    plan = []
    layer_map = {ld['name']: ld for ld in layers_data}

    # 按层级分组持仓
    holdings_by_layer = {}
    for h in holdings:
        layer_name = h.layer.name
        holdings_by_layer.setdefault(layer_name, []).append(h)

    for layer_result in rebalance_result.get('layers', []):
        adjustment = layer_result['adjustment']
        if abs(adjustment) < 100:
            continue

        layer_name = layer_result['name']
        layer_holdings = holdings_by_layer.get(layer_name, [])
        deviation = abs(layer_result['deviation'])

        # 确定优先级和时间线
        if layer_result['status'] == 'critical':
            priority = 1
            timeline = '本周内'
            urgency = 'urgent'
        elif layer_result['status'] == 'warning':
            priority = 2
            timeline = '本月内'
            urgency = 'normal'
        else:
            priority = 3
            timeline = '下次季度检视'
            urgency = 'low'

        actions = []

        if adjustment > 0:
            # 需要卖出 — 从该层最大持仓开始
            remaining = adjustment
            sorted_holdings = sorted(layer_holdings, key=lambda h: float(h.market_value or 0), reverse=True)
            for h in sorted_holdings:
                if remaining <= 0:
                    break
                mv = float(h.market_value or 0)
                if mv <= 0:
                    continue
                sell_amount = min(remaining, mv)
                sell_pct = sell_amount / mv * 100
                actions.append({
                    'type': 'sell',
                    'asset_name': h.name,
                    'asset_code': h.code or '',
                    'platform': h.platform or '',
                    'amount': round(sell_amount, 0),
                    'current_value': round(mv, 0),
                    'pct_of_holding': round(sell_pct, 1),
                    'profit_loss_pct': float(h.profit_loss_pct or 0),
                })
                remaining -= sell_amount

            plan.append({
                'priority': priority,
                'timeline': timeline,
                'urgency': urgency,
                'direction': 'sell',
                'layer': layer_name,
                'deviation': round(layer_result['deviation'], 1),
                'total_amount': round(adjustment, 0),
                'actions': actions,
                'reason': f'{layer_name}超配 {layer_result["deviation"]:+.1f}%，需减持 ¥{adjustment:,.0f} 回到目标比例',
            })

        else:
            # 需要买入 — 推荐已有持仓中加仓或层级默认标的
            buy_amount = abs(adjustment)
            sorted_holdings = sorted(layer_holdings, key=lambda h: float(h.market_value or 0), reverse=True)

            if sorted_holdings:
                # 分配买入金额到现有持仓（按市值权重）
                total_layer_value = sum(float(h.market_value or 0) for h in sorted_holdings)
                for h in sorted_holdings:
                    mv = float(h.market_value or 0)
                    if total_layer_value > 0:
                        weight = mv / total_layer_value
                    else:
                        weight = 1.0 / len(sorted_holdings)
                    alloc = buy_amount * weight
                    if alloc < 100:
                        continue
                    actions.append({
                        'type': 'buy',
                        'asset_name': h.name,
                        'asset_code': h.code or '',
                        'platform': h.platform or '',
                        'amount': round(alloc, 0),
                        'current_value': round(mv, 0),
                        'profit_loss_pct': float(h.profit_loss_pct or 0),
                    })
            else:
                # 该层无持仓，给出层级级别的建议
                actions.append({
                    'type': 'buy',
                    'asset_name': f'{layer_name}（待选标的）',
                    'asset_code': '',
                    'platform': '',
                    'amount': round(buy_amount, 0),
                    'current_value': 0,
                    'profit_loss_pct': 0,
                })

            plan.append({
                'priority': priority,
                'timeline': timeline,
                'urgency': urgency,
                'direction': 'buy',
                'layer': layer_name,
                'deviation': round(layer_result['deviation'], 1),
                'total_amount': round(buy_amount, 0),
                'actions': actions,
                'reason': f'{layer_name}低配 {layer_result["deviation"]:+.1f}%，需增持 ¥{buy_amount:,.0f} 回到目标比例',
            })

    # 按优先级排序
    plan.sort(key=lambda x: x['priority'])
    return plan


# 极端情景应对手册（v3.4 §六，5类情景触发表）
DRAWDOWN_PROTOCOLS = {
    "a_share_crash": {
        "name": "A股单日跌幅 ≥ 5%",
        "rules": [
            {"threshold": "触发", "action": "不恐慌卖出。检查各层级偏差，若 T3 偏离触发再平衡则按机械规则加仓宽基(中证A500)。DCA 继续，不停。"},
        ],
    },
    "gold_swing": {
        "name": "黄金单周波动 ≥ 8%",
        "rules": [
            {"threshold": "触发", "action": "回到黄金双指标判断表（实质金价分位 + Gold/SPX 比价分位），按状态决定建仓/暂停/减配，不凭单周波动操作。"},
        ],
    },
    "single_holding_drop": {
        "name": "单一持仓单日跌 ≥ 10%",
        "rules": [
            {"threshold": "触发", "action": "对照「卖出五大信号」核验投资逻辑是否破坏；逻辑未变→持有/按计划，逻辑变→按退出条件分批处理。先看基本面，不看当日情绪。"},
        ],
    },
    "bond_redemption": {
        "name": "T2 固收增强债基赎回潮 ≥ 5%",
        "rules": [
            {"threshold": "触发", "action": "审视是否信用事件或极端利率环境；若信用风险则转利率债/货基，若纯利率冲击可缩短久期后继续持有。"},
        ],
    },
    "fx_swing": {
        "name": "美元/人民币单月波动 ≥ 3%",
        "rules": [
            {"threshold": "触发", "action": "影响 T5 全球分散的汇率分位。人民币强势区正常 DCA 配置 QDII/港股通；人民币弱势区放缓新增换汇节奏，不追汇率。"},
        ],
    },
}


# 分级 DCA 规则表（v3.4 §5.2.1）
DCA_RULES = [
    {"asset": "宽基指数 (中证A500)", "mode": "纯DCA", "freq": "季度", "rule": "PE分位 > 90% 降频 50%", "asset_types": ["index_fund"]},
    {"asset": "红利低波 (512890)", "mode": "半估值触发", "freq": "季度", "rule": "股息率分位：>60% 正常DCA / 30-60% 降频 / <30% 暂停（v3.4实证 4.90%，约60-75%分位 → 正常DCA）", "asset_types": ["dividend_stock"]},
    {"asset": "黄金 (华安518880)", "mode": "双指标触发", "freq": "每月", "rule": "实质金价分位 + Gold/SPX 比价分位（当前 A:80-85% / B:50-60% → 暂停建仓）", "asset_types": ["gold"]},
    {"asset": "纳指/科技ETF", "mode": "估值触发", "freq": "每月", "rule": "Shiller PE 分位", "asset_types": ["qdii"]},
    {"asset": "精选个股", "mode": "估值触发 + 买入门槛", "freq": "季度", "rule": "PE/PB分位 + 五项买入门槛", "asset_types": ["stock"]},
    {"asset": "港股通 / QDII", "mode": "纯DCA", "freq": "季度核对汇率分位", "rule": "人民币强势区正常 DCA", "asset_types": ["hk_stock"]},
]


# 黄金双指标判断（v3.4 §2.3，5状态）
GOLD_DUAL_INDICATOR = {
    "name": "黄金双指标规则",
    "indicators": ["指标A：实质金价（剔除通胀）分位", "指标B：Gold/SPX 比价分位"],
    "current": "当前评估 A约80-85%、B约50-60% → ⏸ 暂停建仓",
    "states": [
        {"state": "A低 + B低", "action": "✅ 积极建仓（黄金便宜且相对股票便宜）"},
        {"state": "A低 + B高", "action": "✅ 正常DCA"},
        {"state": "A中 + B中", "action": "⚠ 正常DCA，观察"},
        {"state": "A高 + B低", "action": "⚠ 降频，相对股票仍有价值"},
        {"state": "A高 + B高", "action": "⏸ 暂停建仓（黄金贵且相对股票也贵）"},
    ],
}


# 精选个股买入门槛（v3.4 §2.2.2，5项必须全部满足）
STOCK_BUY_GATES = [
    "研究报告：至少一页 A4 买入理由（可手写可电子）",
    "估值依据：PE/PB/股息率分位明确，或有清晰 DCF 逻辑",
    "行业约束：不突破单一申万一级行业 ≤ 精选个股层 50%",
    "仓位上限：单只 ≤ 总资产 3%",
    "退出条件：买入前写清卖出信号（对应卖出五大信号的具体化）",
]
