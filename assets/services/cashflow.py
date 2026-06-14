"""
资金流向分析服务
从快照差值和交易记录自动推算投资组合的资金流入/流出，
包括买卖活动、净转入/转出，以及未对账差额。
"""
import logging
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

import json
from django.db.models import Sum, Q
from django.utils import timezone

from ..models import Snapshot, Transaction, Holding, Setting

logger = logging.getLogger(__name__)


def analyze_portfolio_flows():
    """
    主入口：分析投资组合资金流向。

    Returns:
        dict with keys:
        - summary: 总览统计
        - periods: 每对相邻快照之间的推算明细
        - recent_transactions: 近期交易记录
        - monthly_chart: 月度图表数据
        - action_chart: 按操作类型汇总
    """
    snapshots = list(
        Snapshot.objects.order_by('date', 'id')
        .values('id', 'date', 'total_value', 'holdings_data')
    )

    # --- 汇总统计 ---
    total_transfers = Transaction.objects.filter(
        action='transfer',
    ).aggregate(t=Sum('amount'))['t'] or Decimal('0')
    total_withdrawals = Transaction.objects.filter(
        action='withdraw',
    ).aggregate(t=Sum('amount'))['t'] or Decimal('0')
    net_contribution = total_transfers - total_withdrawals

    current_value = Holding.objects.aggregate(
        t=Sum('market_value'))['t'] or Decimal('0')
    
    current_profit = Holding.objects.aggregate(
        t=Sum('profit_loss'))['t'] or Decimal('0')

    total_realized = Transaction.objects.filter(
        action='sell', realized_pnl__isnull=False,
    ).aggregate(t=Sum('realized_pnl'))['t'] or Decimal('0')

    total_dividends = Transaction.objects.filter(
        action='dividend'
    ).aggregate(t=Sum('amount'))['t'] or Decimal('0')

    actual_investment_return = current_profit + total_realized + total_dividends
    total_consumption = net_contribution + actual_investment_return - current_value
    
    return_rate = float(actual_investment_return / net_contribution * 100) if net_contribution > 0 else 0.0

    summary = {
        'current_value': current_value,
        'net_contribution': net_contribution,
        'total_transfers': total_transfers,
        'total_withdrawals': total_withdrawals,
        'total_return': actual_investment_return,
        'total_consumption': total_consumption,
        'return_rate': return_rate,
        'total_realized_pnl': total_realized,
    }

    # 加载已忽略的预警
    raw_dismissed = Setting.get('dismissed_cashflow_alerts', '{}')
    try:
        dismissed_alerts = json.loads(raw_dismissed)
    except (json.JSONDecodeError, TypeError):
        dismissed_alerts = {}

    # --- 初始缺口检测 ---
    # 首张快照的总资产远大于已记录的转入总额时，提示补录初始投入
    initial_gap = None
    if snapshots:
        first_snap = snapshots[0]
        first_val = first_snap['total_value']
        # 首张快照之前或当天已记录的转入
        first_date = first_snap['date'].date() if hasattr(first_snap['date'], 'date') else first_snap['date']
        pre_transfers = Transaction.objects.filter(
            action='transfer', date__lte=first_date,
        ).aggregate(t=Sum('amount'))['t'] or Decimal('0')
        gap = first_val - pre_transfers
        if gap > 500:  # 超过 ¥500 视为有缺口
            gap_key = f"initial_{first_date.isoformat()}"
            if gap_key not in dismissed_alerts:
                initial_gap = {
                    'date': first_date.isoformat(),
                    'snapshot_value': float(first_val),
                    'recorded_transfers': float(pre_transfers),
                    'gap': float(gap),
                    'key': gap_key,
                }

    summary['initial_gap'] = initial_gap

    # --- 逐期分析（相邻快照之间） ---
    all_periods = _analyze_periods(snapshots)
    periods = [p for p in all_periods if p['end_date'] not in dismissed_alerts]

    # --- 近期交易（最新 50 条） ---
    recent_txs = list(
        Transaction.objects.select_related('holding')
        .order_by('-date', '-created_at')[:50]
        .values(
            'id', 'action', 'asset_name', 'amount', 'date',
            'source', 'realized_pnl', 'platform', 'notes', 'quantity', 'price',
        )
    )
    for tx in recent_txs:
        tx['action_display'] = dict(Transaction.ACTION_CHOICES).get(tx['action'], tx['action'])
        tx['source_display'] = dict(Transaction.SOURCE_CHOICES).get(tx['source'], tx['source'])
        tx['amount'] = float(tx['amount'])
        tx['realized_pnl'] = float(tx['realized_pnl']) if tx['realized_pnl'] else None

    # --- 月度图表数据 ---
    monthly_chart = _build_monthly_chart(all_periods)

    # --- 按操作类型汇总 ---
    action_chart = _build_action_chart()

    return {
        'summary': summary,
        'periods': periods,
        'all_periods': all_periods,
        'recent_transactions': recent_txs,
        'monthly_chart': monthly_chart,
        'action_chart': action_chart,
    }


def _analyze_periods(snapshots):
    """分析相邻快照之间的资金变动，推算未记录的流入/流出。"""
    if len(snapshots) < 2:
        return []

    periods = []
    for i in range(len(snapshots) - 1):
        s1 = snapshots[i]
        s2 = snapshots[i + 1]

        start_date = s1['date'].date() if hasattr(s1['date'], 'date') else s1['date']
        end_date = s2['date'].date() if hasattr(s2['date'], 'date') else s2['date']
        start_val = s1['total_value']
        end_val = s2['total_value']
        value_change = end_val - start_val

        # 该时段内已确认的资金流（转入/转出）
        flows = Transaction.objects.filter(
            date__gt=start_date, date__lte=end_date,
            action__in=['transfer', 'withdraw'],
        )
        transfers = flows.filter(action='transfer').aggregate(
            t=Sum('amount'))['t'] or Decimal('0')
        withdrawals = flows.filter(action='withdraw').aggregate(
            t=Sum('amount'))['t'] or Decimal('0')
        net_flow = transfers - withdrawals

        # 该时段内的买卖活动
        buys = Transaction.objects.filter(
            date__gt=start_date, date__lte=end_date, action='buy',
        ).aggregate(t=Sum('amount'))['t'] or Decimal('0')
        sells = Transaction.objects.filter(
            date__gt=start_date, date__lte=end_date, action='sell',
        ).aggregate(t=Sum('amount'))['t'] or Decimal('0')

        s1_holdings = s1.get('holdings_data', [])
        s2_holdings = s2.get('holdings_data', [])
        
        if s1_holdings and s2_holdings:
            s1_profit = sum(Decimal(str(item.get('profit_loss', '0'))) for item in s1_holdings)
            s2_profit = sum(Decimal(str(item.get('profit_loss', '0'))) for item in s2_holdings)
            
            realized_pnl = Transaction.objects.filter(
                date__gt=start_date, date__lte=end_date, action='sell'
            ).aggregate(t=Sum('realized_pnl'))['t'] or Decimal('0')
            
            dividend_amount = Transaction.objects.filter(
                date__gt=start_date, date__lte=end_date, action='dividend'
            ).aggregate(t=Sum('amount'))['t'] or Decimal('0')
            
            actual_return = (s2_profit - s1_profit) + realized_pnl + dividend_amount
            consumption = net_flow + actual_return - value_change
        else:
            actual_return = value_change - net_flow
            consumption = Decimal('0')

        # 推算未记录的资金流：
        # 如果有大量买入但没有对应的转入记录，说明可能有未记录的转入
        net_buy = buys - sells
        unreconciled = net_buy - net_flow  # 正值=可能缺少转入记录

        periods.append({
            'start_date': start_date.isoformat(),
            'end_date': end_date.isoformat(),
            'start_value': float(start_val),
            'end_value': float(end_val),
            'value_change': float(value_change),
            'transfers': float(transfers),
            'withdrawals': float(withdrawals),
            'net_flow': float(net_flow),
            'buys': float(buys),
            'sells': float(sells),
            'estimated_return': float(actual_return),
            'consumption': float(consumption),
            'unreconciled': float(unreconciled),
        })

    # 只返回最近的 12 个区间，倒序（最新在前）
    return list(reversed(periods[-12:]))


def _build_monthly_chart(periods):
    """按月汇总资金流向，用于柱状图。"""
    # 取最近 6 个月的交易数据
    six_months_ago = timezone.localdate().replace(day=1) - timedelta(days=180)
    txs = Transaction.objects.filter(date__gte=six_months_ago)

    monthly = defaultdict(lambda: {
        'transfer': Decimal('0'), 'withdraw': Decimal('0'),
        'buy': Decimal('0'), 'sell': Decimal('0'),
        'consumption': Decimal('0'),
    })

    for tx in txs.values('action', 'amount', 'date'):
        month_key = tx['date'].strftime('%Y-%m')
        if tx['action'] in monthly[month_key]:
            monthly[month_key][tx['action']] += tx['amount']

    # 把期间消费归结到结束日期所在的月份
    six_months_ago_str = six_months_ago.strftime('%Y-%m')
    for p in periods:
        month_key = p['end_date'][:7]
        if month_key >= six_months_ago_str:
            monthly[month_key]['consumption'] += Decimal(str(p['consumption']))

    result = []
    for month_key in sorted(monthly.keys()):
        d = monthly[month_key]
        result.append({
            'month': month_key,
            'transfer': float(d['transfer']),
            'withdraw': float(d['withdraw']),
            'buy': float(d['buy']),
            'sell': float(d['sell']),
            'consumption': float(d['consumption']),
        })
    return result


def _build_action_chart():
    """按操作类型汇总总金额，用于环形图。"""
    action_labels = dict(Transaction.ACTION_CHOICES)
    data = list(
        Transaction.objects.values('action')
        .annotate(total=Sum('amount'))
        .order_by('-total')
    )
    return [
        {
            'action': item['action'],
            'label': action_labels.get(item['action'], item['action']),
            'total': float(item['total']),
        }
        for item in data
    ]
