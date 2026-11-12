"""
量化交易系统 —— 主入口

串联四大模块完成一次完整回测：
  1. 数据接入  -> data (RandomWalkFeed / AkshareFeed / YahooFeed)
  2. 策略信号  -> strategy (DualMAStrategy / MACDStrategy)
  3. 回测引擎  -> engine (Cerebro + Broker)
  4. 结果输出  -> output (指标 + 净值曲线图)

运行：python main.py
"""
from __future__ import annotations

import sys
import os

# 确保能 import 本项目包
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data.sources import RandomWalkFeed, AkshareFeed, YahooFeed, BaostockFeed
from strategy.strategy import DualMAStrategy, MACDStrategy
from engine.cerebro import Cerebro
from output.metrics import calc_metrics, calc_benchmark_equity, normalize
from output.plotter import plot_equity, print_metrics_table


def load_data():
    """优先接入真实数据（akshare A 股），失败则回退到随机游走演示"""
    print("[1/4] 数据接入：加载行情数据...")
    try:
        data = AkshareFeed(symbol="sh600519", start_date="20200101",
                           end_date="20231231")
        print(f"      [真实数据] 贵州茅台(sh600519) 共 {len(data)} 根 K 线")
        return data
    except Exception as e:
        print(f"      真实数据获取失败({e})，改用随机游走演示数据")
        data = RandomWalkFeed(n_bars=800, start_price=100.0, seed=42)
        print(f"      [模拟数据] 共 {len(data)} 根 K 线")
        return data


def run_backtest():
    # ========== 1. 数据接入 ==========
    data = load_data()

    # ========== 2. 策略信号 + 3. 回测引擎 ==========
    print("[2/4] 策略信号 + 回测引擎：构建 Cerebro 并运行...")
    cerebro = Cerebro(cash=1_000_000.0, commission=0.0003)
    cerebro.adddata(data)
    # 经 optimize.py 样本内搜索 + 样本外验证后选出的稳健配置：
    #   MACD(16, 32, 9)  —— 样本外夏普 0.766，收益 +22.99%，回撤 12.14%
    cerebro.addstrategy(MACDStrategy, period_me1=16, period_me2=32,
                        period_signal=9)

    result = cerebro.run()
    print(f"      回测完成：{result['n_trades']} 笔交易，"
          f"累计收益 {result['total_return']*100:.2f}%")

    # ========== 4. 结果输出 ==========
    print("[3/4] 结果输出：计算绩效指标...")
    strat_equity = result["equity_curve"]
    strat_norm = normalize(strat_equity)

    # 基准：买入持有
    bench_equity = calc_benchmark_equity(data)
    bench_norm = normalize(bench_equity)

    strat_metrics = calc_metrics(strat_equity)
    bench_metrics = calc_metrics(bench_equity)

    print_metrics_table(strat_metrics, label="策略")
    print_metrics_table(bench_metrics, label="基准(买入持有)")

    # 超额收益
    excess = strat_metrics["累计收益率"] - bench_metrics["累计收益率"]
    print(f"  相对基准超额收益: {excess*100:.2f}%")

    # 绘制净值对比图
    print("[4/4] 结果输出：绘制净值曲线图...")
    plot_equity(strat_norm, bench_norm,
                datetimes=result["datetimes"],
                trades=result["trades"],
                save_path="equity_curve.png")

    print("\n回测全部完成 ✔")
    return result


if __name__ == "__main__":
    run_backtest()
