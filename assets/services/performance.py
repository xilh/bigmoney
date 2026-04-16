import datetime
from decimal import Decimal
from django.utils import timezone
from django.db.models import Sum

from assets.models import Snapshot, Transaction

def calculate_interval_performance(start_date_str, end_date_str):
    """
    使用 Modified Dietz 方法计算区间收益率
    """
    try:
        from django.utils.dateparse import parse_datetime, parse_date
        
        # 解析输入日期 (处理包含时间的 isoformat 或单纯的 date 字符串)
        # 统一转为 datetime 类型，强制使用此时区的边界
        start_dt = parse_datetime(start_date_str)
        if not start_dt:
            start_dt = datetime.datetime.combine(parse_date(start_date_str), datetime.time.min)
            
        end_dt = parse_datetime(end_date_str)
        if not end_dt:
            # 如果只传了结束日，那边界取该日的最后一秒
            end_dt = datetime.datetime.combine(parse_date(end_date_str), datetime.time.max)
            
    except (ValueError, TypeError):
        raise ValueError("Invalid date format. Please use YYYY-MM-DD or ISO 8601 strings.")

    # 1. 寻找期初快照
    start_snapshot = Snapshot.objects.filter(date__lte=start_dt).order_by('-date', '-id').first()
    if start_snapshot:
        start_value = start_snapshot.total_value
        actual_start_dt = start_snapshot.date
    else:
        # 没有 start_date 之前的快照，回退到区间内最早的快照作为起点
        first_snapshot = Snapshot.objects.filter(date__gt=start_dt, date__lte=end_dt).order_by('date', 'id').first()
        if first_snapshot:
            start_value = first_snapshot.total_value
            actual_start_dt = first_snapshot.date
        else:
            # 整个区间内完全没有快照，无法计算
            aware_start = timezone.make_aware(start_dt) if timezone.is_naive(start_dt) else start_dt
            aware_end = timezone.make_aware(end_dt) if timezone.is_naive(end_dt) else end_dt
            return _empty_result(aware_start, aware_end)

    # 2. 寻找期末快照
    end_snapshot = Snapshot.objects.filter(date__lte=end_dt).order_by('-date', '-id').first()
    if end_snapshot:
        end_value = end_snapshot.total_value
        actual_end_dt = end_snapshot.date
        # 极端情况：如果起止时间刚好挤压在了极短的时间内，找到了完全一样的快照
        if start_snapshot and end_snapshot.id == start_snapshot.id:
            # 期初期末是一个快照，收益为 0
            return _empty_result(actual_start_dt, actual_end_dt)
    else:
        end_value = Decimal('0')
        actual_end_dt = timezone.make_aware(end_dt) if timezone.is_naive(end_dt) else end_dt
        return _empty_result(actual_start_dt, actual_end_dt)

    total_days = (actual_end_dt.date() - actual_start_dt.date()).days
    if total_days < 0:
        return _empty_result(actual_start_dt, actual_end_dt)

    # 3. 寻找期间内的有效操作日志（注资/提现）
    transactions = Transaction.objects.filter(
        date__gt=actual_start_dt,
        date__lte=actual_end_dt,
        action__in=['transfer', 'withdraw']
    )

    cf_net = Decimal('0')
    weighted_cf = Decimal('0')

    for tx in transactions:
        tx_date = getattr(tx, 'date')
        # 如果 tx_date 是只是 date 类型，需转为 datetime 或直接取日级别相减
        if isinstance(tx_date, datetime.datetime):
            tx_day = tx_date.date()
        else:
            tx_day = tx_date
            
        weight_days_passed = (tx_day - actual_start_dt.date()).days
        # 区间约束防溢出
        weight_days_passed = max(0, min(weight_days_passed, total_days))
        
        weight = Decimal(str((total_days - weight_days_passed) / total_days)) if total_days > 0 else Decimal('1.0')
        
        amount = tx.amount if tx.action == 'transfer' else -tx.amount
        cf_net += amount
        weighted_cf += amount * weight

    # 4. 计算绝对利润与 Dietz 收益率
    profit = end_value - start_value - cf_net
    adjusted_cost = start_value + weighted_cf

    if adjusted_cost > 0:
        return_rate = profit / adjusted_cost
    elif adjusted_cost < 0:
        # 当期初几乎无本金，且大额在期末抽走时可能发生负成本基数。特殊处理。
        return_rate = profit / abs(adjusted_cost) 
    else:
        # 只有赚钱，没有本金（比如零成本白嫖福利），算一个无极限利润（可视为0%或100%）
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
