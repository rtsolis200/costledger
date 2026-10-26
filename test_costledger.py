"""costledger —— 自检测试。

按调用记录归集 token 与费用，区分输入输出与缓存命中，给出日度与月度汇总。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import costledger  # noqa: E402


class TestCostLedger(unittest.TestCase):
    def test_cost_is_split_by_kind(self):
        call = {"model": "gpt-4o", "input_tokens": 1000, "output_tokens": 500,
                "cache_read_tokens": 200, "ts": 1760000000, "feature": "chat"}
        row = costledger.cost_of(call)
        self.assertGreater(row["cost_input"], 0)
        self.assertGreater(row["cost_output"], 0)
        self.assertGreater(row["cost_cache_read"], 0)
        self.assertAlmostEqual(
            row["cost_total"],
            row["cost_input"] + row["cost_output"] + row["cost_cache_read"]
            + row["cost_cache_write"], places=8)

    def test_cache_read_is_cheaper_than_fresh_input(self):
        base = {"model": "gpt-4o", "input_tokens": 1000, "output_tokens": 0}
        fresh = costledger.cost_of(base)
        cached = costledger.cost_of({**base, "cache_read_tokens": 1000})
        self.assertLess(cached["cost_cache_read"], fresh["cost_input"])

    def test_aggregation_by_feature(self):
        calls = [
            {"model": "gpt-4o", "input_tokens": 100, "output_tokens": 10,
             "feature": "a", "ts": 1760000000},
            {"model": "gpt-4o", "input_tokens": 200, "output_tokens": 20,
             "feature": "b", "ts": 1760000000},
        ]
        agg = costledger.aggregate([costledger.cost_of(c) for c in calls])
        self.assertEqual(agg["by_feature"]["a"]["calls"], 1)
        self.assertEqual(agg["by_feature"]["b"]["calls"], 1)
        self.assertEqual(agg["totals"]["calls"], 2)

    def test_unknown_model_uses_default_price(self):
        p = costledger.price_for("some-unknown-model-v1")
        self.assertEqual(p, costledger.PRICING["default"])

    def test_expensive_call_is_an_outlier(self):
        calls = [
            {"model": "gpt-4o", "input_tokens": 10, "output_tokens": 1,
             "ts": 1760000000},
            {"model": "gpt-4o", "input_tokens": 100000, "output_tokens": 1000,
             "ts": 1760000001},
        ]
        agg = costledger.aggregate([costledger.cost_of(c) for c in calls])
        self.assertEqual(agg["outliers"][0]["total_tokens"], 101000)
