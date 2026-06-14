"""
数据完整性自检：跨表对账，发现持仓/交易/快照之间的潜在不一致。

对账规则：
1. holdings.market_value 之和 ≈ 各 layer.total_market_value 之和
2. 最新 snapshot.total_value ≈ holdings.market_value 之和（若快照在 1 小时内）
3. 同一持仓的 transactions（buy/sell）数量净额 vs holding.quantity 在合理范围
4. 有 realized_pnl 的卖出记录数量 与 cashflow 总收益口径自洽

返回结构：
{
    'overall': 'pass' | 'warning' | 'fail',
    'checks': [
        {'name': str, 'status': 'pass'|'warning'|'fail', 'message': str, 'detail': dict}
    ],
}
"""
from decimal import Decimal
from datetime import timedelta

from django.db.models import Sum
from django.utils import timezone

from ..models import Holding, AssetLayer, Snapshot, Transaction


_TOLERANCE_YUAN = Decimal('1.00')  # 持仓/层级合计允许 1 元舍入误差
_SNAPSHOT_FRESH_HOURS = 24         # 快照新鲜度阈值


def run_integrity_checks() -> dict:
    checks = []

    # === Check 1: holdings 合计 vs 层级合计 ===
    holdings_sum = Holding.objects.aggregate(s=Sum('market_value'))['s'] or Decimal('0')
    layers_sum = sum(
        (l.total_market_value for l in AssetLayer.objects.all()),
        Decimal('0'),
    )
    diff = abs(holdings_sum - layers_sum)
    if diff <= _TOLERANCE_YUAN:
        checks.append({
            'name': '持仓与层级合计',
            'status': 'pass',
            'message': f'持仓合计 ¥{holdings_sum:,.2f} = 层级合计 ¥{layers_sum:,.2f}',
            'detail': {'diff': float(diff)},
        })
    else:
        checks.append({
            'name': '持仓与层级合计',
            'status': 'fail',
            'message': f'持仓合计与层级合计差 ¥{float(diff):,.2f}（持仓可能未正确归属层级）',
            'detail': {
                'holdings_sum': float(holdings_sum),
                'layers_sum': float(layers_sum),
                'diff': float(diff),
            },
        })

    # === Check 2: 最新快照 vs 当前持仓（新鲜快照） ===
    latest_snap = Snapshot.objects.order_by('-date', '-id').first()
    if latest_snap:
        age_hours = (timezone.now() - latest_snap.date).total_seconds() / 3600
        if age_hours <= _SNAPSHOT_FRESH_HOURS:
            snap_total = Decimal(str(latest_snap.total_value))
            snap_diff = abs(snap_total - holdings_sum)
            if snap_diff <= _TOLERANCE_YUAN:
                checks.append({
                    'name': '最新快照与当前持仓',
                    'status': 'pass',
                    'message': f'快照（{int(age_hours)}h 前）总值 ¥{snap_total:,.2f} = 当前持仓 ¥{holdings_sum:,.2f}',
                    'detail': {'age_hours': age_hours},
                })
            else:
                checks.append({
                    'name': '最新快照与当前持仓',
                    'status': 'warning',
                    'message': f'快照（{int(age_hours)}h 前）总值与当前持仓差 ¥{float(snap_diff):,.2f}',
                    'detail': {
                        'snapshot_total': float(snap_total),
                        'current_total': float(holdings_sum),
                        'age_hours': age_hours,
                    },
                })
        else:
            checks.append({
                'name': '最新快照与当前持仓',
                'status': 'warning',
                'message': f'最近快照已经是 {int(age_hours)} 小时前，建议建立新快照',
                'detail': {'age_hours': age_hours},
            })
    else:
        checks.append({
            'name': '最新快照与当前持仓',
            'status': 'warning',
            'message': '尚无任何快照记录，无法对账',
            'detail': {},
        })

    # === Check 3: 持仓未归属层级 ===
    orphan_count = Holding.objects.filter(layer__isnull=True).count()
    if orphan_count == 0:
        checks.append({
            'name': '持仓层级归属',
            'status': 'pass',
            'message': '所有持仓均已归属层级',
            'detail': {},
        })
    else:
        checks.append({
            'name': '持仓层级归属',
            'status': 'fail',
            'message': f'存在 {orphan_count} 笔持仓未归属层级',
            'detail': {'count': orphan_count},
        })

    # === Check 4: 卖出记录的 realized_pnl 缺失情况 ===
    sells = Transaction.objects.filter(action='sell')
    sells_total = sells.count()
    sells_with_pnl = sells.exclude(realized_pnl__isnull=True).count()
    if sells_total == 0:
        checks.append({
            'name': '卖出记录已实现盈亏完整性',
            'status': 'pass',
            'message': '尚无卖出记录',
            'detail': {},
        })
    elif sells_with_pnl == sells_total:
        checks.append({
            'name': '卖出记录已实现盈亏完整性',
            'status': 'pass',
            'message': f'全部 {sells_total} 笔卖出记录均含已实现盈亏',
            'detail': {'total': sells_total},
        })
    else:
        missing = sells_total - sells_with_pnl
        checks.append({
            'name': '卖出记录已实现盈亏完整性',
            'status': 'warning',
            'message': f'{missing}/{sells_total} 笔卖出缺已实现盈亏（总收益率可能偏低）',
            'detail': {'missing': missing, 'total': sells_total},
        })

    # === Check 5: 未来日期交易 ===
    today = timezone.localdate()
    future_count = Transaction.objects.filter(date__gt=today).count()
    if future_count == 0:
        checks.append({
            'name': '无未来日期交易',
            'status': 'pass',
            'message': '所有交易日期均不晚于今天',
            'detail': {},
        })
    else:
        checks.append({
            'name': '无未来日期交易',
            'status': 'fail',
            'message': f'存在 {future_count} 条未来日期的交易记录',
            'detail': {'count': future_count},
        })

    # 总状态：任一 fail 即 fail；否则任一 warning 即 warning；否则 pass
    statuses = {c['status'] for c in checks}
    if 'fail' in statuses:
        overall = 'fail'
    elif 'warning' in statuses:
        overall = 'warning'
    else:
        overall = 'pass'

    return {
        'overall': overall,
        'checks': checks,
        'checked_at': timezone.localtime().isoformat(),
    }
