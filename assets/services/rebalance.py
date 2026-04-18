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

    # 个股集中度
    'concentration_warning': 5.0,     # %，单票占总资产上限
    'concentration_critical': 10.0,   # %，严重超标

    # 债基异常波动
    'bond_anomaly_warning': -1.0,     # %，盈亏比例
    'bond_anomaly_critical': -3.0,    # %

    # 卫星仓位止盈阶梯
    'satellite_tp_50': 50.0,
    'satellite_tp_100': 100.0,
    'satellite_tp_200': 200.0,

    # 卫星仓位止损阶梯
    'satellite_sl_30': -30.0,
    'satellite_sl_50': -50.0,
    'satellite_sl_70': -70.0,

    # 第三层整体回撤协议
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


def calculate_risk_alerts(holdings, total_value, acknowledged_keys=None) -> list:
    """
    计算基于全部持仓的逐笔风险预警（单票超5%、债基异常、卫星层止盈止损、回撤协议）

    Args:
        holdings: QuerySet/list of Holding (需 select_related('layer'))
        total_value: 当前总资产市值
        acknowledged_keys: set of "holding_id:alert_type" strings to skip

    Returns:
        list of alert dicts, each with: level, source, message, action, alert_type, holding_id
    """
    alerts = []
    if total_value <= 0:
        return alerts

    acked = acknowledged_keys or set()

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

    # 按层级汇总持仓盈亏（用于回撤协议判断）
    layer3_holdings = [h for h in holdings if h.layer.order == 3]
    layer3_total_pl_pct = 0.0
    if layer3_holdings:
        layer3_cost = sum(
            float((h.cost_price or 0) * h.quantity) for h in layer3_holdings
            if h.cost_price and h.quantity
        )
        layer3_pl = sum(float(h.profit_loss or 0) for h in layer3_holdings)
        if layer3_cost > 0:
            layer3_total_pl_pct = layer3_pl / layer3_cost * 100

    for h in holdings:
        h_id = h.id

        # 1. 个股集中度预警
        if h.asset_type in ('stock', 'dividend_stock', 'hk_stock'):
            if h.market_value and total_value > 0:
                pct = float(h.market_value) / total_value * 100
                if pct > THRESHOLDS['concentration_warning']:
                    level = "critical" if pct > THRESHOLDS['concentration_critical'] else "warning"
                    _add(level, h.name,
                         f"单票集中度过高（当前占比 {pct:.1f}%）",
                         f"突破{THRESHOLDS['concentration_warning']:.0f}%天花板，建议在本月内分批减仓至总比例的{THRESHOLDS['concentration_warning']:.0f}%以下。",
                         "concentration_5pct", h_id)

        # 2. 债券基金异常波动预警
        if h.asset_type in ('bond_fund', 'convertible_bond') and h.profit_loss_pct:
            pl_pct = float(h.profit_loss_pct)
            if pl_pct < THRESHOLDS['bond_anomaly_critical']:
                _add("critical", h.name,
                     f"债基异常波动（盈亏 {pl_pct:+.1f}%）",
                     "可能信用事件或极端利率环境，审视持仓是否有信用违约风险，如有则转换为利率债基金或货币基金。",
                     "bond_anomaly_3pct", h_id)
            elif pl_pct < THRESHOLDS['bond_anomaly_warning']:
                _add("warning", h.name,
                     f"债基波动偏大（盈亏 {pl_pct:+.1f}%）",
                     "检查原因：若为利率政策冲击，可继续持有；考虑缩短久期，换入更短期限的债基。",
                     "bond_anomaly_1pct", h_id)

        # 3. 第五层（卫星仓位）止盈止损纪律
        if hasattr(h, 'layer') and h.layer.order == 5 and h.profit_loss_pct:
            pl_pct = float(h.profit_loss_pct)

            # 止盈（取最高档，不重复）
            if pl_pct >= THRESHOLDS['satellite_tp_200']:
                _add("success", h.name,
                     f"强力复利达成（盈亏 {pl_pct:+.1f}%）",
                     "卖出三分之二部分，将利润回收到第一层或核心宽基层级，剩余部分继续持有。",
                     "satellite_tp_200", h_id)
            elif pl_pct >= THRESHOLDS['satellite_tp_100']:
                _add("success", h.name,
                     f"盈利翻倍（盈亏 {pl_pct:+.1f}%）",
                     "建议卖出一半收回成本，将剩余转化为「零成本仓位」，设定最高点回撤 15% 的移动止盈线。",
                     "satellite_tp_100", h_id)
            elif pl_pct >= THRESHOLDS['satellite_tp_50']:
                _add("info", h.name,
                     f"可观利润（盈亏 {pl_pct:+.1f}%）",
                     "建议设定最高点回撤 20% 的移动止盈线防坐过山车。",
                     "satellite_tp_50", h_id)

            # 止损（取最严重档，不重复）
            if pl_pct <= THRESHOLDS['satellite_sl_70']:
                _add("critical", h.name,
                     f"穿透底线（亏损 {pl_pct:+.1f}%）",
                     "无条件止损！接受「学费」，切勿补仓摆平成本，不要幻想反弹。",
                     "satellite_sl_70", h_id)
            elif pl_pct <= THRESHOLDS['satellite_sl_50']:
                _add("critical", h.name,
                     f"高危亏损（亏损 {pl_pct:+.1f}%）",
                     "默认止损点已触发。除非有极其强烈的理由继续持有，否则应立刻清仓。",
                     "satellite_sl_50", h_id)
            elif pl_pct <= THRESHOLDS['satellite_sl_30']:
                _add("warning", h.name,
                     f"逻辑验证预警（亏损 {pl_pct:+.1f}%）",
                     "强制重新审视投资逻辑。若写不出有说服力的持仓理由，请立即止损。",
                     "satellite_sl_30", h_id)

    # 4. 第三层整体回撤 → 下跌应对协议自动建议
    if layer3_total_pl_pct <= THRESHOLDS['drawdown_extreme']:
        reserve_hint = f"（当前干火药储备 ¥{reserve_value:,.0f}）" if reserve_value > 0 else "（未标记干火药储备）"
        alerts.append({
            "level": "critical",
            "source": "第三层·股票核心",
            "message": f"极端恐慌回撤（整体亏损 {layer3_total_pl_pct:.1f}%）",
            "action": f"历史性买入机会！动用全部干火药及第五层现金加仓指数基金，分3-4批、每批间隔1-2周。{reserve_hint}",
            "alert_type": "drawdown_30", "holding_id": None,
        })
    elif layer3_total_pl_pct <= THRESHOLDS['drawdown_critical']:
        reserve_hint = f"（当前干火药储备 ¥{reserve_value:,.0f}）" if reserve_value > 0 else "（未标记干火药储备）"
        alerts.append({
            "level": "critical",
            "source": "第三层·股票核心",
            "message": f"显著回撤（整体亏损 {layer3_total_pl_pct:.1f}%）",
            "action": f"动用第一层干火药储备分三批加仓指数基金，1-2周内启动第一批。{reserve_hint}",
            "alert_type": "drawdown_20", "holding_id": None,
        })
    elif layer3_total_pl_pct <= THRESHOLDS['drawdown_warning']:
        alerts.append({
            "level": "warning",
            "source": "第三层·股票核心",
            "message": f"明显调整（整体亏损 {layer3_total_pl_pct:.1f}%）",
            "action": "检查各层级偏差，如触发5%偏差则执行触发式再平衡，季度检视时处理。",
            "alert_type": "drawdown_10", "holding_id": None,
        })

    return alerts


# 下跌应对协议数据（基于文档第三节）
DRAWDOWN_PROTOCOLS = {
    "layer_1_2": {
        "name": "第一层/第二层（安全垫 + 债券）",
        "rules": [
            {"threshold": "单月跌幅 >1%", "action": "检查原因。如为利率政策冲击（央行加息），继续持有。可考虑缩短久期，换入更短期限的债基。"},
            {"threshold": "单月跌幅 >3%", "action": "可能信用事件或极端利率环境。审视基金持仓是否有信用违约风险，如有则立即转换为利率债基金或货币基金。"},
        ],
    },
    "layer_3": {
        "name": "第三层（股票核心）",
        "rules": [
            {"threshold": "跌幅 0–10%", "action": "正常波动，市场噪音。不做任何操作，DCA阶段继续执行定投。"},
            {"threshold": "跌幅 10–20%", "action": "明显调整，但仍属常见范围。检查各层级偏差，如触发5%偏差则执行触发式再平衡。季度检视时处理。"},
            {"threshold": "跌幅 20–30%", "action": "显著回撤，A股每3–5年会发生一次。动用第一层「干火药」储备加仓指数基金，分三批投入。发现后1–2周内启动第一批。"},
            {"threshold": "跌幅 >30%", "action": "极端恐慌，历史性买入机会。将第五层现金也加仓至指数基金。「别人恐惧我贪婪」。分3–4批，每批间隔1–2周。"},
        ],
    },
    "layer_4": {
        "name": "第四层（黄金/港股）",
        "rules": [
            {"threshold": "黄金单独下跌 15–20%", "action": "正常波动。持有不动。黄金的价值在于与股票的低相关性，而非短期回报。"},
            {"threshold": "黄金与股票同时下跌", "action": "可能是流动性危机（类似2020年初）。继续持有，黄金通常最先反弹。"},
            {"threshold": "黄金跌而股票涨", "action": "正常的资产轮动，组合正在按设计运行。不需操作。"},
        ],
    },
    "layer_5": {
        "name": "第五层（卫星仓位）",
        "rules": [
            {"threshold": "单笔投资跌 40%+", "action": "问自己：投资逻辑是否已变？如果没变→继续持有或小幅加仓。如果变了→果断止损。"},
            {"threshold": "单笔投资跌 70%+", "action": "基本确认论点失败。止损或接受为「学费」，不要补仓摊平成本。"},
        ],
    },
}
