"""
交易台账服务
当持仓发生变化时，自动生成 Transaction 记录，
构建完整的买卖历史与已实现盈亏台账。
"""
import logging
from decimal import Decimal

from django.utils import timezone

from ..models import Holding, Transaction

logger = logging.getLogger(__name__)


def snapshot_holding(holding):
    """捕获持仓当前状态，用于变更对比。"""
    return {
        'quantity': holding.quantity or Decimal('0'),
        'cost_price': holding.cost_price,
        'current_price': holding.current_price,
        'market_value': holding.market_value or Decimal('0'),
        'profit_loss': holding.profit_loss or Decimal('0'),
        'name': holding.name,
        'platform': holding.platform,
    }


def record_holding_change(holding, old_data, source='manual'):
    """
    对比持仓变更前后状态，按需创建 buy/sell Transaction。

    Args:
        holding: 已保存的 Holding 实例（新状态）
        old_data: snapshot_holding() 返回的 dict，或 None（新建持仓）
        source: 'manual' | 'ocr' | 'auto'

    Returns:
        Transaction or None
    """
    if old_data is None:
        # 全新持仓 → 买入
        return _record_buy(holding, holding.quantity, source)

    old_qty = old_data['quantity']
    new_qty = holding.quantity or Decimal('0')
    delta = new_qty - old_qty

    if delta > 0:
        return _record_buy(holding, delta, source)
    elif delta < 0:
        return _record_sell(holding, abs(delta), old_data, source)
    else:
        # 数量没变（仅价格变动），不生成交易记录
        return None


def record_holding_removal(holding, source='manual'):
    """
    持仓被删除或消失时，生成 sell Transaction 记录已实现盈亏。

    Args:
        holding: 即将被删除的 Holding
        source: 'manual' | 'ocr' | 'auto'

    Returns:
        Transaction
    """
    qty = holding.quantity or Decimal('0')
    price = holding.current_price or Decimal('0')
    amount = holding.market_value or Decimal('0')
    realized = holding.profit_loss or Decimal('0')

    tx = Transaction.objects.create(
        holding=None,  # holding 即将被删除
        action='sell',
        asset_name=holding.name,
        quantity=qty,
        price=price,
        amount=amount,
        date=timezone.now().date(),
        source=source,
        realized_pnl=realized,
        platform=holding.platform,
        notes='持仓清除（自动记录）',
    )
    logger.info("Auto sell tx #%d: %s qty=%.4f amount=%.2f realized_pnl=%.2f",
                tx.id, holding.name, qty, amount, realized)
    return tx


def _record_buy(holding, qty, source):
    """创建买入交易记录。"""
    price = holding.cost_price or holding.current_price or Decimal('0')
    amount = price * qty if price else (holding.market_value or Decimal('0'))

    tx = Transaction.objects.create(
        holding=holding,
        action='buy',
        asset_name=holding.name,
        quantity=qty,
        price=price,
        amount=amount,
        date=timezone.now().date(),
        source=source,
        platform=holding.platform,
        notes='自动记录' if source != 'manual' else '',
    )
    logger.info("Auto buy tx #%d: %s qty=%.4f amount=%.2f", tx.id, holding.name, qty, amount)
    return tx


def _record_sell(holding, qty_sold, old_data, source):
    """创建卖出交易记录，计算已实现盈亏。"""
    price = holding.current_price or Decimal('0')
    amount = price * qty_sold if price else Decimal('0')

    # 计算已实现盈亏：(卖出价 - 成本价) * 卖出数量
    cost_price = old_data.get('cost_price') or Decimal('0')
    realized = (price - cost_price) * qty_sold if price and cost_price else Decimal('0')

    tx = Transaction.objects.create(
        holding=holding,
        action='sell',
        asset_name=holding.name,
        quantity=qty_sold,
        price=price,
        amount=amount,
        date=timezone.now().date(),
        source=source,
        realized_pnl=realized,
        platform=holding.platform,
        notes='自动记录' if source != 'manual' else '',
    )
    logger.info("Auto sell tx #%d: %s qty=%.4f amount=%.2f realized_pnl=%.2f",
                tx.id, holding.name, qty_sold, amount, realized)
    return tx
