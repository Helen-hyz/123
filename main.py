"""
量化交易系统 —— 主入口

串联四大模块完成一次完整回测：
  1. 数据接入  -> data (AkshareFeed 个股 / AkshareIndexFeed 基准)
  2. 策略信号  -> strategy (MACD 16/32/9)
  3. 回测引擎  -> engine (Cerebro + Broker)
  4. 结果输出  -> output (指标 + 净值曲线) + report (横向对比/归因/报告)

运行：python main.py
"""
from __future__ import annotations

import sys
import os

# 确保能 import 本项目包
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data.sources import (AkshareFeed, AkshareIndexFeed, RandomWalkFeed)
from strategy.strategy import MACDStrategy
from engine.cerebro import Cerebro
from output.metrics import (calc_metrics, align_benchmark_curve,
                            exposure_ratio)
from output.plotter import plot_equity
from report import (build_segment, segment_indices, write_report,
                    print_comparison)

# ============ 回测配置 ============
STOCK_SYMBOL = "sh600519"
STOCK_NAME = "贵州茅台"
BENCH_SYMBOL = "sh000300"
BENCH_NAME = "沪深300"

START, END = "20200101", "20231231"
IN_START, IN_END = "2020-01-01", "2021-12-31"
OOS_START, OOS_END = "2022-01-01", "2023-12-31"

INITIAL_CASH = 1_000_000.0
COMMISSION = 0.0003
STRATEGY_KWARGS = dict(period_me1=16, period_me2=32, period_signal=9)


def load_data():
    """加载个股与市场基准；个股失败回退随机游走，基准失败回退标的买入持有"""
    print("[1/5] 数据接入：加载个股与基准行情...")
    try:
        data = AkshareFeed(symbol=STOCK_SYMBOL, start_date=START, end_date=END)
        print(f"      [真实数据] {STOCK_NAME}({STOCK_SYMBOL}) "
              f"{len(data)} 根 K 线")
    except Exception as e:
        print(f"      个股数据获取失败({e})，改用随机游走演示数据")
        data = RandomWalkFeed(n_bars=800, start_price=100.0, seed=42)

    try:
        bench = AkshareIndexFeed(symbol=BENCH_SYMBOL, start_date=START,
                                 end_date=END)
        bench_bars = bench._bars
        bench_name, bench_symbol = BENCH_NAME, BENCH_SYMBOL
        print(f"      [基准] {bench_name}({bench_symbol}) "
              f"{len(bench_bars)} 根 K 线")
    except Exception as e:
        print(f"      基准指数获取失败({e})，改用标的买入持有作为基准")
        bench_bars = data._bars
        bench_name, bench_symbol = f"{STOCK_NAME}买入持有", STOCK_SYMBOL

    return data, bench_bars, bench_name, bench_symbol


def run_backtest():
    # ========== 1. 数据接入 ==========
    data, bench_bars, bench_name, bench_symbol = load_data()

    # ========== 2. 策略信号 + 3. 回测引擎 ==========
    print("[2/5] 策略信号 + 回测引擎：构建 Cerebro 并运行...")
    cerebro = Cerebro(cash=INITIAL_CASH, commission=COMMISSION)
    cerebro.adddata(data)
    cerebro.addstrategy(MACDStrategy, **STRATEGY_KWARGS)

    result = cerebro.run()
    print(f"      回测完成：{result['n_trades']} 笔交易，"
          f"累计收益 {result['total_return']*100:.2f}%")

    dates = result["datetimes"]
    n = len(dates)
    strat_equity = result["equity_curve"]
    positions = result["position_history"]

    # ========== 4. 横向对比 ==========
    print("[3/5] 横向对比：对齐基准并计算分段指标...")
    bench_norm = align_benchmark_curve(bench_bars, dates)
    bh_norm = align_benchmark_curve(data._bars, dates)

    in_idx = segment_indices(dates, None, IN_END)
    oos_idx = segment_indices(dates, OOS_START, None)
    full_idx = list(range(n))

    segments = [
        build_segment("全样本", full_idx, strat_equity, bench_norm,
                      bh_norm, positions),
        build_segment("样本内", in_idx, strat_equity, bench_norm,
                      bh_norm, positions),
        build_segment("样本外", oos_idx, strat_equity, bench_norm,
                      bh_norm, positions),
    ]
    segments = [s for s in segments if s is not None]
    print_comparison(segments)

    # ========== 5. 绘图 + 报告 ==========
    print("[4/5] 结果输出：绘制归一化净值曲线...")
    split_index = len(in_idx) if 0 < len(in_idx) < n else None
    plot_equity(
        strat_equity, bench_norm,
        datetimes=dates, trades=result["trades"],
        title=f"{STOCK_NAME} MACD(16,32,9) vs {bench_name}（归一化至 1.0）",
        save_path="equity_curve.png",
        benchmark_label=f"基准({bench_name})",
        split_index=split_index,
    )

    print("[5/5] 生成回测报告 report.md ...")
    full = segments[0]
    trade_stats = {
        "n_trades": result["n_trades"],
        "in_trades": sum(1 for t in result["trades"] if t.datetime <= IN_END),
        "oos_trades": sum(1 for t in result["trades"] if t.datetime > IN_END),
        "exposure": exposure_ratio(positions),
        "max_dd": full["strategy"]["最大回撤"],
        "bench_max_dd": full["bench"]["最大回撤"],
    }
    meta = {
        "stock_name": STOCK_NAME, "stock_symbol": STOCK_SYMBOL,
        "bench_name": bench_name, "bench_symbol": bench_symbol,
        "start": dates[0], "end": dates[-1], "n_bars": n,
        "in_start": IN_START, "in_end": IN_END,
        "oos_start": OOS_START, "oos_end": OOS_END,
        "cash": INITIAL_CASH, "commission": COMMISSION,
    }
    write_report("report.md", meta, segments, trade_stats)
    print("      报告已保存到 report.md")

    print("\n回测全部完成 ✔")
    return {**result, "segments": segments, "bench_norm": bench_norm}


if __name__ == "__main__":
    run_backtest()
