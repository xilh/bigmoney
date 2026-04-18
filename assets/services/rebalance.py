"""
再平衡计算引擎
基于《资产配置方案》文档中的规则计算再平衡建议
"""


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
        if abs(deviation) > 5:
            layer_result['status'] = 'critical'
            alerts.append({
                "level": "critical",
                "layer": layer['name'],
                "message": f"{layer['name']}偏差 {deviation:+.1f}%，超过5%阈值，建议立即调整",
                "action": f"{'卖出' if adjustment > 0 else '买入'} ¥{abs(adjustment):,.0f}",
            })
        elif abs(deviation) > 3:
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


def calculate_risk_alerts(holdings, total_value) -> list:
    """
    计算基于全部持仓的逐笔风险预警（单票超5%、卫星层止盈止损）
    
    Args:
        holdings: QuerySet of Holding
        total_value: 当前总资产市值
        
    Returns:
        list of alert dicts: [{"level": str, "source": str, "message": str, "action": str}]
    """
    alerts = []
    if total_value <= 0:
        return alerts

    for h in holdings:
        # 1. 个股集中度预警：任何单只股票仓位不超过总资产的5%
        # 仅适��于个股类资产，基金/ETF/黄金等不受此限制
        if h.asset_type in ('stock', 'dividend_stock', 'hk_stock'):
            if h.market_value and total_value > 0:
                pct = float(h.market_value) / total_value * 100
                if pct > 5.0:
                    level = "critical" if pct > 10.0 else "warning"
                    alerts.append({
                        "level": level,
                        "source": h.name,
                        "message": f"单票集中度过高（当前占比 {pct:.1f}%）",
                        "action": "突破5%天花板，建议在本月内分批减仓至总比例的5%以下。",
                    })

        # 2. 债券基金异常波动预警：总盈亏比例跌幅>1%视为异常
        if h.asset_type in ('bond_fund', 'convertible_bond') and h.profit_loss_pct:
            pl_pct = float(h.profit_loss_pct)
            if pl_pct < -3.0:
                alerts.append({
                    "level": "critical",
                    "source": h.name,
                    "message": f"债基异常波动（盈亏 {pl_pct:+.1f}%）",
                    "action": "可能信用事件或极端利率环境，审视持仓是否有信用违约风险，如有则转换为利率债基金或货币基金。",
                })
            elif pl_pct < -1.0:
                alerts.append({
                    "level": "warning",
                    "source": h.name,
                    "message": f"债基波动偏大（盈亏 {pl_pct:+.1f}%）",
                    "action": "检查原因：若为利率政策��击，可继续持有；考虑缩短久期，换入更短期限的债基。",
                })

        # 2. 第五层（卫星仓位）特有止损与止盈纪律
        # 假设 layer.order=5 为第五层 (卫星仓位)
        if hasattr(h, 'layer') and h.layer.order == 5 and h.profit_loss_pct:
            pl_pct = float(h.profit_loss_pct)
            
            # 止盈
            if pl_pct >= 200:
                alerts.append({
                    "level": "success",
                    "source": h.name,
                    "message": f"强力复利达成（盈亏 {pl_pct:+.1f}%）",
                    "action": "卖出三分之二部分，将利润回收到第一层或核心宽基层级，剩余部分继续持有。",
                })
            elif pl_pct >= 100:
                alerts.append({
                    "level": "success",
                    "source": h.name,
                    "message": f"盈利翻倍（盈亏 {pl_pct:+.1f}%）",
                    "action": "建议卖出一半收回成本，将剩余转化为「零成本仓位」，设定最高点回撤 15% 的移动止盈线。",
                })
            elif pl_pct >= 50:
                alerts.append({
                    "level": "info",
                    "source": h.name,
                    "message": f"可观利润（盈亏 {pl_pct:+.1f}%）",
                    "action": "建议设定最高点回撤 20% 的移动止盈线防坐过山车。",
                })
            
            # 止损
            if pl_pct <= -70:
                alerts.append({
                    "level": "critical",
                    "source": h.name,
                    "message": f"穿透底线（亏损 {pl_pct:+.1f}%）",
                    "action": "无条件止损！接受「学费」，切勿补仓摆平成本，不要幻想反弹。",
                })
            elif pl_pct <= -50:
                alerts.append({
                    "level": "critical",
                    "source": h.name,
                    "message": f"高危亏损（亏损 {pl_pct:+.1f}%）",
                    "action": "默认止损点已触发。除非有极其强烈的理由继续持有，否则应立刻清仓。",
                })
            elif pl_pct <= -30:
                alerts.append({
                    "level": "warning",
                    "source": h.name,
                    "message": f"逻辑验证预警（亏损 {pl_pct:+.1f}%）",
                    "action": "强制重新审视投资逻辑。若写不出有说服力的持仓理由，请立即止损。",
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
