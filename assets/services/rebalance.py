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


def allocate_new_funds(layers_data: list, total_value: float, new_amount: float) -> list:
    """
    新增资金分配：优先投入最低配的层级
    
    Args:
        layers_data: 各层级数据
        total_value: 当前总资产
        new_amount: 新增资金
        
    Returns:
        list of allocation suggestions
    """
    if new_amount <= 0 or total_value <= 0:
        return []

    new_total = total_value + new_amount
    allocations = []
    remaining = new_amount

    # 计算每个层级的缺口
    gaps = []
    for layer in layers_data:
        target_value = new_total * layer['target_ratio'] / 100
        current_value = layer['actual_value']
        gap = target_value - current_value
        if gap > 0:
            gaps.append({
                "id": layer['id'],
                "name": layer['name'],
                "gap": gap,
                "target_ratio": layer['target_ratio'],
            })

    # 按缺口大小排序，优先填充缺口最大的
    gaps.sort(key=lambda x: x['gap'], reverse=True)

    for gap_info in gaps:
        if remaining <= 0:
            break
        alloc = min(gap_info['gap'], remaining)
        allocations.append({
            "layer": gap_info['name'],
            "layer_id": gap_info['id'],
            "amount": round(alloc, 2),
            "reason": f"当前低配，缺口 ¥{gap_info['gap']:,.0f}",
        })
        remaining -= alloc

    # 如果还有剩余，按目标比例分配
    if remaining > 0:
        for layer in layers_data:
            alloc = remaining * layer['target_ratio'] / 100
            if alloc > 0:
                # 合并到已有分配
                existing = next((a for a in allocations if a.get('layer_id') == layer['id']), None)
                if existing:
                    existing['amount'] += round(alloc, 2)
                else:
                    allocations.append({
                        "layer": layer['name'],
                        "layer_id": layer['id'],
                        "amount": round(alloc, 2),
                        "reason": "按目标比例分配剩余资金",
                    })

    return allocations


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
