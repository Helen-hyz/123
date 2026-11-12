"""
结果输出模块 —— 净值曲线绘图

绘制归一化净值曲线（策略 vs 基准），标注关键买卖点。
依赖 matplotlib（若不可用则降级为文本输出）。
"""
from __future__ import annotations

from typing import Sequence


def plot_equity(strategy_equity: Sequence[float],
                benchmark_equity: Sequence[float],
                datetimes: Sequence[str] = None,
                trades: list = None,
                title: str = "策略 vs 基准 净值曲线（归一化至 1.0）",
                save_path: str = "equity_curve.png") -> str | None:
    """
    绘制归一化净值对比图，标注买卖点。

    参数:
        strategy_equity: 策略净值序列
        benchmark_equity: 基准净值序列（买入持有）
        datetimes: 日期标签（可选）
        trades: 成交记录列表（用于标注买卖点）
        save_path: 图片保存路径
    返回:
        保存路径，或 None（matplotlib 不可用时）
    """
    try:
        import matplotlib
        matplotlib.use("Agg")  # 无界面后端
        import matplotlib.pyplot as plt
    except ImportError:
        print("[plotter] matplotlib 未安装，跳过绘图")
        return None

    n = min(len(strategy_equity), len(benchmark_equity))
    x = list(range(n))

    fig, ax = plt.subplots(figsize=(12, 6))
    ax.plot(x, strategy_equity[:n], label="策略", color="#d62728", linewidth=1.5)
    ax.plot(x, benchmark_equity[:n], label="基准(买入持有)",
            color="#1f77b4", linewidth=1.2, alpha=0.7)

    # 标注买卖点
    if trades:
        for t in trades:
            # 在净值曲线上找对应日期的位置
            if datetimes and t.datetime in datetimes:
                idx = list(datetimes).index(t.datetime)
                if idx < n:
                    y = strategy_equity[idx]
                    if t.side.value == "buy":
                        ax.scatter(idx, y, marker="^", color="green", s=60, zorder=5)
                    else:
                        ax.scatter(idx, y, marker="v", color="red", s=60, zorder=5)

    ax.set_title(title)
    ax.set_xlabel("交易日")
    ax.set_ylabel("归一化净值")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(save_path, dpi=150)
    plt.close(fig)
    print(f"[plotter] 净值曲线已保存到 {save_path}")
    return save_path


def print_metrics_table(metrics: dict, label: str = "策略") -> None:
    """以表格形式打印指标"""
    print(f"\n{'='*40}")
    print(f"  {label} 绩效指标")
    print(f"{'='*40}")
    for k, v in metrics.items():
        if isinstance(v, float):
            if "夏普" in k:
                print(f"  {k:<10}: {v:.4f}")
            else:
                print(f"  {k:<10}: {v*100:.2f}%")
        else:
            print(f"  {k:<10}: {v}")
    print(f"{'='*40}\n")
