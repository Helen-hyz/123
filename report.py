"""回测报告生成：分段指标、超额收益归因、Markdown 报告"""
from __future__ import annotations

from output.metrics import calc_metrics, exposure_ratio


def segment_indices(dates, start=None, end=None):
    """按日期区间取索引（ISO 日期可直接字典序比较）"""
    out = []
    for i, d in enumerate(dates):
        if start is not None and d < start:
            continue
        if end is not None and d > end:
            continue
        out.append(i)
    return out


def build_segment(name, idx, strat_equity, bench_norm, bh_norm, positions):
    """构造一个区间的横向对比与归因结果"""
    if not idx:
        return None
    s = [strat_equity[i] for i in idx]
    b = [bench_norm[i] for i in idx]
    h = [bh_norm[i] for i in idx]
    pos = [positions[i] for i in idx]

    sm, bm, hm = calc_metrics(s), calc_metrics(b), calc_metrics(h)
    total_excess = sm["累计收益率"] - bm["累计收益率"]
    selection = hm["累计收益率"] - bm["累计收益率"]   # 选股：标的买入持有 vs 基准
    timing = sm["累计收益率"] - hm["累计收益率"]      # 择时：策略 vs 买入持有
    return {
        "name": name,
        "n": len(idx),
        "strategy": sm,
        "bench": bm,
        "buyhold": hm,
        "excess": total_excess,
        "selection": selection,
        "timing": timing,
        "exposure": exposure_ratio(pos),
    }


def _pct(x: float) -> str:
    return f"{x * 100:+.2f}%"


def _num(x: float) -> str:
    return f"{x:.3f}"


def render_report(meta: dict, segments: list[dict], trade_stats: dict) -> str:
    """生成 Markdown 报告文本"""
    bn, bs = meta["bench_name"], meta["bench_symbol"]
    sn = meta["stock_name"]
    lines: list[str] = []
    lines.append(f"# 量化策略回测报告：MACD(16,32,9) vs {bn}")
    lines.append("")
    lines.append("## 1. 基准选择与理由")
    lines.append("")
    lines.append(f"- **主基准：{bn}（{bs}）**")
    if bs == "sh000300":
        lines.append(f"  - 标的{sn}是沪深300权重股，二者同属A股大盘蓝筹；"
                     "用沪深300作为市场基准可分离「市场β」与「选股/择时α」。")
        lines.append("  - 沪深300覆盖沪深两市300只大市值股票，是A股最常用的业绩比较基准，"
                     "数据可长期稳定获取（akshare 新浪源）。")
    else:
        lines.append("  - 因指数数据不可用，采用标的买入持有作为市场基准。")
    lines.append(f"- **辅助参照：{sn}买入持有**")
    lines.append("  - 与策略使用同一标的，用于把超额收益进一步分解为「选股」与「择时」。")
    lines.append("")
    lines.append("## 2. 数据与回测设置")
    lines.append("")
    lines.append(f"- 标的：{sn}（{meta['stock_symbol']}），前复权日线")
    lines.append(f"- 区间：{meta['start']} ~ {meta['end']}，共 {meta['n_bars']} 根 K 线")
    lines.append(f"- 样本内：{meta['in_start']} ~ {meta['in_end']}；"
                 f"样本外：{meta['oos_start']} ~ {meta['oos_end']}")
    lines.append(f"- 初始资金：{meta['cash']:,.0f} 元；单边佣金：{meta['commission']*100:.3f}%")
    lines.append("- 撮合规则：信号出现后下一根 bar 开盘价成交，避免未来函数")
    lines.append("")
    lines.append("## 3. 绩效横向对比")
    lines.append("")
    lines.append("四项必报指标：累计收益率、年化收益率、最大回撤、夏普比率。")
    lines.append("")
    for n, seg in enumerate(segments, start=1):
        lines.append(f"### 3.{n} {seg['name']}（{seg['n']} 个交易日）")
        lines.append("")
        lines.append(f"| 指标 | 策略 | {bn} | {sn}买入持有 |")
        lines.append("|---|---:|---:|---:|")
        s, b, h = seg["strategy"], seg["bench"], seg["buyhold"]
        lines.append(f"| 累计收益率 | {_pct(s['累计收益率'])} | {_pct(b['累计收益率'])} "
                     f"| {_pct(h['累计收益率'])} |")
        lines.append(f"| 年化收益率 | {_pct(s['年化收益率'])} | {_pct(b['年化收益率'])} "
                     f"| {_pct(h['年化收益率'])} |")
        lines.append(f"| 最大回撤 | {_pct(-s['最大回撤'])} | {_pct(-b['最大回撤'])} "
                     f"| {_pct(-h['最大回撤'])} |")
        lines.append(f"| 夏普比率 | {_num(s['夏普比率'])} | {_num(b['夏普比率'])} "
                     f"| {_num(h['夏普比率'])} |")
        lines.append("")
    lines.append("## 4. 超额收益及来源分解")
    lines.append("")
    lines.append(f"| 区间 | 超额收益(策略−{bn}) | 选股贡献 | 择时贡献 | 持仓天数占比 |")
    lines.append("|---|---:|---:|---:|---:|")
    for seg in segments:
        lines.append(f"| {seg['name']} | {_pct(seg['excess'])} | "
                     f"{_pct(seg['selection'])} | {_pct(seg['timing'])} | "
                     f"{seg['exposure']*100:.1f}% |")
    lines.append("")
    lines.append("**分解口径**：")
    lines.append("")
    lines.append(f"- 选股贡献 = {sn}买入持有 − {bn}（同区间持有标的相对市场的超额）")
    lines.append(f"- 择时贡献 = 策略 − {sn}买入持有（空仓/进出场带来的超额）")
    lines.append(f"- 超额收益 = 选股贡献 + 择时贡献 = 策略 − {bn}")
    lines.append("")
    lines.append("## 5. 交易与风险特征")
    lines.append("")
    lines.append(f"- 全样本交易笔数：{trade_stats['n_trades']} 笔"
                 f"（样本内 {trade_stats['in_trades']} 笔，样本外 {trade_stats['oos_trades']} 笔）")
    lines.append(f"- 全样本持仓天数占比：{trade_stats['exposure']*100:.1f}%")
    lines.append(f"- 最大回撤：策略 {_pct(-trade_stats['max_dd'])}，"
                 f"{bn} {_pct(-trade_stats['bench_max_dd'])}")
    lines.append("")
    lines.append("## 6. 归一化净值曲线")
    lines.append("")
    lines.append("![净值曲线](equity_curve.png)")
    lines.append("")
    lines.append("> 策略与基准均归一化至 1.0 起算；绿色▲为买入、红色▼为卖出；"
                 "虚线左侧为样本内、右侧为样本外。")
    lines.append("")
    lines.append("## 7. 结论与说明")
    lines.append("")
    if len(segments) >= 3:
        full, ins, oos = segments[0], segments[1], segments[2]
        lines.append(f"- 全样本超额收益 {_pct(full['excess'])}，其中选股贡献 "
                     f"{_pct(full['selection'])}、择时贡献 {_pct(full['timing'])}。")
        lines.append(f"- 择时在样本内为 {_pct(ins['timing'])}（牛市满仓买入持有更强），"
                     f"样本外为 {_pct(oos['timing'])}（熊市空仓避险）；"
                     f"策略的主要价值体现在回撤控制：最大回撤 "
                     f"{_pct(-full['strategy']['最大回撤'])}，"
                     f"低于沪深300的 {_pct(-full['bench']['最大回撤'])}。")
    lines.append(f"- 选股贡献反映{sn}相对{bn}的事后强弱，不等于可持续的选股能力；"
                 "样本外区间未重新调参，属于真正的样本外延续。")
    lines.append("- 局限：单标的、单参数组合，未计滑点与冲击成本，结论不宜直接外推。")
    lines.append("")
    return "\n".join(lines)


def write_report(path: str, meta: dict, segments: list[dict],
                 trade_stats: dict) -> str:
    text = render_report(meta, segments, trade_stats)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def print_comparison(segments: list[dict]) -> None:
    """控制台横向对比表"""
    print(f"\n{'区间':<8}{'对象':<14}{'累计收益':>10}{'年化':>9}"
          f"{'最大回撤':>10}{'夏普':>8}")
    print("-" * 59)
    for seg in segments:
        for label, m in [("策略", seg["strategy"]),
                         ("基准(市场)", seg["bench"]),
                         ("标的买入持有", seg["buyhold"])]:
            print(f"{seg['name']:<8}{label:<14}"
                  f"{m['累计收益率']*100:>+9.2f}%{m['年化收益率']*100:>+8.2f}%"
                  f"{-m['最大回撤']*100:>+9.2f}%{m['夏普比率']:>8.3f}")
        print(f"{'':<8}{'超额(策略-基准)':<14}"
              f"{seg['excess']*100:>+9.2f}%  "
              f"[选股 {seg['selection']*100:+.2f}% / "
              f"择时 {seg['timing']*100:+.2f}%]")
        print("-" * 59)
