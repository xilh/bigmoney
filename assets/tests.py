from decimal import Decimal
from datetime import date, timedelta

from django.test import TestCase
from django.utils import timezone
from django.core.exceptions import ValidationError

from .models import AssetLayer, Holding, Snapshot, Transaction
from .services.rebalance import calculate_rebalance, calculate_risk_alerts, THRESHOLDS
from .services.performance import calculate_interval_performance


class HoldingProfitLossTest(TestCase):
    """Test Holding auto-calculation on save()."""

    def setUp(self):
        self.layer = AssetLayer.objects.create(
            name='测试层', target_ratio=20, color='#000', order=1,
        )

    def test_auto_calculate_profit_loss(self):
        h = Holding(
            layer=self.layer, name='测试股票',
            quantity=Decimal('100'), cost_price=Decimal('10.00'),
            current_price=Decimal('12.50'),
        )
        h.save()
        self.assertEqual(h.market_value, Decimal('1250.00'))
        self.assertEqual(h.profit_loss, Decimal('250.00'))
        self.assertAlmostEqual(float(h.profit_loss_pct), 25.0, places=2)

    def test_auto_calculate_loss(self):
        h = Holding(
            layer=self.layer, name='亏损股票',
            quantity=Decimal('200'), cost_price=Decimal('5.00'),
            current_price=Decimal('3.50'),
        )
        h.save()
        self.assertEqual(h.market_value, Decimal('700.00'))
        self.assertEqual(h.profit_loss, Decimal('-300.00'))
        self.assertAlmostEqual(float(h.profit_loss_pct), -30.0, places=2)

    def test_no_price_info_preserves_market_value(self):
        """If no cost/current price, save() should not overwrite market_value."""
        h = Holding(
            layer=self.layer, name='货币基金',
            market_value=Decimal('50000.00'),
        )
        h.save()
        self.assertEqual(h.market_value, Decimal('50000.00'))

    def test_zero_cost_no_division_error(self):
        h = Holding(
            layer=self.layer, name='零成本',
            quantity=Decimal('100'), cost_price=Decimal('0'),
            current_price=Decimal('10.00'),
        )
        h.save()
        # cost_price is falsy (0), so auto-calc should not run
        self.assertEqual(h.profit_loss_pct, Decimal('0'))

    def test_quantize_precision(self):
        """Verify profit_loss_pct is quantized to 4 decimal places."""
        h = Holding(
            layer=self.layer, name='精度测试',
            quantity=Decimal('3'), cost_price=Decimal('7.00'),
            current_price=Decimal('10.00'),
        )
        h.save()
        # (30-21)/21*100 = 42.857142...% -> should be 42.8571
        self.assertEqual(h.profit_loss_pct, Decimal('42.8571'))

    def test_validation_negative_price(self):
        h = Holding(
            layer=self.layer, name='非法',
            quantity=Decimal('100'), cost_price=Decimal('-5.00'),
            current_price=Decimal('10.00'),
        )
        with self.assertRaises(ValidationError):
            h.clean()

    def test_validation_negative_quantity(self):
        h = Holding(
            layer=self.layer, name='非法',
            quantity=Decimal('-100'), cost_price=Decimal('5.00'),
            current_price=Decimal('10.00'),
        )
        with self.assertRaises(ValidationError):
            h.clean()


class AssetLayerTotalValueTest(TestCase):
    """Test AssetLayer.total_market_value uses DB aggregation."""

    def test_total_market_value_aggregation(self):
        layer = AssetLayer.objects.create(
            name='测试', target_ratio=50, color='#000', order=1,
        )
        Holding.objects.create(
            layer=layer, name='A', market_value=Decimal('1000'),
        )
        Holding.objects.create(
            layer=layer, name='B', market_value=Decimal('2000'),
        )
        self.assertEqual(layer.total_market_value, Decimal('3000'))

    def test_empty_layer(self):
        layer = AssetLayer.objects.create(
            name='空层', target_ratio=10, color='#000', order=2,
        )
        self.assertEqual(layer.total_market_value, Decimal('0'))


class RebalanceCalculationTest(TestCase):
    """Test the rebalance engine."""

    def test_balanced_portfolio(self):
        layers_data = [
            {'id': 1, 'name': '安全垫', 'target_ratio': 50, 'actual_value': 5000},
            {'id': 2, 'name': '股票', 'target_ratio': 50, 'actual_value': 5000},
        ]
        result = calculate_rebalance(layers_data, 10000)
        self.assertEqual(len(result['alerts']), 0)
        for layer in result['layers']:
            self.assertEqual(layer['status'], 'balanced')

    def test_critical_deviation(self):
        layers_data = [
            {'id': 1, 'name': '安全垫', 'target_ratio': 50, 'actual_value': 3500},
            {'id': 2, 'name': '股票', 'target_ratio': 50, 'actual_value': 6500},
        ]
        result = calculate_rebalance(layers_data, 10000)
        critical_alerts = [a for a in result['alerts'] if a['level'] == 'critical']
        self.assertEqual(len(critical_alerts), 2)  # both layers deviate >5%

    def test_zero_total_value(self):
        result = calculate_rebalance([], 0)
        self.assertEqual(result['total_value'], 0)
        self.assertEqual(len(result['layers']), 0)

    def test_warning_deviation(self):
        """3-5% deviation should be warning."""
        layers_data = [
            {'id': 1, 'name': '安全垫', 'target_ratio': 50, 'actual_value': 4600},
            {'id': 2, 'name': '股票', 'target_ratio': 50, 'actual_value': 5400},
        ]
        result = calculate_rebalance(layers_data, 10000)
        warnings = [a for a in result['alerts'] if a['level'] == 'warning']
        self.assertTrue(len(warnings) >= 1)


class RiskAlertsTest(TestCase):
    """Test risk alert generation."""

    def setUp(self):
        self.layer1 = AssetLayer.objects.create(name='安全垫', target_ratio=12.5, color='#000', order=1)
        self.layer3 = AssetLayer.objects.create(name='股票核心', target_ratio=37.5, color='#000', order=3)
        self.layer5 = AssetLayer.objects.create(name='卫星', target_ratio=15, color='#000', order=5)

    def test_concentration_warning(self):
        """Stock exceeding 5% of total should trigger warning."""
        h = Holding.objects.create(
            layer=self.layer3, name='集中股',
            asset_type='stock', market_value=Decimal('6000'),
        )
        holdings = list(Holding.objects.select_related('layer').all())
        alerts = calculate_risk_alerts(holdings, 100000)
        conc = [a for a in alerts if a['alert_type'] == 'concentration_5pct']
        self.assertEqual(len(conc), 1)
        self.assertEqual(conc[0]['level'], 'warning')

    def test_concentration_critical(self):
        """Stock exceeding 10% should trigger critical."""
        Holding.objects.create(
            layer=self.layer3, name='超集中股',
            asset_type='stock', market_value=Decimal('11000'),
        )
        holdings = list(Holding.objects.select_related('layer').all())
        alerts = calculate_risk_alerts(holdings, 100000)
        conc = [a for a in alerts if a['alert_type'] == 'concentration_5pct']
        self.assertEqual(conc[0]['level'], 'critical')

    def test_no_alert_for_fund(self):
        """Funds should not trigger concentration alerts."""
        Holding.objects.create(
            layer=self.layer3, name='指数基金',
            asset_type='index_fund', market_value=Decimal('20000'),
        )
        holdings = list(Holding.objects.select_related('layer').all())
        alerts = calculate_risk_alerts(holdings, 100000)
        conc = [a for a in alerts if a['alert_type'] == 'concentration_5pct']
        self.assertEqual(len(conc), 0)

    def test_satellite_stop_loss(self):
        """Layer 5 holding with -35% should trigger sl_30 alert."""
        Holding.objects.create(
            layer=self.layer5, name='卫星亏损',
            asset_type='stock', market_value=Decimal('650'),
            profit_loss_pct=Decimal('-35'),
        )
        holdings = list(Holding.objects.select_related('layer').all())
        alerts = calculate_risk_alerts(holdings, 100000)
        sl = [a for a in alerts if a['alert_type'] == 'satellite_sl_30']
        self.assertEqual(len(sl), 1)

    def test_satellite_take_profit(self):
        """Layer 5 holding with +60% should trigger tp_50 alert."""
        Holding.objects.create(
            layer=self.layer5, name='卫星盈利',
            asset_type='stock', market_value=Decimal('1600'),
            profit_loss_pct=Decimal('60'),
        )
        holdings = list(Holding.objects.select_related('layer').all())
        alerts = calculate_risk_alerts(holdings, 100000)
        tp = [a for a in alerts if a['alert_type'] == 'satellite_tp_50']
        self.assertEqual(len(tp), 1)

    def test_bond_anomaly(self):
        """Bond fund with -2% should trigger warning."""
        Holding.objects.create(
            layer=self.layer1, name='债基',
            asset_type='bond_fund', market_value=Decimal('10000'),
            profit_loss_pct=Decimal('-2.0'),
        )
        holdings = list(Holding.objects.select_related('layer').all())
        alerts = calculate_risk_alerts(holdings, 100000)
        bond = [a for a in alerts if a['alert_type'] == 'bond_anomaly_1pct']
        self.assertEqual(len(bond), 1)

    def test_acknowledged_alerts_skipped(self):
        """Acknowledged alerts should not appear."""
        h = Holding.objects.create(
            layer=self.layer3, name='已知悉股',
            asset_type='stock', market_value=Decimal('6000'),
        )
        holdings = list(Holding.objects.select_related('layer').all())
        acked = {f"{h.id}:concentration_5pct"}
        alerts = calculate_risk_alerts(holdings, 100000, acknowledged_keys=acked)
        conc = [a for a in alerts if a['alert_type'] == 'concentration_5pct']
        self.assertEqual(len(conc), 0)

    def test_drawdown_protocol(self):
        """Layer 3 overall loss >10% should trigger drawdown alert."""
        Holding.objects.create(
            layer=self.layer3, name='亏损核心A',
            asset_type='index_fund',
            quantity=Decimal('100'), cost_price=Decimal('10'),
            current_price=Decimal('8.5'),
            market_value=Decimal('850'), profit_loss=Decimal('-150'),
        )
        holdings = list(Holding.objects.select_related('layer').all())
        alerts = calculate_risk_alerts(holdings, 100000)
        dd = [a for a in alerts if a['alert_type'] == 'drawdown_10']
        self.assertEqual(len(dd), 1)

    def test_empty_portfolio_no_alerts(self):
        alerts = calculate_risk_alerts([], 0)
        self.assertEqual(len(alerts), 0)


class ModifiedDietzTest(TestCase):
    """Test Modified Dietz performance calculation."""

    def _local_date(self, days_ago=0):
        """Return a local date N days ago in the configured timezone."""
        return timezone.localdate() - timedelta(days=days_ago)

    def _make_aware_dt(self, local_date):
        """Convert a local date to an aware datetime at noon (avoids edge cases)."""
        import datetime as dt
        tz = timezone.get_current_timezone()
        return timezone.make_aware(
            dt.datetime.combine(local_date, dt.time(12, 0)), tz,
        )

    def _create_snapshot(self, local_date, total_value):
        return Snapshot.objects.create(
            date=self._make_aware_dt(local_date),
            total_value=Decimal(str(total_value)),
        )

    def test_simple_return(self):
        """No cash flows, simple return."""
        start = self._local_date(30)
        end = self._local_date(1)

        self._create_snapshot(start, 10000)
        self._create_snapshot(end, 11000)

        result = calculate_interval_performance(
            start.isoformat(), end.isoformat(),
        )
        self.assertAlmostEqual(result['return_rate_pct'], 10.0, places=1)

    def test_with_transfer(self):
        """Cash inflow should be factored out of return."""
        start = self._local_date(30)
        mid = self._local_date(15)
        end = self._local_date(1)

        self._create_snapshot(start, 10000)
        self._create_snapshot(end, 16000)

        Transaction.objects.create(
            action='transfer', asset_name='转入',
            amount=Decimal('5000'), date=mid,
        )

        result = calculate_interval_performance(
            start.isoformat(), end.isoformat(),
        )
        # Profit = 16000 - 10000 - 5000 = 1000
        # Adjusted capital ~= 10000 + 5000*0.5 = 12500
        # Return ~= 1000/12500 = 8%
        self.assertAlmostEqual(result['return_rate_pct'], 8.0, delta=1.0)

    def test_no_snapshots_returns_empty(self):
        result = calculate_interval_performance('2025-01-01', '2025-06-01')
        self.assertEqual(result['return_rate_pct'], 0.0)

    def test_same_snapshot_returns_empty(self):
        """Single snapshot should return empty result."""
        today = self._local_date(0)
        self._create_snapshot(today, 10000)
        result = calculate_interval_performance(
            today.isoformat(), today.isoformat(),
        )
        self.assertEqual(result['return_rate_pct'], 0.0)

    def test_negative_return(self):
        start = self._local_date(30)
        end = self._local_date(1)

        self._create_snapshot(start, 10000)
        self._create_snapshot(end, 8000)

        result = calculate_interval_performance(
            start.isoformat(), end.isoformat(),
        )
        self.assertAlmostEqual(result['return_rate_pct'], -20.0, places=1)


class TransactionValidationTest(TestCase):
    """Test Transaction model validation."""

    def test_future_date_rejected(self):
        tx = Transaction(
            action='buy', asset_name='测试',
            amount=Decimal('1000'),
            date=date.today() + timedelta(days=1),
        )
        with self.assertRaises(ValidationError):
            tx.clean()

    def test_today_date_accepted(self):
        tx = Transaction(
            action='buy', asset_name='测试',
            amount=Decimal('1000'), date=date.today(),
        )
        tx.clean()  # Should not raise


# ============================================================
# 数据准确性回归测试（P0/P1 修复）
# ============================================================

from .services.ledger import (
    snapshot_holding, record_holding_change, record_holding_removal,
    _record_sell,
)


class LedgerTimezoneTest(TestCase):
    """ledger 服务应使用本地日期，不能用 UTC 日期。"""

    def setUp(self):
        self.layer = AssetLayer.objects.create(
            name='测试层', target_ratio=20, color='#000', order=1,
        )

    def test_buy_uses_local_date(self):
        h = Holding.objects.create(
            layer=self.layer, name='A',
            quantity=Decimal('100'), cost_price=Decimal('10'),
            current_price=Decimal('11'),
        )
        record_holding_change(h, old_data=None, source='manual')
        tx = Transaction.objects.filter(action='buy', asset_name='A').first()
        self.assertIsNotNone(tx)
        self.assertEqual(tx.date, timezone.localdate())

    def test_sell_uses_local_date(self):
        h = Holding.objects.create(
            layer=self.layer, name='B',
            quantity=Decimal('100'), cost_price=Decimal('10'),
            current_price=Decimal('11'),
        )
        record_holding_removal(h, source='manual')
        tx = Transaction.objects.filter(action='sell', asset_name='B').first()
        self.assertIsNotNone(tx)
        self.assertEqual(tx.date, timezone.localdate())


class SellRealizedPnlTest(TestCase):
    """_record_sell 应使用市值差额（更贴近实际成交价）计算盈亏。"""

    def setUp(self):
        self.layer = AssetLayer.objects.create(
            name='测试层', target_ratio=20, color='#000', order=1,
        )

    def test_sell_uses_market_value_delta(self):
        """卖出 50/100 份，old market_value=1500，new market_value=750
        → 成交额=750，cost=10*50=500，realized=250"""
        h = Holding.objects.create(
            layer=self.layer, name='C',
            quantity=Decimal('100'), cost_price=Decimal('10'),
            current_price=Decimal('15'),
            market_value=Decimal('1500'),
        )
        old_data = {
            'quantity': Decimal('100'),
            'cost_price': Decimal('10'),
            'current_price': Decimal('15'),
            'market_value': Decimal('1500'),
            'profit_loss': Decimal('500'),
            'name': h.name,
            'platform': '',
        }
        # 模拟卖出后状态
        h.quantity = Decimal('50')
        h.market_value = Decimal('750')
        h.save()
        tx = _record_sell(h, Decimal('50'), old_data, 'manual')
        self.assertEqual(tx.amount, Decimal('750'))
        self.assertEqual(tx.realized_pnl, Decimal('250'))


class PartialSellMarketValueTest(TestCase):
    """部分卖出且 current_price 为空时，应按剩余份额比例缩减市值，不应清零。"""

    def setUp(self):
        from django.test import Client
        self.layer = AssetLayer.objects.create(
            name='测试层', target_ratio=20, color='#000', order=1,
        )
        self.client = Client()

    def test_partial_sell_no_current_price_preserves_market_value(self):
        h = Holding.objects.create(
            layer=self.layer, name='理财D',
            quantity=Decimal('100'), cost_price=Decimal('10'),
            current_price=None,
            market_value=Decimal('1100'),
        )
        # 直接卖 30 份
        from django.test.utils import override_settings
        with override_settings(AUTH_REQUIRED=False):
            import json as _json
            resp = self.client.post(
                f'/api/holding/{h.id}/sell/',
                data=_json.dumps({'quantity': '30', 'amount': '350'}),
                content_type='application/json',
            )
        self.assertEqual(resp.status_code, 200)
        h.refresh_from_db()
        self.assertEqual(h.quantity, Decimal('70'))
        # 应按比例缩减：1100 * 70/100 = 770（不是 0）
        self.assertEqual(h.market_value, Decimal('770.00'))


class SnapshotHoldingsCostTest(TestCase):
    """快照应保存 cost_price/quantity 便于事后重算。"""

    def setUp(self):
        from django.test import Client
        self.layer = AssetLayer.objects.create(
            name='测试层', target_ratio=20, color='#000', order=1,
        )
        self.client = Client()

    def test_snapshot_includes_cost_and_quantity(self):
        Holding.objects.create(
            layer=self.layer, name='E',
            quantity=Decimal('50'), cost_price=Decimal('20'),
            current_price=Decimal('22'),
        )
        from django.test.utils import override_settings
        with override_settings(AUTH_REQUIRED=False):
            resp = self.client.post('/api/snapshot/create/', data='{}', content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        snap = Snapshot.objects.first()
        self.assertEqual(len(snap.holdings_data), 1)
        item = snap.holdings_data[0]
        self.assertEqual(item['cost_price'], 20.0)
        self.assertEqual(item['quantity'], 50.0)
        self.assertEqual(item['current_price'], 22.0)


class IntegrityCheckTest(TestCase):
    """数据完整性自检服务。"""

    def setUp(self):
        self.layer = AssetLayer.objects.create(
            name='测试层', target_ratio=20, color='#000', order=1,
        )

    def test_all_pass_for_clean_data(self):
        Holding.objects.create(
            layer=self.layer, name='X',
            quantity=Decimal('100'), cost_price=Decimal('10'),
            current_price=Decimal('12'),
        )
        from .services.integrity import run_integrity_checks
        result = run_integrity_checks()
        # 没有快照 → warning；其它应 pass
        self.assertIn(result['overall'], ('pass', 'warning'))
        names = {c['name'] for c in result['checks']}
        self.assertIn('持仓与层级合计', names)
        self.assertIn('卖出记录已实现盈亏完整性', names)

    def test_future_date_transaction_fails(self):
        Transaction.objects.create(
            action='buy', asset_name='Y', amount=Decimal('1000'),
            date=timezone.localdate() + timedelta(days=5),
        )
        from .services.integrity import run_integrity_checks
        result = run_integrity_checks()
        future_check = next(c for c in result['checks'] if c['name'] == '无未来日期交易')
        self.assertEqual(future_check['status'], 'fail')
        self.assertEqual(result['overall'], 'fail')

    def test_sell_without_realized_pnl_warns(self):
        Transaction.objects.create(
            action='sell', asset_name='Z',
            quantity=Decimal('10'), price=Decimal('10'),
            amount=Decimal('100'),
            realized_pnl=None,
            date=timezone.localdate(),
        )
        from .services.integrity import run_integrity_checks
        result = run_integrity_checks()
        pnl_check = next(c for c in result['checks'] if c['name'] == '卖出记录已实现盈亏完整性')
        self.assertEqual(pnl_check['status'], 'warning')


class SellValidationTest(TestCase):
    """卖出严格校验：超额请求应明确拒绝。"""

    def setUp(self):
        from django.test import Client
        self.layer = AssetLayer.objects.create(
            name='测试层', target_ratio=20, color='#000', order=1,
        )
        self.client = Client()

    def test_sell_qty_exceeds_holding_rejected(self):
        h = Holding.objects.create(
            layer=self.layer, name='W',
            quantity=Decimal('10'), cost_price=Decimal('10'),
            current_price=Decimal('15'),
        )
        from django.test.utils import override_settings
        import json as _json
        with override_settings(AUTH_REQUIRED=False):
            resp = self.client.post(
                f'/api/holding/{h.id}/sell/',
                data=_json.dumps({'quantity': '20', 'amount': '300'}),
                content_type='application/json',
            )
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertFalse(body['success'])
        self.assertEqual(body.get('code'), 'INVALID_INPUT')
        # 持仓应保持不变
        h.refresh_from_db()
        self.assertEqual(h.quantity, Decimal('10'))


class ApiErrorEnvelopeTest(TestCase):
    """API 错误响应应带结构化 code/message，不暴露 traceback。"""

    def setUp(self):
        from django.test import Client
        self.client = Client()

    def test_not_found_returns_structured_error(self):
        from django.test.utils import override_settings
        with override_settings(AUTH_REQUIRED=False):
            resp = self.client.delete('/api/holding/99999/delete/')
        self.assertEqual(resp.status_code, 404)
        body = resp.json()
        self.assertFalse(body['success'])
        self.assertEqual(body['code'], 'NOT_FOUND')
        self.assertIn('message', body)


class TransactionUpdateRecalcTest(TestCase):
    """api_transaction_update 改 amount 时应让 quantity*price 与 realized_pnl 自洽。"""

    def setUp(self):
        from django.test import Client
        self.client = Client()

    def test_update_amount_recalcs_price_and_realized(self):
        tx = Transaction.objects.create(
            action='sell', asset_name='F',
            quantity=Decimal('100'), price=Decimal('10'),
            amount=Decimal('1000'),
            realized_pnl=Decimal('200'),  # 隐含成本 = 1000 - 200 = 800
            date=timezone.localdate(),
        )
        from django.test.utils import override_settings
        import json as _json
        with override_settings(AUTH_REQUIRED=False):
            resp = self.client.post(
                f'/api/transaction/{tx.id}/update/',
                data=_json.dumps({'amount': '1200'}),
                content_type='application/json',
            )
        self.assertEqual(resp.status_code, 200)
        tx.refresh_from_db()
        self.assertEqual(tx.amount, Decimal('1200'))
        # price 从 1200/100 反推
        self.assertEqual(tx.price, Decimal('12.0000'))
        # realized_pnl 重算：amount 1200 - cost 800 = 400
        self.assertEqual(tx.realized_pnl, Decimal('400.00'))
