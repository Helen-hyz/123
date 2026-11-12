"""
结果输出模块 —— 净值曲线绘图

绘制归一化净值曲线（策略 vs 基准），标注关键买卖点。
依赖 matplotlib（若不可用则降级为文本输出）。
"""
from __future__ import annotations

from typing import Sequence

from output.metrics import normalize


def plot_equity(strategy_equity: Sequence[float],
                benchmark_equity: Sequence[float],
                datetimes: Sequence[str] = None,
                trades: list = None,
                title: str = "策略 vs 基准 净值曲线（归一化至 1.0）",
                save_path: str = "equity_curve.png",
                benchmark_label: str = "基准(沪深300)",
                split_index: int | None = None) -> str | None:
    """
    绘制归一化净值对比图，标注买卖点与样本内/外分界。

    参数:
        strategy_equity: 策略净值序列（引擎输出，未归一化）
        benchmark_equity: 基准净值序列（已归一化到 1.0）
        datetimes: 日期标签（可选）
        trades: 成交记录列表（用于标注买卖点）
        split_index: 样本内/外分界的位置（可选），用于阴影与竖线
        save_path: 图片保存路径
    """
    try:
        import matplotlib
        matplotlib.use("Agg")  # 无界面后端
        import matplotlib.pyplot as plt
    except ImportError:
        print("[plotter] matplotlib 未安装，跳过绘图")
        return None

    strat = normalize(strategy_equity)
    n = min(len(strat), len(benchmark_equity))
    x = list(range(n))

    fig, ax = plt.subplots(figsize=(13, 6.5))

    # 样本内/外阴影
    if split_index is not None and 0 < split_index < n:
        ax.axvspan(0, split_index, color="#999999", alpha=0.08)
        ax.axvspan(split_index, n, color="#4c72b0", alpha=0.06)
        ax.axvline(split_index, color="#666666", linestyle="--", linewidth=1)
        ax.text(split_index * 0.5, 0.97, "样本内", ha="center", va="top",
                fontsize=10, color="#555555",
                transform=ax.get_xaxis_transform())
        ax.text(split_index + (n - split_index) * 0.5, 0.97, "样本外",
                ha="center", va="top", fontsize=10, color="#555555",
                transform=ax.get_xaxis_transform())

    ax.plot(x, strat[:n], label="策略(MACD 16/32/9)", color="#d62728",
            linewidth=1.6, zorder=3)
    ax.plot(x, benchmark_equity[:n], label=benchmark_label,
            color="#1f77b4", linewidth=1.3, alpha=0.85, zorder=2)

    # 标注买卖点
    if trades and datetimes:
        pos = {d: i for i, d in enumerate(datetimes)}
        for t in trades:
            idx = pos.get(t.datetime)
            if idx is None or idx >= n:
                continue
            y = strat[idx]
            if t.side.value == "buy":
                ax.scatter(idx, y, marker="^", color="#2ca02c", s=55,
                           zorder=5, edgecolors="white", linewidths=0.5)
            else:
                ax.scatter(idx, y, marker="v", color="#d62728", s=55,
                           zorder=5, edgecolors="white", linewidths=0.5)

    # 日期刻度（约 6 个）
    if datetimes:
        step = max(1, n // 6)
        ticks = list(range(0, n, step))
        ax.set_xticks(ticks)
        ax.set_xticklabels([datetimes[i] for i in ticks], rotation=15)

    # 终值标注
    ax.annotate(f"策略 {strat[n-1]:.3f}", xy=(n - 1, strat[n - 1]),
                xytext=(-10, 8), textcoords="offset points",
                color="#d62728", fontsize=9, ha="right")
    ax.annotate(f"{benchmark_label.split('(')[0]} {benchmark_equity[n-1]:.3f}",
                xy=(n - 1, benchmark_equity[n - 1]), xytext=(-10, -14),
                textcoords="offset points", color="#1f77b4", fontsize=9,
                ha="right")

    ax.set_title(title)
    ax.set_xlabel("交易日")
    ax.set_ylabel("归一化净值")
    ax.legend(loc="upper left")
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
