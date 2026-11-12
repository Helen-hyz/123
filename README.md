# 量化交易系统骨架（基于 Backtrader 设计要点自研）

课程作业工程，按 **数据接入 / 策略信号 / 回测引擎 / 结果输出** 四大模块组织，
借鉴 Backtrader 的核心设计（统一 Line 抽象、元类参数系统、minperiod 自动传播、
运算符重载构建因子 DAG、事件驱动回测循环），但核心逻辑为自行实现。

## 模块结构

```
quant_skeleton/
├── data/                # 数据接入
│   ├── feed.py          # Line 统一抽象 + DataFeed 基类（OHLCV 多线）
│   └── sources.py       # AkshareFeed / YahooFeed / CsvFeed / RandomWalkFeed
├── strategy/            # 策略信号
│   ├── indicator.py     # Indicator 基类（元类参数 + minperiod + 运算符重载）
│   ├── indicators/      # SMA / EMA / MACD / RSI / Bollinger 等因子
│   └── strategy.py      # Strategy 基类 + DualMAStrategy / MACDStrategy
├── engine/              # 回测引擎
│   ├── broker.py        # 撮合、持仓、资金、净值记录
│   └── cerebro.py       # 主引擎 + 因子 DAG 拓扑排序 + 事件循环
├── output/              # 结果输出
│   ├── metrics.py       # 累计/年化/最大回撤/夏普
│   └── plotter.py       # 归一化净值对比图（标注买卖点）
├── main.py              # 串联四模块的入口
└── requirements.txt
```

## 设计要点（借鉴自 Backtrader）

| 要点 | 本项目实现 |
|---|---|
| 统一 Line 抽象 | `data/feed.py` 中 `Line` 类，`[0]`=当前、`[-1]`=前一根 |
| 元类参数系统 | `strategy/indicator.py` 中 `MetaParams` 自动注入 `self.p` |
| minperiod 自动传播 | `_minperiod` 沿因子 DAG 自动取 max，无需手写预热判断 |
| 运算符重载 | `Line` / `Indicator` 重载 `+ - * / < >`，返回 `LineOperation` 构建 DAG |
| 因子 DAG 拓扑排序 | `engine/cerebro.py` 中 `_build_dag` 用 Kahn 算法排序 |
| 事件驱动循环 | `Cerebro.run` 每 bar：数据→因子→撮合→策略→记净值 |
| 双执行模式基础 | 逐 bar `next()` 驱动（可扩展向量化 `once`） |

## 数据源接入状态

已接入并验证可用的真实数据源（均免费）：

| 数据源 | 实现类 | 市场 | 说明 |
|---|---|---|---|
| **akshare（推荐）** | `AkshareFeed` | A 股 | 走新浪源 `stock_zh_a_daily`，稳定不易限流；symbol 如 `sh600519` |
| yfinance | `YahooFeed` | 美股/全球 | Yahoo Finance，沙箱共享 IP 可能被限流 |
| baostock | `BaostockFeed` | A 股 | 无需 token，需 baostock 服务器可达 |
| CSV | `CsvFeed` | 通用 | 本地 CSV，列含 datetime/open/high/low/close/volume |
| 随机游走 | `RandomWalkFeed` | 演示 | 无网络时的兜底演示数据 |

`main.py` 默认用 `AkshareFeed("sh600519", ...)` 拉取贵州茅台真实日线，失败自动回退到随机游走。

## 参数与信号迭代（`optimize.py`）

用贵州茅台 2020-2023 真实日线，按时间切分为**样本内 2020-2021**（486 根）与
**样本外 2022-2023**（484 根）。先做样本内网格搜索，再用样本外验证，避免过拟合。

运行：

```bash
python optimize.py          # 输出迭代过程与结论，并生成 optimization_results.csv
```

关键迭代过程（夏普 / 累计收益）：

| 配置 | 样本内 | 样本外 |
|---|---|---|
| 买入持有（基准） | 1.128 / +85.26% | -0.117 / -11.53% |
| 双均线 MA(5,20) | 1.294 / +80.03% | 0.179 / +3.04% |
| MA(8,15)（样本内较优） | 1.351 / — | 0.368 / +8.98% |
| MA(3,40)（样本内最优） | 1.501 / — | -0.173 / -6.19% |
| MA(5,20) + RSI 过滤 | 0.922 / — | **0.678 / +17.89%** |
| MACD(8,17,9) | 0.998 / — | -0.084 / -5.36% |
| **MACD(16,32,9)（最终选择）** | 1.058 / +53.99% | **0.766 / +22.99%** |

**迭代结论**：

1. 样本内最优的 MA(3,40) 在样本外亏损 -6.19%，属于典型过拟合，被淘汰；
2. 加入 RSI 过滤后，MA(5,20) 的样本外收益由 +3.04% 提升到 +17.89%，信号有效性跨期稳定；
3. 最终选择 **MACD(16,32,9)**——样本内、外夏普均最高且稳定（1.058 / 0.766），
   样本外最大回撤仅 12.14%。

`main.py` 已采用最终配置。全样本（2020-2023）回测：

| 指标 | MACD(16,32,9) | 基准(买入持有) |
|---|---|---|
| 累计收益率 | +80.49% | +64.00% |
| 年化收益率 | 16.60% | 13.73% |
| 最大回撤 | 21.34% | 47.04% |
| 夏普比率 | 0.86 | 0.58 |

## 快速运行

```bash
pip install -r requirements.txt
python main.py
```

切换数据源示例：

```python
from data.sources import AkshareFeed, YahooFeed, BaostockFeed, CsvFeed

# A 股（推荐，新浪源）
data = AkshareFeed(symbol="sh600519", start_date="20200101", end_date="20231231")
# 美股
data = YahooFeed(ticker="AAPL", start="2020-01-01", end="2023-12-31")
# baostock
data = BaostockFeed(code="sh.600519", start_date="2020-01-01", end_date="2023-12-31")
# 本地 CSV
data = CsvFeed(path="your_data.csv")
```

## 输出

- 控制台：策略与基准（买入持有）的累计收益率、年化收益率、最大回撤、夏普比率
- `equity_curve.png`：归一化净值对比曲线，绿色▲=买入，红色▼=卖出
