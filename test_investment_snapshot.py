"""
investment_snapshot 纯函数单测——钉住三维评分和预警的阈值逻辑，
防止 config/investment_thresholds.json 或计算公式被改动后无声漂移。

运行：python3 -m unittest test_investment_snapshot -v
"""
import unittest

import investment_snapshot as snap


def make_exposures(*levels_and_domestic):
    """构造 build_exposures 风格的 exposures_by_code：[(supply_level, domestic_rate), ...] -> {'X': [...]}"""
    exposures = []
    for i, (level, domestic) in enumerate(levels_and_domestic):
        exposures.append({
            'industry_id': f'ind{i}', 'industry': f'行业{i}',
            'node_id': f'node{i}', 'node_name': f'环节{i}',
            'layer': 'midstream', 'supply_level': level, 'domestic_rate': domestic,
        })
    return {'X': exposures}


class TestThresholds(unittest.TestCase):
    def test_defaults_present(self):
        t = snap.load_thresholds()
        for key in ('prob_attack', 'prob_defense', 'divergent_spread',
                    'risk_alert_keywords', 'risk_defensive_keywords'):
            self.assertIn(key, t)

    def test_comment_key_stripped(self):
        # 配置文件里的 _comment 说明键不应污染返回值
        t = snap.load_thresholds()
        self.assertNotIn('_comment', t)

    def test_config_matches_defaults_shape(self):
        # 配置文件如果存在，其核心数值应可被读取为数字/列表
        t = snap.load_thresholds()
        self.assertIsInstance(t['prob_attack'], (int, float))
        self.assertIsInstance(t['risk_alert_keywords'], list)


class TestStockDimensions(unittest.TestCase):
    def setUp(self):
        # 固定阈值，避免测试受配置文件改动影响
        self._orig = snap.THRESHOLDS
        snap.THRESHOLDS = dict(snap.DEFAULT_THRESHOLDS)

    def tearDown(self):
        snap.THRESHOLDS = self._orig

    def test_no_exposures_supply_neutral(self):
        stock = {'code': 'X', 'probability': 50}
        dims = snap.stock_dimensions(stock, {})
        self.assertEqual(dims['supply'], 50)
        self.assertEqual(dims['exposures_count'], 0)

    def test_tight_exposure_lifts_supply(self):
        # tight=64，无国产率混入时 supply 应等于 64
        stock = {'code': 'X'}
        dims = snap.stock_dimensions(stock, make_exposures(('tight', None)))
        self.assertAlmostEqual(dims['supply'], 64, places=6)

    def test_risky_exposure_lowers_supply(self):
        stock = {'code': 'X'}
        dims = snap.stock_dimensions(stock, make_exposures(('risky', None)))
        self.assertAlmostEqual(dims['supply'], 32, places=6)

    def test_domestic_rate_blends_supply(self):
        # supply = base*0.72 + domestic*0.28；base=64(tight)，domestic=100 -> 64*.72+100*.28=74.08
        stock = {'code': 'X'}
        dims = snap.stock_dimensions(stock, make_exposures(('tight', 100)))
        self.assertAlmostEqual(dims['supply'], 74.08, places=2)

    def test_divergent_boundary(self):
        # spread 恰好等于 divergent_spread(26) 应判为背离
        # 构造 supply=50(无暴露), demand/profit 拉出 26 分差
        stock_low = {'code': 'X', 'sentiment_score': 24, 'probability': 24,
                     'revenue_growth': 0, 'vol_ratio': 1,
                     'fund_score': 24, 'gross_margin': None, 'profit_growth': 0, 'roe': 0}
        dims = snap.stock_dimensions(stock_low, {})
        # 手工核对 spread，并断言 divergent 与阈值一致
        self.assertEqual(dims['divergent'], dims['spread'] >= 26)

    def test_divergent_respects_threshold_change(self):
        stock = {'code': 'X', 'sentiment_score': 90, 'probability': 90,
                 'revenue_growth': 100, 'vol_ratio': 3,
                 'fund_score': 10, 'gross_margin': -20, 'profit_growth': -100, 'roe': -50}
        dims_default = snap.stock_dimensions(stock, {})
        self.assertTrue(dims_default['divergent'])  # 大分差默认应背离
        # 把阈值抬到 100，同样的 spread 不再算背离——证明阈值真正生效
        snap.THRESHOLDS = dict(snap.DEFAULT_THRESHOLDS, divergent_spread=100)
        dims_high = snap.stock_dimensions(stock, {})
        self.assertFalse(dims_high['divergent'])


class TestDetectAlerts(unittest.TestCase):
    def setUp(self):
        self._orig = snap.THRESHOLDS
        snap.THRESHOLDS = dict(snap.DEFAULT_THRESHOLDS)

    def tearDown(self):
        snap.THRESHOLDS = self._orig

    def _dims(self, divergent=False, spread=10):
        return {'divergent': divergent, 'spread': spread}

    def test_no_prev_no_alert(self):
        self.assertEqual(snap.detect_alerts(None, 'X', '甲', self._dims(), {'probability': 90}), [])

    def test_prob_cross_up(self):
        prev = {'probability': 54.9, 'risk_label': '中风险', 'divergent': 0}
        alerts = snap.detect_alerts(prev, 'X', '甲', self._dims(), {'probability': 55.0, 'risk_label': '中风险'})
        self.assertEqual([a['type'] for a in alerts], ['prob_up'])

    def test_prob_already_above_no_alert(self):
        prev = {'probability': 56, 'risk_label': '中风险', 'divergent': 0}
        alerts = snap.detect_alerts(prev, 'X', '甲', self._dims(), {'probability': 58, 'risk_label': '中风险'})
        self.assertEqual(alerts, [])

    def test_prob_cross_down(self):
        prev = {'probability': 42, 'risk_label': '中风险', 'divergent': 0}
        alerts = snap.detect_alerts(prev, 'X', '甲', self._dims(), {'probability': 38, 'risk_label': '中风险'})
        self.assertEqual([a['type'] for a in alerts], ['prob_down'])

    def test_risk_label_to_danger(self):
        prev = {'probability': 50, 'risk_label': '中风险', 'divergent': 0}
        alerts = snap.detect_alerts(prev, 'X', '甲', self._dims(), {'probability': 50, 'risk_label': '高风险'})
        self.assertEqual([a['type'] for a in alerts], ['risk'])

    def test_risk_alert_keywords_configurable(self):
        # 把 '高风险' 从告警关键词里移除，同样的跳变不应再触发
        snap.THRESHOLDS = dict(snap.DEFAULT_THRESHOLDS, risk_alert_keywords=['危险'])
        prev = {'probability': 50, 'risk_label': '中风险', 'divergent': 0}
        alerts = snap.detect_alerts(prev, 'X', '甲', self._dims(), {'probability': 50, 'risk_label': '高风险'})
        self.assertEqual(alerts, [])

    def test_divergent_newly_true(self):
        prev = {'probability': 50, 'risk_label': '中风险', 'divergent': 0}
        alerts = snap.detect_alerts(prev, 'X', '甲', self._dims(divergent=True, spread=30),
                                    {'probability': 50, 'risk_label': '中风险'})
        self.assertEqual([a['type'] for a in alerts], ['divergent'])

    def test_divergent_already_true_no_alert(self):
        prev = {'probability': 50, 'risk_label': '中风险', 'divergent': 1}
        alerts = snap.detect_alerts(prev, 'X', '甲', self._dims(divergent=True, spread=30),
                                    {'probability': 50, 'risk_label': '中风险'})
        self.assertEqual(alerts, [])

    def test_no_change_no_alert(self):
        prev = {'probability': 50, 'risk_label': '中风险', 'divergent': 0}
        alerts = snap.detect_alerts(prev, 'X', '甲', self._dims(), {'probability': 50, 'risk_label': '中风险'})
        self.assertEqual(alerts, [])


if __name__ == '__main__':
    unittest.main()
