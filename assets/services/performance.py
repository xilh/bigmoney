import datetime
from decimal import Decimal
from django.utils import timezone
from django.utils.dateparse import parse_datetime, parse_date

from assets.models import Snapshot, Transaction


def calculate_interval_performance(start_date_str, end_date_str):
    """
    使用 Modified Dietz 方法计算区间收益率
    """
    tz = timezone.get_current_timezone()

    # 解析日期并转为 aware datetime
    start_dt = _parse_to_aware(start_date_str, tz, end_of_day=False)
    end_dt = _parse_to_aware(end_date_str, tz, end_of_day=True)

    if start_dt >= end_dt:
        return _empty_result(start_dt, end_dt)

    # 1. 寻找期初快照（start_dt 当天或之前最近的一条）
    begin_snapshot = Snapshot.objects.filter(date__lte=start_dt).order_by('-date', '-id').first()
    if not begin_snapshot:
        # 没有 start_date 之前的快照，回退到区间内最早的快照作为起点
        begin_snapshot = (
            Snapshot.objects.filter(date__gt=start_dt, date__lte=end_dt)
            .order_by('date', 'id').first()
        )
    if not begin_snapshot:
        return _empty_result(start_dt, end_dt)

    start_value = begin_snapshot.total_value
    actual_start_dt = begin_snapshot.date

    # 2. 寻找期末快照（end_dt 当天或之前最近的一条）
    end_snapshot = Snapshot.objects.filter(date__lte=end_dt).order_by('-date', '-id').first()
    if not end_snapshot or end_snapshot.id == begin_snapshot.id:
        return _empty_result(actual_start_dt, end_dt)

    end_value = end_snapshot.total_value
    actual_end_dt = end_snapshot.date

    # 日级别区间长度
    start_day = actual_start_dt.date()
    end_day = actual_end_dt.date()
    total_days = (end_day - start_day).days
    if total_days <= 0:
        return _empty_result(actual_start_dt, actual_end_dt)

    # 3. 区间内的现金流（转入/转出）
    #    Transaction.date 是 DateField，用 date 范围查询
    transactions = Transaction.objects.filter(
        date__gt=start_day,
        date__lte=end_day,
        action__in=['transfer', 'withdraw'],
    )

    cf_net = Decimal('0')
    weighted_cf = Decimal('0')

    for tx in transactions:
        tx_day = tx.date
        days_passed = (tx_day - start_day).days
        days_passed = max(0, min(days_passed, total_days))

        weight = Decimal(str((total_days - days_passed) / total_days))

        amount = tx.amount if tx.action == 'transfer' else -tx.amount
        cf_net += amount
        weighted_cf += amount * weight

    # 4. Modified Dietz 收益率
    profit = end_value - start_value - cf_net
    adjusted_capital = start_value + weighted_cf

    if adjusted_capital > 0:
        return_rate = profit / adjusted_capital
    elif adjusted_capital < 0:
        return_rate = profit / abs(adjusted_capital)
    else:
        return_rate = Decimal('0')

    return {
        'start_date': actual_start_dt.isoformat(),
        'end_date': actual_end_dt.isoformat(),
        'start_value': float(start_value),
        'end_value': float(end_value),
        'net_cashflow': float(cf_net),
        'absolute_profit': float(profit),
        'return_rate_pct': float(return_rate * 100),
    }


def _parse_to_aware(date_str, tz, end_of_day=False):
    """将日期/日期时间字符串解析为 aware datetime"""
    # 先尝试 parse_date，纯日期字符串需要应用 end_of_day 逻辑
    d = parse_date(date_str)
    if d:
        t = datetime.time.max if end_of_day else datetime.time.min
        return timezone.make_aware(datetime.datetime.combine(d, t), tz)

    # 带时间的完整 datetime 字符串
    dt = parse_datetime(date_str)
    if dt:
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, tz)
        return dt

    raise ValueError(f"无法解析日期: {date_str}")


def _empty_result(start, end):
    return {
        'start_date': start.isoformat() if hasattr(start, 'isoformat') else str(start),
        'end_date': end.isoformat() if hasattr(end, 'isoformat') else str(end),
        'start_value': 0.0,
        'end_value': 0.0,
        'net_cashflow': 0.0,
        'absolute_profit': 0.0,
        'return_rate_pct': 0.0,
    }
