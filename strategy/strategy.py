"""
策略信号模块 —— Strategy 基类与示例策略

策略负责：在每根 bar 上读取因子信号，向 broker 下单。
策略不直接撮合，只产生订单意图；由 engine/broker 负责成交与持仓。
"""
from __future__ import annotations

from typing import Optional

from data.feed import DataFeed, Line
from engine.broker import Broker, Order


def _nan(x) -> bool:
    """NaN 判断（x != x 仅对 NaN 成立）"""
    return x != x


class Strategy:
    """
    策略基类。

    子类覆写 next() 实现交易逻辑，通过 self.buy()/self.sell()/self.close() 下单。
    引擎每根 bar 调用一次 next()。
    """

    params: dict = {}

    def __init__(self, data: DataFeed, broker: Broker, **kwargs) -> None:
        self.data = data
        self.broker = broker
        # 合并参数
        from .indicator import Params
        self.p = Params({**self.__class__.params, **kwargs})

        # 用户在 __init__ 中创建的 indicator 会被引擎收集
        self._indicators: list = []

    def _register_indicator(self, ind) -> None:
        self._indicators.append(ind)
        ind._owner = self

    # ---------- 下单接口（面向策略作者）----------
    def buy(self, size: Optional[int] = None, price: Optional[float] = None) -> Order:
        """市价买入；size=None 时由 broker 根据可用资金计算"""
        return self.broker.buy(size=size, price=price)

    def sell(self, size: Optional[int] = None, price: Optional[float] = None) -> Order:
        """市价卖出"""
        return self.broker.sell(size=size, price=price)

    def close(self) -> Optional[Order]:
        """平掉当前全部持仓"""
        pos = self.broker.position
        if pos > 0:
            return self.broker.sell(size=pos)
        elif pos < 0:
            return self.broker.buy(size=-pos)
        return None

    @property
    def position(self) -> int:
        return self.broker.position

    @property
    def cash(self) -> float:
        return self.broker.cash

    @property
    def value(self) -> float:
        return self.broker.value

    # ---------- 生命周期钩子 ----------
    def next(self) -> None:
        """每根 bar 调用一次，子类覆写"""
        pass

    def start(self) -> None:
        """回测开始前调用"""
        pass

    def stop(self) -> None:
        """回测结束时调用"""
        pass


# ===================== 示例策略：双均线金叉死叉 =====================

class DualMAStrategy(Strategy):
    """
    经典双均线策略：
      - 短期均线上穿长期均线 -> 买入（全仓）
      - 短期均线下穿长期均线 -> 卖出（清仓）
    """
    params = {"fast_period": 5, "slow_period": 20}

    def __init__(self, data, broker, **kwargs):
        super().__init__(data, broker, **kwargs)
        from .indicators import SMA
        # 在策略中声明因子
        self.fast_ma = SMA(data.close, period=self.p.fast_period)
        self.slow_ma = SMA(data.close, period=self.p.slow_period)
        self._register_indicator(self.fast_ma)
        self._register_indicator(self.slow_ma)

    def next(self) -> None:
        # 因子未就绪则跳过
        if len(self.fast_ma.lines.sma) < 2 or len(self.slow_ma.lines.sma) < 2:
            return
        fm = self.fast_ma.lines.sma
        sm = self.slow_ma.lines.sma
        if _nan(fm[-1]) or _nan(fm[0]) or _nan(sm[-1]) or _nan(sm[0]):
            return

        # 金叉：fast 从下方穿越 slow（统一用前一根/当前根比较）
        if fm[-1] <= sm[-1] and fm[0] > sm[0] and self.position == 0:
            self.buy()
        # 死叉：fast 从上方穿越 slow
        elif fm[-1] >= sm[-1] and fm[0] < sm[0] and self.position > 0:
            self.close()


# ===================== 示例策略：MACD 金叉 =====================

class MACDStrategy(Strategy):
    params = {"period_me1": 12, "period_me2": 26, "period_signal": 9}

    def __init__(self, data, broker, **kwargs):
        super().__init__(data, broker, **kwargs)
        from .indicators import MACD
        self.macd = MACD(data.close,
                         period_me1=self.p.period_me1,
                         period_me2=self.p.period_me2,
                         period_signal=self.p.period_signal)
        self._register_indicator(self.macd)

    def next(self) -> None:
        if len(self.macd.lines.signal) < 2:
            return
        macd = self.macd.lines.macd
        sig = self.macd.lines.signal
        if _nan(macd[-1]) or _nan(macd[0]) or _nan(sig[-1]) or _nan(sig[0]):
            return
        if macd[-1] <= sig[-1] and macd[0] > sig[0] and self.position == 0:
            self.buy()
        elif macd[-1] >= sig[-1] and macd[0] < sig[0] and self.position > 0:
            self.close()


# ===================== 迭代版信号：双均线 + 趋势/超买过滤 =====================

class FilteredMAStrategy(Strategy):
    """
    在双均线金叉基础上增加两道过滤（信号迭代）：

      1) 趋势过滤 use_trend：仅当慢线向上（slow_ma[0] > slow_ma[-1]）才允许买入，
         避免在下跌趋势中的「假金叉」。
      2) RSI 过滤 use_rsi：
         - 买入前要求 RSI < rsi_upper（不在超买区追高）；
         - 持仓中若 RSI > rsi_overbought（默认 75）则提前止盈。

    两个开关默认关闭，由参数搜索决定是否启用。
    """
    params = {
        "fast_period": 5, "slow_period": 20,
        "rsi_period": 14, "rsi_upper": 70, "rsi_overbought": 75,
        "use_trend": False, "use_rsi": False,
    }

    def __init__(self, data, broker, **kwargs):
        super().__init__(data, broker, **kwargs)
        from .indicators import SMA, RSI
        self.fast_ma = SMA(data.close, period=self.p.fast_period)
        self.slow_ma = SMA(data.close, period=self.p.slow_period)
        self._register_indicator(self.fast_ma)
        self._register_indicator(self.slow_ma)
        self._rsi = None
        if self.p.use_rsi:
            self._rsi = RSI(data.close, period=self.p.rsi_period)
            self._register_indicator(self._rsi)

    def next(self) -> None:
        fm = self.fast_ma.lines.sma
        sm = self.slow_ma.lines.sma
        if len(fm) < 2 or len(sm) < 2:
            return
        if (_nan(fm[-1]) or _nan(fm[0]) or _nan(sm[-1]) or _nan(sm[0])):
            return

        golden = fm[-1] <= sm[-1] and fm[0] > sm[0]
        death = fm[-1] >= sm[-1] and fm[0] < sm[0]

        if self.position == 0 and golden:
            ok = True
            if self.p.use_trend and not (sm[0] > sm[-1]):
                ok = False
            if self.p.use_rsi and self._rsi is not None:
                rv = self._rsi.lines.rsi[0]
                if _nan(rv) or rv >= self.p.rsi_upper:
                    ok = False
            if ok:
                self.buy()
        elif self.position > 0:
            if death:
                self.close()
            elif self.p.use_rsi and self._rsi is not None:
                rv = self._rsi.lines.rsi[0]
                if not _nan(rv) and rv > self.p.rsi_overbought:
                    self.close()
