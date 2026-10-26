"""costledger —— 把每一次模型调用的花费记成流水账。

设计原则：
1. **追到单次调用**。只给总额的话，你只知道花了钱，不知道是哪个功能花的。
2. **输入、输出、缓存命中要分开**。三者的单价不同，混在一起算就看不出优化空间。
3. **单价表写在配置里**。模型价格会变，表一改，所有历史记录按新价重算。

仅依赖 Python 标准库。

用法:
    python costledger.py --help
    python costledger.py --calls calls.jsonl
    python costledger.py --calls calls.jsonl --json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# 单价表（美元 / 每百万 token）
# ---------------------------------------------------------------------------

PRICING: dict[str, dict[str, float]] = {
    "default": {"input": 3.0, "output": 15.0, "cache_read": 0.3, "cache_write": 3.75},
    "gpt-4o": {"input": 2.5, "output": 10.0, "cache_read": 1.25, "cache_write": 3.75},
    "gpt-4o-mini": {"input": 0.15, "output": 0.6, "cache_read": 0.075, "cache_write": 0.18},
    "claude-3-5-sonnet": {"input": 3.0, "output": 15.0, "cache_read": 0.3, "cache_write": 3.75},
    "claude-3-5-haiku": {"input": 0.8, "output": 4.0, "cache_read": 0.08, "cache_write": 1.0},
    "gemini-1.5-pro": {"input": 1.25, "output": 5.0, "cache_read": 0.3125, "cache_write": 0.3125},
    "gemini-1.5-flash": {"input": 0.075, "output": 0.3, "cache_read": 0.01875, "cache_write": 0.075},
}

MILLION = 1_000_000


def price_for(model: str) -> dict[str, float]:
    """取模型的单价。未知模型回落到 default。"""
    m = (model or "").lower()
    if m in PRICING:
        return PRICING[m]
    for key, val in PRICING.items():
        if key != "default" and key in m:
            return val
    return PRICING["default"]


# ---------------------------------------------------------------------------
# 加载
# ---------------------------------------------------------------------------

def load_calls(path: str) -> list[dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read().strip()
    except OSError as e:
        raise ValueError(f"读不了 {path}：{e}") from e

    if not raw:
        return []

    # JSON 数组
    if raw.startswith("["):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"JSON 解析失败：{e}") from e
        return [x for x in data if isinstance(x, dict)]

    calls: list[dict] = []
    for lineno, line in enumerate(raw.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(f"第 {lineno} 行 JSON 解析失败：{e}") from e
        if isinstance(obj, dict):
            calls.append(obj)
    return calls


def _get_int(d: dict, *names: str) -> int:
    for n in names:
        v = d.get(n)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return int(v)
    return 0


def _call_ts(call: dict) -> str:
    for key in ("ts", "timestamp", "time", "created_at"):
        v = call.get(key)
        if v is None:
            continue
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(float(v), tz=timezone.utc).strftime("%Y-%m-%d")
        s = str(v)
        if len(s) >= 10 and s[4] == "-":
            return s[:10]
    return "未知日期"


# ---------------------------------------------------------------------------
# 归集
# ---------------------------------------------------------------------------

def cost_of(call: dict) -> dict:
    """算一次调用的花费。"""
    model = str(call.get("model", ""))
    price = price_for(model)

    inp = _get_int(call, "input_tokens", "prompt_tokens", "in_tokens")
    out = _get_int(call, "output_tokens", "completion_tokens", "out_tokens")
    cache_read = _get_int(call, "cache_read_tokens", "cached_tokens",
                          "cache_read_input_tokens")
    cache_write = _get_int(call, "cache_write_tokens", "cache_creation_tokens")

    # 有些记录把总输入拆成了「已缓存」和「未缓存」，
    # 这里按「input_tokens 不含 cache_read」处理，避免重复计费。
    cost_in = inp / MILLION * price["input"]
    cost_out = out / MILLION * price["output"]
    cost_cr = cache_read / MILLION * price["cache_read"]
    cost_cw = cache_write / MILLION * price["cache_write"]

    total = cost_in + cost_out + cost_cr + cost_cw

    return {
        "date": _call_ts(call),
        "model": model,
        "feature": call.get("feature") or call.get("tag")
                   or call.get("route") or call.get("name") or "未标注",
        "input_tokens": inp,
        "output_tokens": out,
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
        "cost_input": round(cost_in, 8),
        "cost_output": round(cost_out, 8),
        "cost_cache_read": round(cost_cr, 8),
        "cost_cache_write": round(cost_cw, 8),
        "cost_total": round(total, 8),
        "total_tokens": inp + out + cache_read + cache_write,
    }


def aggregate(rows: list[dict]) -> dict:
    """按天、月、功能、模型归集。"""
    by_day: dict[str, dict] = defaultdict(lambda: {"calls": 0, "cost": 0.0,
                                                    "tokens": 0})
    by_month: dict[str, dict] = defaultdict(lambda: {"calls": 0, "cost": 0.0,
                                                     "tokens": 0})
    by_feature: dict[str, dict] = defaultdict(lambda: {"calls": 0, "cost": 0.0,
                                                       "tokens": 0})
    by_model: dict[str, dict] = defaultdict(lambda: {"calls": 0, "cost": 0.0,
                                                     "tokens": 0})
    totals = {"cost_input": 0.0, "cost_output": 0.0, "cost_cache_read": 0.0,
              "cost_cache_write": 0.0, "cost_total": 0.0, "tokens": 0, "calls": 0}

    for r in rows:
        day = r["date"]
        month = day[:7] if len(day) >= 7 else "未知"
        for bucket, key in ((by_day, day), (by_month, month),
                            (by_feature, r["feature"]), (by_model, r["model"])):
            bucket[key]["calls"] += 1
            bucket[key]["cost"] += r["cost_total"]
            bucket[key]["tokens"] += r["total_tokens"]
        totals["cost_input"] += r["cost_input"]
        totals["cost_output"] += r["cost_output"]
        totals["cost_cache_read"] += r["cost_cache_read"]
        totals["cost_cache_write"] += r["cost_cache_write"]
        totals["cost_total"] += r["cost_total"]
        totals["tokens"] += r["total_tokens"]
        totals["calls"] += 1

    for k in ("cost_input", "cost_output", "cost_cache_read",
              "cost_cache_write", "cost_total"):
        totals[k] = round(totals[k], 6)

    # 找出单次异常贵的调用
    outliers = sorted(rows, key=lambda r: -r["cost_total"])[:10]

    return {
        "totals": totals,
        "by_day": {k: {"calls": v["calls"], "cost": round(v["cost"], 6),
                       "tokens": v["tokens"]}
                   for k, v in sorted(by_day.items())},
        "by_month": {k: {"calls": v["calls"], "cost": round(v["cost"], 6),
                         "tokens": v["tokens"]}
                     for k, v in sorted(by_month.items())},
        "by_feature": {k: {"calls": v["calls"], "cost": round(v["cost"], 6),
                           "tokens": v["tokens"]}
                       for k, v in sorted(by_feature.items(),
                                          key=lambda kv: -kv[1]["cost"])},
        "by_model": {k: {"calls": v["calls"], "cost": round(v["cost"], 6),
                         "tokens": v["tokens"]}
                     for k, v in sorted(by_model.items(),
                                        key=lambda kv: -kv[1]["cost"])},
        "outliers": outliers,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def render(agg: dict) -> str:
    out: list[str] = []
    t = agg["totals"]
    out.append("模型调用成本流水")
    out.append("=" * 60)
    out.append(f"调用数 {t['calls']} ｜ 总 token {t['tokens']:,} ｜ "
               f"总花费 ${t['cost_total']:.6f}")
    out.append("")
    out.append(f"  输入      ${t['cost_input']:.6f}")
    out.append(f"  输出      ${t['cost_output']:.6f}")
    out.append(f"  缓存读    ${t['cost_cache_read']:.6f}")
    out.append(f"  缓存写    ${t['cost_cache_write']:.6f}")
    out.append("")

    if agg["by_feature"]:
        out.append("按功能归集：")
        for name, v in agg["by_feature"].items():
            pct = (v["cost"] / t["cost_total"] * 100) if t["cost_total"] else 0
            bar = "█" * int(pct / 2.5)
            out.append(f"  {name:<24} ${v['cost']:>10.6f}  {pct:>5.1f}%  "
                       f"{v['calls']:>5} 次  {bar}")
        out.append("")

    if agg["by_model"]:
        out.append("按模型归集：")
        for name, v in agg["by_model"].items():
            out.append(f"  {name:<24} ${v['cost']:>10.6f}  {v['calls']:>5} 次")
        out.append("")

    if agg["by_day"]:
        out.append("按日归集：")
        for name, v in agg["by_day"].items():
            out.append(f"  {name}  ${v['cost']:>10.6f}  {v['calls']:>4} 次")
        out.append("")

    out.append("单次开销最大的调用：")
    for i, o in enumerate(agg["outliers"][:5], 1):
        out.append(f"  {i}. ${o['cost_total']:.6f}  {o['feature']}  "
                   f"{o['model']}  {o['date']}")
        out.append(f"     输入 {o['input_tokens']} / 输出 {o['output_tokens']} / "
                   f"缓存读 {o['cache_read_tokens']}")
    out.append("")
    out.append("注：单价表写在脚本顶部，模型调价后改表重算即可。")
    return "\n".join(out).rstrip()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="costledger",
        description="把每一次模型调用的花费记成流水账（仅依赖标准库）",
        epilog=(
            "调用记录（JSONL 或 JSON 数组）：\n"
            "  {\"model\":\"gpt-4o\",\"input_tokens\":1000,\"output_tokens\":200,"
            "\"ts\":1760000000,\"feature\":\"chat\"}\n"
            "  python costledger.py --calls calls.jsonl\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--calls", required=True, help="调用记录文件")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args(argv)

    try:
        calls = load_calls(args.calls)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2

    if not calls:
        print("没有调用记录。", file=sys.stderr)
        return 2

    rows = [cost_of(c) for c in calls]
    agg = aggregate(rows)

    if args.json:
        print(json.dumps({"calls": rows, "aggregate": agg},
                         ensure_ascii=False, indent=2))
        return 0

    print(render(agg))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
