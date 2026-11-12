"""结果输出模块：指标计算 + 净值曲线绘图"""
from .metrics import (total_return, annualized_return, max_drawdown,
                      sharpe_ratio, calc_metrics, calc_benchmark_equity, normalize)
from .plotter import plot_equity, print_metrics_table

__all__ = [
    "total_return", "annualized_return", "max_drawdown",
    "sharpe_ratio", "calc_metrics", "calc_benchmark_equity", "normalize",
    "plot_equity", "print_metrics_table",
]
