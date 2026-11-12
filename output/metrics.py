"""
结果输出模块 —— 绩效指标计算

作业要求的四个核心指标：累计收益率、年化收益率、最大回撤、夏普比率。
所有函数输入为净值序列（list[float]），输出为单个指标值。
"""
from __future__ import annotations

import math
from typing import Sequence


def total_return(equity: Sequence[float]) -> float:
    """累计收益率 = 期末净值 / 期初净值 - 1"""
    if len(equity) < 2:
        return 0.0
    return equity[-1] / equity[0] - 1.0


def annualized_return(equity: Sequence[float], periods_per_year: int = 252) -> float:
    """年化收益率。默认日频 -> 252 个交易日"""
    if len(equity) < 2:
        return 0.0
    total = equity[-1] / equity[0]
    n_years = (len(equity) - 1) / periods_per_year
    if n_years <= 0:
        return 0.0
    return total ** (1.0 / n_years) - 1.0


def max_drawdown(equity: Sequence[float]) -> float:
    """最大回撤 = max((峰值 - 谷值) / 峰值)"""
    if len(equity) < 2:
        return 0.0
    peak = equity[0]
    max_dd = 0.0
    for v in equity:
        if v > peak:
            peak = v
        dd = (peak - v) / peak
        if dd > max_dd:
            max_dd = dd
    return max_dd


def sharpe_ratio(equity: Sequence[float], periods_per_year: int = 252,
                 risk_free: float = 0.0) -> float:
    """年化夏普比率。risk_free 为年化无风险利率。"""
    if len(equity) < 2:
        return 0.0
    # 单期收益率
    rets = [equity[i] / equity[i - 1] - 1.0 for i in range(1, len(equity))]
    mean_ret = sum(rets) / len(rets)
    var = sum((r - mean_ret) ** 2 for r in rets) / len(rets)
    std = var ** 0.5
    if std == 0:
        return 0.0
    # 单期无风险利率
    rf_period = (1 + risk_free) ** (1.0 / periods_per_year) - 1.0
    return (mean_ret - rf_period) / std * math.sqrt(periods_per_year)


def calc_metrics(equity: Sequence[float], periods_per_year: int = 252,
                 risk_free: float = 0.0) -> dict:
    """一次性计算全部指标"""
    return {
        "累计收益率": total_return(equity),
        "年化收益率": annualized_return(equity, periods_per_year),
        "最大回撤": max_drawdown(equity),
        "夏普比率": sharpe_ratio(equity, periods_per_year, risk_free),
    }


def calc_benchmark_equity(data) -> list[float]:
    """
    基于数据源计算「买入持有」基准的净值曲线（归一化到 1.0）。
    直接用 close 序列归一化。
    """
    closes = [data.lines.close._array[i] for i in range(len(data.lines.close))]
    if not closes:
        return []
    base = closes[0]
    return [c / base for c in closes]


def normalize(equity: Sequence[float]) -> list[float]:
    """归一化到 1.0 起算"""
    if not equity:
        return []
    base = equity[0]
    return [v / base for v in equity]
