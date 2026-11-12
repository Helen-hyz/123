"""
具体因子实现：SMA / EMA / MACD / RSI / Bollinger

全部采用「声明式 __init__」风格：只写公式，minperiod 与 DAG 由框架自动管理。
"""
from __future__ import annotations

from strategy.indicator import Indicator, Average, Max, Min, StdDev, PeriodN
from data.feed import Line


# -------------------- 移动平均族 --------------------

class SMA(Indicator):
    """简单移动平均"""
    lines = ("sma",)
    params = {"period": 30}

    def __init__(self, data, **kwargs):
        super().__init__(data, **kwargs)
        self.lines.sma = Average(self.data, period=self.p.period)


class EMA(Indicator):
    """指数移动平均（递推实现）"""
    lines = ("ema",)
    params = {"period": 20}

    def __init__(self, data, **kwargs):
        super().__init__(data, **kwargs)
        self._alpha = 2.0 / (self.p.period + 1)
        self._prev: float | None = None
        # EMA 预热期：在输入周期基础上叠加 period
        self.addminperiod(self.p.period)

    def next(self) -> None:
        if self.data is None:
            return
        val = self.data[0]
        if val != val:  # NaN：预热期，避免污染递推状态
            self.lines.ema.set(float("nan"))
            return
        if self._prev is None:
            self._prev = val
        else:
            self._prev = self._alpha * val + (1 - self._alpha) * self._prev
        self.lines.ema.set(self._prev)


class SMMA(Indicator):
    """Wilder 平滑移动平均（RSI 用）"""
    lines = ("smma",)
    params = {"period": 14}

    def __init__(self, data, **kwargs):
        super().__init__(data, **kwargs)
        self._prev: float | None = None
        self._sum: float = 0.0
        self._count = 0
        # Wilder 平滑：在输入周期基础上叠加 period
        self.addminperiod(self.p.period)

    def next(self) -> None:
        if self.data is None:
            return
        val = self.data[0]
        if val != val:  # NaN：预热期，跳过该值
            self.lines.smma.set(float("nan"))
            return
        self._count += 1
        if self._count <= self.p.period:
            self._sum += val
            if self._count == self.p.period:
                self._prev = self._sum / self.p.period
        else:
            self._prev = (self._prev * (self.p.period - 1) + val) / self.p.period
        if self._prev is not None:
            self.lines.smma.set(self._prev)


# 移动平均族注册表（可通过参数切换平滑算法）
class MovAv:
    Simple = SMA
    Exponential = EMA
    Smoothed = SMMA


# -------------------- MACD --------------------

class MACD(Indicator):
    """
    MACD = EMA(short) - EMA(long)
    signal = EMA(MACD, signal_period)
    """
    lines = ("macd", "signal")
    params = {"period_me1": 12, "period_me2": 26, "period_signal": 9,
              "movav": EMA}

    def __init__(self, data, **kwargs):
        super().__init__(data, **kwargs)
        me1 = self.p.movav(self.data, period=self.p.period_me1)
        me2 = self.p.movav(self.data, period=self.p.period_me2)
        self.lines.macd = me1 - me2
        self.lines.signal = self.p.movav(self.lines.macd,
                                         period=self.p.period_signal)


# -------------------- RSI --------------------

class UpDay(PeriodN):
    """上涨日幅度：max(close - close[n], 0)"""
    lines = ("upday",)
    params = {"period": 1}

    def next(self) -> None:
        if self.data is not None:
            diff = self.data[0] - self.data[-self.p.period]
            self.line.set(max(diff, 0.0))


class DownDay(PeriodN):
    """下跌日幅度：max(close[n] - close, 0)"""
    lines = ("downday",)
    params = {"period": 1}

    def next(self) -> None:
        if self.data is not None:
            diff = self.data[-self.p.period] - self.data[0]
            self.line.set(max(diff, 0.0))


class RSI(Indicator):
    """
    RSI = 100 - 100/(1 + RS), RS = SMA(upday) / SMA(downday)
    默认 Wilder 平滑（SMMA），可通过 movav 参数切换。
    """
    lines = ("rsi",)
    params = {"period": 14, "movav": SMMA, "lookback": 1}

    def __init__(self, data, **kwargs):
        super().__init__(data, **kwargs)
        up = UpDay(self.data, period=self.p.lookback)
        down = DownDay(self.data, period=self.p.lookback)
        maup = self.p.movav(up, period=self.p.period)
        madown = self.p.movav(down, period=self.p.period)
        # 防止全涨行情下 madown=0 导致除零
        rs = maup / (madown + 1e-12)
        self.lines.rsi = 100.0 - 100.0 / (1.0 + rs)


# -------------------- Bollinger Bands --------------------

class BollingerBands(Indicator):
    lines = ("mid", "top", "bot")
    params = {"period": 20, "devfactor": 2.0, "movav": SMA}

    def __init__(self, data, **kwargs):
        super().__init__(data, **kwargs)
        mid = self.p.movav(self.data, period=self.p.period)
        std = StdDev(self.data, period=self.p.period)
        self.lines.mid = mid
        self.lines.top = mid + self.p.devfactor * std
        self.lines.bot = mid - self.p.devfactor * std
