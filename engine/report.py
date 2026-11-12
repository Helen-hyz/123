"""
回测引擎 —— 基准对比与报告生成

在引擎内部完成作业要求的对比分析，不改动数据接入 / 策略信号 / 结果输出模块：
  1. 基准可选「买入持有（同标的）」或「沪深300」，并在报告中说明选择理由；
  2. 报告四项核心指标：累计收益率、年化收益率、最大回撤、夏普比率；
  3. 绘制策略与基准均归一化至 1.0 的净值曲线，标注买卖点，并划分样本内/样本外；
  4. 计算超额收益并做差异来源拆解（仓位暴露、交易成本、择时残差）。
"""
from __future__ import annotations

from typing import Optional

from output.metrics import (total_return, annualized_return,
                            max_drawdown, sharpe_ratio)


# ===================== 基础工具 =====================

def _metrics(equity: list[float]) -> dict:
    """四项核心指标"""
    return {
        "累计收益率": total_return(equity),
        "年化收益率": annualized_return(equity),
        "最大回撤": max_drawdown(equity),
        "夏普比率": sharpe_ratio(equity),
    }


def _normalize(equity: list[float]) -> list[float]:
    """归一化至 1.0 起算"""
    base = equity[0]
    return [v / base for v in equity]


def _split_index(dates: list[str], split_date: Optional[str], n: int) -> int:
    """确定样本内/样本外分界下标；未指定时按时间中点切分"""
    if not split_date:
        return n // 2
    for i, d in enumerate(dates):
        if d >= split_date:
            return i
    return n


def _load_csi300(dates: list[str], initial_cash: float):
    """通过 akshare 拉取沪深300指数并按其交易日对齐到策略日期。
    优先使用新浪源（stock_zh_index_daily），失败再试东方财富源。
    返回 (equity 序列, 说明) 或 (None, 失败说明)。
    """
    close_map: dict[str, float] = {}
    errors: list[str] = []
    try:
        import akshare as ak
        df = ak.stock_zh_index_daily(symbol="sh000300")
        close_map = {str(r["date"])[:10]: float(r["close"])
                     for _, r in df.iterrows()}
    except Exception as e:
        errors.append(f"新浪源{type(e).__name__}")
        try:
            import akshare as ak
            df = ak.index_zh_a_hist(symbol="000300", period="daily",
                                    start_date=dates[0].replace("-", ""),
                                    end_date=dates[-1].replace("-", ""))
            close_map = {str(r["日期"])[:10]: float(r["收盘"])
                         for _, r in df.iterrows()}
        except Exception as e2:
            errors.append(f"东财源{type(e2).__name__}")

    if not close_map:
        return None, ("沪深300获取失败（" + "、".join(errors) +
                      "），已回退买入持有")

    raw, last = [], None
    for d in dates:
        if d in close_map:
            last = close_map[d]
        raw.append(last)
    first = next((x for x in raw if x is not None), None)
    if first is None:
        return None, "沪深300与策略日期无重叠交易日，已回退买入持有"
    eq = [initial_cash if x is None else initial_cash * x / first for x in raw]
    return eq, "沪深300指数（akshare stock_zh_index_daily）"


def _benchmark_equity(kind: str, dates: list[str], closes: list[float],
                      initial_cash: float):
    """构造基准净值曲线，返回 (equity, 名称, 选择理由)"""
    if kind == "csi300":
        eq, note = _load_csi300(dates, initial_cash)
        if eq is not None:
            return eq, "沪深300", (
                "标的为沪深300成分股（贵州茅台），选沪深300作为市场基准，"
                "可分离个股 α 与市场 β，衡量择时是否跑赢大盘。")
        # 回退
        fallback_note = note
    else:
        fallback_note = ""

    eq = [initial_cash * c / closes[0] for c in closes]
    reason = ("选择「同标的买入持有」作为基准：与策略持有完全相同的资产，"
              "剥离选股差异，纯粹检验择时与风控是否创造超额收益；"
              "且不依赖额外行情源，结果可复现。")
    if fallback_note:
        reason = fallback_note + "；" + reason
    return eq, "买入持有", reason


# ===================== 对比分析 =====================

def build_comparison(cerebro, benchmark: str = "buyhold",
                     split_date: Optional[str] = None) -> dict:
    """基于已完成 run() 的引擎构建对比结果（不落盘）"""
    broker = cerebro.broker
    dates = list(broker.datetimes)
    strat = list(broker.equity_curve)
    n = len(dates)
    if n < 2:
        raise RuntimeError("回测数据不足，无法生成报告")

    closes = list(cerebro.datas[0].lines.close._array)[:n]
    if len(closes) < n and closes:
        closes += [closes[-1]] * (n - len(closes))
    initial = broker.initial_cash

    bench, bench_name, bench_reason = _benchmark_equity(
        benchmark, dates, closes, initial)

    k = _split_index(dates, split_date, n)
    k = max(1, min(k, n - 1))

    date_idx = {d: i for i, d in enumerate(dates)}
    trades = list(broker.trades)
    positions = list(broker.position_history)[:n]

    # 仓位暴露序列：持仓市值 / 总权益
    exposure = []
    for i in range(n):
        if strat[i]:
            exposure.append(max(0.0, min(1.0, positions[i] * closes[i] / strat[i])))
        else:
            exposure.append(0.0)

    segments = [("全样本", 0, n), ("样本内", 0, k), ("样本外", k, n)]
    seg_stats = []
    for name, a, b in segments:
        if b - a < 2:
            continue
        se, be = strat[a:b], bench[a:b]
        sm, bm = _metrics(se), _metrics(be)
        excess = sm["累计收益率"] - bm["累计收益率"]

        seg_trades = [t for t in trades
                      if t.datetime in date_idx and a <= date_idx[t.datetime] < b]
        commission = sum(t.commission for t in seg_trades)
        avg_expo = sum(exposure[a:b]) / (b - a)

        # 差异来源拆解（近似）
        exposure_contrib = bm["累计收益率"] * (avg_expo - 1.0)
        cost_drag = -commission / initial
        residual = excess - exposure_contrib - cost_drag

        seg_stats.append({
            "segment": name, "start": a, "end": b,
            "strategy": sm, "benchmark": bm,
            "excess": excess,
            "excess_annual": sm["年化收益率"] - bm["年化收益率"],
            "n_trades": len(seg_trades),
            "commission": commission,
            "avg_exposure": avg_expo,
            "exposure_contrib": exposure_contrib,
            "cost_drag": cost_drag,
            "residual": residual,
        })

    return {
        "strategy_name": cerebro.strategy.__class__.__name__,
        "strategy_params": _params_of(cerebro.strategy),
        "benchmark_name": bench_name,
        "benchmark_kind": benchmark,
        "benchmark_reason": bench_reason,
        "dates": dates,
        "strategy_equity": strat,
        "benchmark_equity": bench,
        "trades": trades,
        "split_index": k,
        "segments": seg_stats,
    }


def _params_of(strategy) -> dict:
    p = getattr(strategy, "p", None)
    if p is None:
        return {}
    defaults = getattr(p, "_defaults", {})
    return {k: getattr(p, k, v) for k, v in defaults.items()}


# ===================== 绘图 =====================

def plot_comparison(comp: dict, save_path: str) -> Optional[str]:
    """归一化净值曲线：策略 vs 基准，标注买卖点，区分样本内/外"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return None

    dates = comp["dates"]
    strat = _normalize(comp["strategy_equity"])
    bench = _normalize(comp["benchmark_equity"])
    n = len(dates)
    x = list(range(n))
    k = comp["split_index"]

    fig, ax = plt.subplots(figsize=(13, 6.5))
    ax.plot(x, strat, label="Strategy", color="#d62728", lw=1.6)
    ax.plot(x, bench, label=f"Benchmark: {comp['benchmark_name']}",
            color="#1f77b4", lw=1.2, alpha=0.85)

    # 样本内 / 样本外分区
    ax.axvspan(0, k, color="#2ca02c", alpha=0.05)
    ax.axvspan(k, n - 1, color="#7f7f7f", alpha=0.08)
    ax.axvline(k, color="#555555", ls="--", lw=0.9)
    ax.text(k / 2 if k > 0 else 0, ax.get_ylim()[1], "In-sample",
            ha="center", va="top", fontsize=10, color="#2ca02c")
    ax.text((k + n) / 2, ax.get_ylim()[1], "Out-of-sample",
            ha="center", va="top", fontsize=10, color="#555555")

    # 买卖点
    idx_map = {d: i for i, d in enumerate(dates)}
    buys = [(idx_map[t.datetime], strat[idx_map[t.datetime]])
            for t in comp["trades"]
            if t.side.value == "buy" and t.datetime in idx_map]
    sells = [(idx_map[t.datetime], strat[idx_map[t.datetime]])
             for t in comp["trades"]
             if t.side.value == "sell" and t.datetime in idx_map]
    if buys:
        ax.scatter([b[0] for b in buys], [b[1] for b in buys],
                   marker="^", color="green", s=55, zorder=5, label="Buy")
    if sells:
        ax.scatter([s[0] for s in sells], [s[1] for s in sells],
                   marker="v", color="red", s=55, zorder=5, label="Sell")

    ax.set_title("Normalized Equity Curve: Strategy vs Benchmark (base = 1.0)")
    ax.set_xlabel("Trading day index")
    ax.set_ylabel("Normalized equity")
    ax.legend(loc="upper left")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(save_path, dpi=150)
    plt.close(fig)
    return save_path


# ===================== 报告文本 =====================

def _pct(x: float) -> str:
    return f"{x*100:.2f}%"


def format_report(comp: dict, plot_path: Optional[str]) -> str:
    L: list[str] = []
    bar = "=" * 68
    L.append(bar)
    L.append("  回测报告：策略 vs 基准")
    L.append(bar)
    L.append(f"策略：{comp['strategy_name']}  参数：{comp['strategy_params']}")
    L.append(f"基准：{comp['benchmark_name']}")
    L.append(f"基准选择理由：{comp['benchmark_reason']}")
    L.append("")

    for seg in comp["segments"]:
        sm, bm = seg["strategy"], seg["benchmark"]
        L.append(f"--- {seg['segment']}（{seg['start']} ~ {seg['end']-1}，"
                 f"{seg['end']-seg['start']} 根）---")
        L.append(f"{'指标':<10}{'策略':>14}{'基准':>14}")
        for key in ("累计收益率", "年化收益率", "最大回撤", "夏普比率"):
            if key == "夏普比率":
                L.append(f"{key:<10}{sm[key]:>16.3f}{bm[key]:>14.3f}")
            else:
                L.append(f"{key:<10}{_pct(sm[key]):>15}{_pct(bm[key]):>14}")
        L.append(f"超额收益  {_pct(seg['excess']):>15}  "
                 f"（年化超额 {_pct(seg['excess_annual'])}）")
        L.append("差异来源拆解：")
        L.append(f"  仓位暴露贡献   {_pct(seg['exposure_contrib'])}  "
                 f"（平均仓位 {_pct(seg['avg_exposure'])}）")
        L.append(f"  交易成本拖累   {_pct(seg['cost_drag'])}  "
                 f"（手续费合计 {seg['commission']:.2f}）")
        L.append(f"  择时/选股残差 {_pct(seg['residual'])}")
        L.append(f"  交易笔数       {seg['n_trades']}")
        L.append("")

    L.append("说明：")
    L.append("  1. 基准与策略净值均归一化至 1.0 起算。")
    L.append("  2. 超额收益 = 策略累计收益率 - 基准累计收益率。")
    L.append("  3. 仓位暴露贡献 = 基准收益 × (平均仓位 - 1)，衡量未满仓的机会成本；")
    L.append("     交易成本拖累 = -累计手续费 / 初始资金；")
    L.append("     残差即择时与风控带来的净贡献。")
    if plot_path:
        L.append(f"  4. 净值对比图：{plot_path}")
    L.append(bar)
    return "\n".join(L)


# ===================== 一站式：构建 + 绘图 + 落盘 =====================

def build_and_save(cerebro, benchmark: str = "csi300",
                   split_date: Optional[str] = None,
                   prefix: str = "backtest", plot: bool = True) -> dict:
    comp = build_comparison(cerebro, benchmark=benchmark, split_date=split_date)

    plot_path = None
    if plot:
        plot_path = plot_comparison(comp, f"{prefix}_equity_compare.png")

    comp["plot_path"] = plot_path
    text = format_report(comp, plot_path)
    report_path = f"{prefix}_report.txt"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(text + "\n")

    comp["report_text"] = text
    comp["report_path"] = report_path
    print(text)
    print(f"[report] 报告已保存：{report_path}")
    if plot_path:
        print(f"[report] 净值对比图已保存：{plot_path}")
    return comp


# ===================== 引擎内自测入口 =====================
# 仅调用已有数据/策略模块，不做任何修改；用于独立验证引擎的对比报告能力。
if __name__ == "__main__":
    import os
    import sys

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    from data.sources import AkshareFeed
    from strategy.strategy import MACDStrategy
    from engine.cerebro import Cerebro

    cb = Cerebro(cash=1_000_000.0, commission=0.0003)
    cb.adddata(AkshareFeed("sh600519", "20200101", "20231231"))
    cb.addstrategy(MACDStrategy, period_me1=16, period_me2=32,
                   period_signal=9)
    cb.run()
    cb.report(benchmark="csi300", split_date="2022-01-01",
              prefix="backtest")

