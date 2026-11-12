"""
回测引擎 —— Broker：撮合、持仓、资金、净值

简化的撮合模型：
  - 市价单在下一根 bar 的开盘价成交（避免未来函数）。
  - 手续费按成交金额的固定比例收取。
  - 支持买入/卖出/平仓，记录每笔成交与每日净值。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class OrderSide(Enum):
    BUY = "buy"
    SELL = "sell"


class OrderStatus(Enum):
    SUBMITTED = "submitted"
    FILLED = "filled"
    CANCELLED = "cancelled"


@dataclass
class Order:
    side: OrderSide
    size: Optional[int] = None      # None 表示由 broker 计算
    price: Optional[float] = None   # None 表示市价
    status: OrderStatus = OrderStatus.SUBMITTED
    fill_price: float = 0.0
    fill_size: int = 0

    @property
    def is_buy(self) -> bool:
        return self.side == OrderSide.BUY


@dataclass
class Trade:
    """一笔已成交的交易记录"""
    side: OrderSide
    size: int
    price: float
    commission: float
    datetime: str


class Broker:
    """
    简单撮合商。
    由 Cerebro 每根 bar 驱动：先接收策略提交的订单，下一根 bar 开盘时撮合成交。
    """

    def __init__(self, cash: float = 1_000_000.0, commission: float = 0.0003) -> None:
        self.initial_cash = cash
        self.cash = cash
        self.commission_rate = commission

        self.position: int = 0              # 持仓股数（正=多头）
        self.avg_cost: float = 0.0          # 持仓均价
        self._pending_orders: list[Order] = []

        # 记录每日净值与交易
        self.equity_curve: list[float] = []
        self.datetimes: list[str] = []
        self.trades: list[Trade] = []

    # ---------- 策略下单接口 ----------
    def buy(self, size: Optional[int] = None, price: Optional[float] = None) -> Order:
        order = Order(side=OrderSide.BUY, size=size, price=price)
        self._pending_orders.append(order)
        return order

    def sell(self, size: Optional[int] = None, price: Optional[float] = None) -> Order:
        order = Order(side=OrderSide.SELL, size=size, price=price)
        self._pending_orders.append(order)
        return order

    # ---------- 撮合（由 Cerebro 在新 bar 开始时调用）----------
    def next(self, open_price: float, datetime_str: str) -> None:
        """
        以当前 bar 的开盘价撮合上一根 bar 提交的订单。
        市价单 size=None 时：买单用全部可用资金，卖单平掉全部持仓。
        """
        for order in self._pending_orders:
            # 确定成交股数
            if order.size is not None:
                size = order.size
            elif order.is_buy:
                # 全仓买入（预留手续费）
                affordable = int(self.cash / (open_price * (1 + self.commission_rate)))
                size = affordable
            else:
                size = self.position  # 全仓卖出

            if size <= 0:
                order.status = OrderStatus.CANCELLED
                continue

            if order.is_buy:
                cost = size * open_price
                commission = cost * self.commission_rate
                if self.cash < cost + commission:
                    order.status = OrderStatus.CANCELLED
                    continue
                self.cash -= (cost + commission)
                # 更新持仓均价
                total_cost = self.avg_cost * self.position + cost
                self.position += size
                self.avg_cost = total_cost / self.position if self.position else 0.0
            else:
                size = min(size, self.position)
                if size <= 0:
                    order.status = OrderStatus.CANCELLED
                    continue
                proceeds = size * open_price
                commission = proceeds * self.commission_rate
                self.cash += (proceeds - commission)
                self.position -= size
                if self.position == 0:
                    self.avg_cost = 0.0

            order.status = OrderStatus.FILLED
            order.fill_price = open_price
            order.fill_size = size
            self.trades.append(Trade(
                side=order.side, size=size, price=open_price,
                commission=commission, datetime=datetime_str,
            ))

        self._pending_orders.clear()

    # ---------- 净值 ----------
    @property
    def value(self) -> float:
        """当前总权益 = 现金 + 持仓市值（用最近成交价，由 Cerebro 更新）"""
        return self.cash + self.position * self._last_price

    def update_market_value(self, price: float) -> None:
        self._last_price = price

    def record_equity(self, datetime_str: str) -> None:
        self.datetimes.append(datetime_str)
        self.equity_curve.append(self.value)

    # ---------- 便捷属性 ----------
    @property
    def market_value(self) -> float:
        return self.position * self._last_price
