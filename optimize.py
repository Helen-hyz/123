"""
参数与信号迭代脚本

流程：
  1. 接入真实数据（akshare，贵州茅台 sh600519 2020-2023）
  2. 划分样本内(2020-2021) / 样本外(2022-2023)
  3. 样本内网格搜索：双均线参数、MACD 参数
  4. 对样本内最优候选做样本外检验（识别过拟合）
  5. 信号迭代：在稳健基线上叠加趋势过滤 / RSI 过滤，样本内外对比
  6. 基于「样本外夏普」选择最终参数与信号，保存 CSV

运行：python optimize.py
"""
from __future__ import annotations

import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data.feed import DataFeed
from data.sources import AkshareFeed
from strategy.strategy import DualMAStrategy, MACDStrategy, FilteredMAStrategy
from engine.cerebro import Cerebro
from output.metrics import calc_metrics


def slice_bars(bars, start, end):
    return [b for b in bars if start <= b["datetime"] <= end]


def run_once(bars, strategy_cls, **params):
    """在给定 bar 上跑一次回测，返回指标字典"""
    d = DataFeed()
    d.load_bars(list(bars))
    cb = Cerebro(cash=1_000_000.0, commission=0.0003)
    cb.adddata(d)
    cb.addstrategy(strategy_cls, **params)
    r = cb.run()
    m = calc_metrics(r["equity_curve"])
    m["交易笔数"] = r["n_trades"]
    return m


def fmt(m):
    return (f"收益 {m['累计收益率']*100:7.2f}% | 年化 {m['年化收益率']*100:6.2f}% | "
            f"回撤 {m['最大回撤']*100:6.2f}% | 夏普 {m['夏普比率']:6.3f} | "
            f"交易 {m['交易笔数']:3d} 笔")


def main():
    print("[1] 接入真实数据：akshare 贵州茅台 sh600519 2020-2023 ...")
    full = AkshareFeed("sh600519", "20200101", "20231231")
    bars = full._bars
    in_bars = slice_bars(bars, "2020-01-01", "2021-12-31")
    oos_bars = slice_bars(bars, "2022-01-01", "2023-12-31")
    print(f"    全样本 {len(bars)} 根；样本内 {len(in_bars)} 根；样本外 {len(oos_bars)} 根")

    bench_in = calc_metrics([b["close"] / in_bars[0]["close"] for b in in_bars])
    bench_oos = calc_metrics([b["close"] / oos_bars[0]["close"] for b in oos_bars])
    print(f"    基准(买入持有) 样本内：{fmt(_with_trades(bench_in, 1))}")
    print(f"    基准(买入持有) 样本外：{fmt(_with_trades(bench_oos, 1))}")

    rows = []

    def run_both(tag, cls, **kw):
        mi = run_once(in_bars, cls, **kw)
        mo = run_once(oos_bars, cls, **kw)
        rows.append(_row(tag, cls.__name__, kw, "in", mi))
        rows.append(_row(tag, cls.__name__, kw, "oos", mo))
        return mi, mo

    # ---------- 2. 基准策略 ----------
    print("\n[2] 基准策略 DualMA(5,20)：")
    base_in, base_oos = run_both("baseline_5_20", DualMAStrategy,
                                  fast_period=5, slow_period=20)
    print(f"    样本内：{fmt(base_in)}")
    print(f"    样本外：{fmt(base_oos)}")

    # ---------- 3. 双均线网格（样本内）----------
    print("\n[3] 双均线参数网格（样本内搜索 + 样本外检验）...")
    ma_grid = []
    for fast in [3, 5, 8, 10, 13]:
        for slow in [15, 20, 30, 40, 60]:
            if fast >= slow:
                continue
            m = run_once(in_bars, DualMAStrategy,
                         fast_period=fast, slow_period=slow)
            ma_grid.append((fast, slow, m))
    ma_grid.sort(key=lambda x: x[2]["夏普比率"], reverse=True)

    print("    样本内 Top5（含样本外检验）：")
    ma_top = []
    for fast, slow, mi in ma_grid[:5]:
        mo = run_once(oos_bars, DualMAStrategy,
                      fast_period=fast, slow_period=slow)
        rows.append(_row(f"grid_ma_{fast}_{slow}", "DualMA",
                         dict(fast_period=fast, slow_period=slow), "in", mi))
        rows.append(_row(f"grid_ma_{fast}_{slow}", "DualMA",
                         dict(fast_period=fast, slow_period=slow), "oos", mo))
        ma_top.append((fast, slow, mi, mo))
        print(f"      MA({fast},{slow})  样本内 夏普{mi['夏普比率']:6.3f} | "
              f"样本外 收益{mo['累计收益率']*100:7.2f}% 夏普{mo['夏普比率']:6.3f} "
              f"({mo['交易笔数']}笔)")

    # ---------- 4. MACD ----------
    print("\n[4] MACD 参数（样本内搜索 + 样本外检验）...")
    macd_grid = []
    for p1, p2, ps in [(8, 17, 9), (10, 20, 5), (12, 26, 9),
                       (16, 32, 9), (20, 40, 10)]:
        mi = run_once(in_bars, MACDStrategy, period_me1=p1,
                      period_me2=p2, period_signal=ps)
        macd_grid.append(((p1, p2, ps), mi))
    macd_grid.sort(key=lambda x: x[1]["夏普比率"], reverse=True)
    macd_top = []
    for ps, mi in macd_grid[:3]:
        mo = run_once(oos_bars, MACDStrategy, period_me1=ps[0],
                      period_me2=ps[1], period_signal=ps[2])
        rows.append(_row(f"grid_macd_{ps[0]}_{ps[1]}_{ps[2]}", "MACD",
                         dict(period_me1=ps[0], period_me2=ps[1],
                              period_signal=ps[2]), "in", mi))
        rows.append(_row(f"grid_macd_{ps[0]}_{ps[1]}_{ps[2]}", "MACD",
                         dict(period_me1=ps[0], period_me2=ps[1],
                              period_signal=ps[2]), "oos", mo))
        macd_top.append((ps, mi, mo))
        print(f"      MACD{ps}  样本内 夏普{mi['夏普比率']:6.3f} | "
              f"样本外 收益{mo['累计收益率']*100:7.2f}% 夏普{mo['夏普比率']:6.3f} "
              f"({mo['交易笔数']}笔)")

    # ---------- 5. 信号迭代 ----------
    print("\n[5] 信号迭代：在稳健基线 MA(5,20) 上叠加过滤 ...")
    filter_rows = []
    for use_trend in [False, True]:
        for use_rsi in [False, True]:
            kw = dict(fast_period=5, slow_period=20,
                      use_trend=use_trend, use_rsi=use_rsi)
            mi, mo = run_both(f"filter_t{int(use_trend)}_r{int(use_rsi)}",
                              FilteredMAStrategy, **kw)
            filter_rows.append((use_trend, use_rsi, mi, mo))
            print(f"      trend={int(use_trend)} rsi={int(use_rsi)}  "
                  f"样本内 夏普{mi['夏普比率']:6.3f} | "
                  f"样本外 收益{mo['累计收益率']*100:7.2f}% "
                  f"夏普{mo['夏普比率']:6.3f} ({mo['交易笔数']}笔)")

    # ---------- 6. 基于样本外选最终配置 ----------
    print("\n[6] 基于样本外夏普选择最终配置（要求样本外交易>=5 笔）...")
    candidates = []
    for fast, slow, mi, mo in ma_top:
        candidates.append((f"MA({fast},{slow})", DualMAStrategy,
                           dict(fast_period=fast, slow_period=slow), mi, mo))
    for use_trend, use_rsi, mi, mo in filter_rows:
        candidates.append((f"MA(5,20)+trend{int(use_trend)}+rsi{int(use_rsi)}",
                           FilteredMAStrategy,
                           dict(fast_period=5, slow_period=20,
                                use_trend=use_trend, use_rsi=use_rsi), mi, mo))
    for ps, mi, mo in macd_top:
        candidates.append((f"MACD{ps}", MACDStrategy,
                           dict(period_me1=ps[0], period_me2=ps[1],
                                period_signal=ps[2]), mi, mo))

    valid = [c for c in candidates if c[4]["交易笔数"] >= 5]
    valid.sort(key=lambda c: c[4]["夏普比率"], reverse=True)
    print("    样本外排名 Top5：")
    for tag, cls, kw, mi, mo in valid[:5]:
        print(f"      {tag:<28} 样本外 夏普{mo['夏普比率']:6.3f} "
              f"收益{mo['累计收益率']*100:7.2f}% 回撤{mo['最大回撤']*100:6.2f}% "
              f"| 样本内 夏普{mi['夏普比率']:6.3f}")

    best = valid[0]
    tag, cls, kw, mi, mo = best
    print("\n" + "=" * 64)
    print("迭代结论：")
    print(f"  最终配置：{tag}")
    print(f"    参数：{kw}")
    print(f"    样本内：{fmt(mi)}")
    print(f"    样本外：{fmt(mo)}")
    print(f"  对照基准 MA(5,20)：样本内 {fmt(base_in)}")
    print(f"                    样本外 {fmt(base_oos)}")
    print("=" * 64)

    # ---------- 7. 保存 ----------
    out_csv = "optimization_results.csv"
    with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["tag", "strategy", "params", "phase",
                                          "累计收益率", "年化收益率",
                                          "最大回撤", "夏普比率", "交易笔数"])
        w.writeheader()
        w.writerows(rows)
    print(f"\n[7] {len(rows)} 条结果已保存到 {out_csv}")

    return {"best": best, "rows": rows}


def _row(tag, strategy, params, phase, m):
    return {"tag": tag, "strategy": strategy, "params": str(params),
            "phase": phase,
            "累计收益率": m["累计收益率"], "年化收益率": m["年化收益率"],
            "最大回撤": m["最大回撤"], "夏普比率": m["夏普比率"],
            "交易笔数": m["交易笔数"]}


def _with_trades(m, n):
    m = dict(m)
    m["交易笔数"] = n
    return m


if __name__ == "__main__":
    main()
