"""回测引擎模块：Cerebro 主引擎 + Broker 撮合
基准对比报告见 ``engine.report``（按需导入，避免循环依赖）。
"""
from .broker import Broker, Order, OrderSide, OrderStatus, Trade
from .cerebro import Cerebro

__all__ = ["Broker", "Order", "OrderSide", "OrderStatus", "Trade", "Cerebro"]
