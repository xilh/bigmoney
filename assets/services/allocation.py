"""
子配置与行业暴露分析（对齐《资产配置方案 v3.4》§2.2 / §2.4 / §2.2.1）。

1. T3 权益核心子配置：宽基 50-60% / 红利·价值锚 25-35% / 精选个股 10-20%（占本层）
2. T4 另类对冲：黄金 8-12%（占总资产）
3. T5 全球分散子配置：港股通 30-40% / QDII 50-60% / 主题 ≤10%（占本层）
   - QDII 内部细分（§2.4.2）为参考指引（数据模型无法按 asset_type 区分标普/纳指，故仅展示）
4. 行业集中度：单一申万一级行业 ≤ 精选个股层市值的 50%（§2.2.1），落地「职业风险脱钩」
"""

# 子配置目标。basis='layer' 占本层比例；basis='total' 占总资产比例。每项以 [min,max] 区间判断。
# 子类别 key 与 SUB_CATEGORY_CHOICES 的取值一致；分类经 classify_subkey()（显式 sub_category 优先）。
SUB_ALLOCATION = {
    3: {
        'layer_name': '第三层·权益核心',
        'basis': 'layer',
        'targets': [
            {'key': 'broad',    'name': '宽基指数',    'min': 50, 'max': 60},
            {'key': 'dividend', 'name': '红利/价值锚', 'min': 25, 'max': 35},
            {'key': 'pick',     'name': '精选个股',    'min': 10, 'max': 20},
        ],
    },
    4: {
        'layer_name': '第四层·另类对冲',
        'basis': 'total',
        'targets': [
            {'key': 'gold', 'name': '黄金', 'min': 8, 'max': 12},
        ],
    },
    5: {
        'layer_name': '第五层·全球分散',
        'basis': 'layer',
        'targets': [
            {'key': 'hk',    'name': '港股通',     'min': 30, 'max': 40},
            {'key': 'qdii',  'name': 'QDII(全球)', 'min': 50, 'max': 60},
            {'key': 'theme', 'name': '主题类',     'min': 0,  'max': 10},
        ],
    },
}

# QDII 内部细分参考（v3.4 §2.4.2）——纯指引，不参与自动校验
QDII_INTERNAL = {
    'name': 'QDII 内部细分（参考指引）',
    'note': 'QDII 首要功能是「全球真分散」，不是全押美股科技。建仓优先级：标普500 → 纳指 → 其他市场 → 美元债。',
    'targets': [
        {'name': '美股宽基（标普500/全球宽基）', 'range': '40-50%'},
        {'name': '美股成长（纳指）',             'range': '20-30%'},
        {'name': '其他市场（欧股/亚太除日/新兴）', 'range': '20-30%'},
        {'name': '美元债',                       'range': '0-10%'},
    ],
}

# 行业集中度阈值（占精选个股层比例）
INDUSTRY_PICK_WARNING = 40.0
INDUSTRY_PICK_CRITICAL = 50.0   # v3.4 红线：单一申万一级 ≤ 个股层 50%

# 视为「权益类」的资产类型（行业脱钩原则主要针对股票仓位）
_EQUITY_TYPES = ('stock', 'dividend_stock', 'hk_stock', 'index_fund', 'etf')

# asset_type → 子类别 key 的回退映射（仅当 Holding.sub_category 留空时使用）。
# 按层 order 区分，因为同一 asset_type 在不同层含义不同（如 etf 在 T3=宽基、在 T5=主题）。
_ASSET_TYPE_TO_SUBKEY = {
    3: {'index_fund': 'broad', 'etf': 'broad', 'dividend_stock': 'dividend', 'stock': 'pick'},
    4: {'gold': 'gold'},
    5: {'hk_stock': 'hk', 'qdii': 'qdii', 'etf': 'theme'},
}


def classify_subkey(h):
    """返回持仓的层内子类别 key。优先用显式 sub_category，否则按 asset_type 回退。"""
    if getattr(h, 'sub_category', ''):
        return h.sub_category
    return _ASSET_TYPE_TO_SUBKEY.get(h.layer.order, {}).get(h.asset_type)


def is_pick(h):
    """是否属于「精选个股」（计入单行业≤50%红线）。"""
    return classify_subkey(h) == 'pick' and h.layer.order == 3


def _f(v):
    return float(v or 0)


def calculate_sub_allocation(holdings, total_value):
    """
    计算 T3/T4/T5 子配置实际占比与区间状态。

    Returns:
        list（每层一项）：{layer_order, layer_name, basis, base_value, layer_value, items[], alerts[]}
        item: {key, name, min, max, target(中值), actual_value, actual_ratio, status: low|high|balanced|info}
    """
    total_value = _f(total_value)
    results = []
    if total_value <= 0:
        return results

    holdings_by_order = {}
    for h in holdings:
        holdings_by_order.setdefault(h.layer.order, []).append(h)

    for order, cfg in SUB_ALLOCATION.items():
        layer_holdings = holdings_by_order.get(order, [])
        layer_value = sum(_f(h.market_value) for h in layer_holdings)
        base_value = layer_value if cfg['basis'] == 'layer' else total_value

        items = []
        alerts = []
        classified = 0.0

        for tgt in cfg['targets']:
            sub_value = sum(_f(h.market_value) for h in layer_holdings if classify_subkey(h) == tgt['key'])
            classified += sub_value
            actual_ratio = (sub_value / base_value * 100) if base_value > 0 else 0.0
            lo, hi = tgt['min'], tgt['max']

            status = 'balanced'
            if layer_value > 0:   # 本层有资产才判断
                if actual_ratio < lo:
                    status = 'low'
                    alerts.append({
                        'level': 'warning',
                        'message': f"{tgt['name']} {actual_ratio:.1f}%，低于目标区间 {lo:.0f}-{hi:.0f}%",
                        'action': f"增配至 {lo:.0f}-{hi:.0f}%",
                    })
                elif actual_ratio > hi:
                    status = 'high'
                    alerts.append({
                        'level': 'warning',
                        'message': f"{tgt['name']} {actual_ratio:.1f}%，高于目标区间 {lo:.0f}-{hi:.0f}%",
                        'action': f"减配回 {lo:.0f}-{hi:.0f}%",
                    })

            items.append({
                'key': tgt['key'], 'name': tgt['name'],
                'min': lo, 'max': hi, 'target': round((lo + hi) / 2, 1),
                'actual_value': round(sub_value, 2),
                'actual_ratio': round(actual_ratio, 2),
                'status': status,
            })

        unclassified = layer_value - classified
        if layer_value > 0 and unclassified > 1:
            unclassified_ratio = unclassified / (layer_value if cfg['basis'] == 'layer' else total_value) * 100
            items.append({
                'key': 'other', 'name': '其他/未归类', 'min': None, 'max': None, 'target': None,
                'actual_value': round(unclassified, 2),
                'actual_ratio': round(unclassified_ratio, 2), 'status': 'info',
            })

        results.append({
            'layer_order': order,
            'layer_name': cfg['layer_name'],
            'basis': cfg['basis'],
            'base_value': round(base_value, 2),
            'layer_value': round(layer_value, 2),
            'items': items,
            'alerts': alerts,
        })

    return results


def calculate_industry_exposure(holdings, total_value):
    """
    行业集中度（v3.4 §2.2.1：单一申万一级行业 ≤ 精选个股层市值的 50%），并附整体行业分布。

    Returns:
        {
            'total_value', 'equity_value', 'picks_value',
            'picks_industries': [{industry, value, pct_of_picks}],   # 精选个股层内
            'exposures': [{industry, value, pct_of_total, pct_of_equity}],  # 全组合（参考）
            'unclassified_picks': float,   # 精选个股中未标注行业的市值
            'unclassified_equity': float,
            'alerts': [{level, message, action}],
        }
    """
    total_value = _f(total_value)
    out = {
        'total_value': total_value, 'equity_value': 0.0, 'picks_value': 0.0,
        'picks_industries': [], 'exposures': [],
        'unclassified_picks': 0.0, 'unclassified_equity': 0.0, 'alerts': [],
    }
    if total_value <= 0:
        return out

    equity_value = sum(_f(h.market_value) for h in holdings if h.asset_type in _EQUITY_TYPES)
    picks = [h for h in holdings if is_pick(h)]
    picks_value = sum(_f(h.market_value) for h in picks)
    out['equity_value'] = round(equity_value, 2)
    out['picks_value'] = round(picks_value, 2)

    # 精选个股层内：按申万一级（industry 字段）聚合
    picks_by_industry = {}
    unclassified_picks = 0.0
    for h in picks:
        mv = _f(h.market_value)
        if mv <= 0:
            continue
        ind = (h.industry or '').strip()
        if not ind:
            unclassified_picks += mv
        else:
            picks_by_industry[ind] = picks_by_industry.get(ind, 0.0) + mv
    out['unclassified_picks'] = round(unclassified_picks, 2)

    for ind, val in sorted(picks_by_industry.items(), key=lambda kv: kv[1], reverse=True):
        pct = val / picks_value * 100 if picks_value > 0 else 0
        out['picks_industries'].append({
            'industry': ind, 'value': round(val, 2), 'pct_of_picks': round(pct, 2),
        })
        if pct > INDUSTRY_PICK_CRITICAL:
            out['alerts'].append({
                'level': 'critical',
                'message': f"精选个股层「{ind}」占 {pct:.1f}%，突破 50% 红线",
                'action': "不再加仓该行业，新增个股优先未覆盖行业（消费/医药/互联网/有色等）。",
            })
        elif pct >= INDUSTRY_PICK_WARNING:
            out['alerts'].append({
                'level': 'warning',
                'message': f"精选个股层「{ind}」占 {pct:.1f}%，接近 50% 上限",
                'action': "临界状态，谨慎加仓该行业。",
            })

    # 全组合行业分布（参考，含职业脱钩视角）
    by_industry = {}
    unclassified_equity = 0.0
    for h in holdings:
        mv = _f(h.market_value)
        if mv <= 0:
            continue
        ind = (h.industry or '').strip()
        if not ind:
            if h.asset_type in _EQUITY_TYPES:
                unclassified_equity += mv
            continue
        by_industry[ind] = by_industry.get(ind, 0.0) + mv
    out['unclassified_equity'] = round(unclassified_equity, 2)
    for ind, val in sorted(by_industry.items(), key=lambda kv: kv[1], reverse=True):
        out['exposures'].append({
            'industry': ind, 'value': round(val, 2),
            'pct_of_total': round(val / total_value * 100, 2) if total_value > 0 else 0,
            'pct_of_equity': round(val / equity_value * 100, 2) if equity_value > 0 else 0,
        })

    return out
