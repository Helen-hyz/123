"""
回测引擎 —— Cerebro：主引擎与事件循环

设计借鉴 Backtrader：
  1. 统一驱动：每根 bar 先推进数据，再按拓扑序计算所有因子，再撮合，再调策略。
  2. 拓扑排序：因子之间通过运算符构成 DAG，引擎自动做拓扑排序保证计算顺序。
  3. minperiod 门槛：只有当数据长度超过所有因子的最大 minperiod 后，才调用策略 next()。
  4. 撮合时机：市价单在下一根 bar 开盘成交，避免未来函数。
"""
from __future__ import annotations

from typing import Optional

from data.feed import DataFeed, Line, _is_computable
from .broker import Broker


class Cerebro:
    """
    回测主控。

    用法：
        cb = Cerebro()
        cb.adddata(feed)
        cb.addstrategy(DualMAStrategy, fast_period=5, slow_period=20)
        cb.broker.set_cash(1_000_000)
        result = cb.run()
    """

    def __init__(self, cash: float = 1_000_000.0, commission: float = 0.0003) -> None:
        self.datas: list[DataFeed] = []
        self._strategy_cls = None
        self._strategy_kwargs: dict = {}
        self.broker = Broker(cash=cash, commission=commission)
        self.strategy: Optional[Strategy] = None

        # 因子 DAG 拓扑序（run 时构建）
        self._compute_order: list = []

    # ---------- 配置接口 ----------
    def adddata(self, data: DataFeed) -> None:
        self.datas.append(data)

    def addstrategy(self, strategy_cls, **kwargs) -> None:
        self._strategy_cls = strategy_cls
        self._strategy_kwargs = kwargs

    def set_cash(self, cash: float) -> None:
        self.broker = Broker(cash=cash, commission=self.broker.commission_rate)

    def set_commission(self, rate: float) -> None:
        self.broker.commission_rate = rate

    # ---------- 因子 DAG 收集与拓扑排序 ----------
    def _build_dag(self) -> None:
        """从策略的指标出发，递归收集所有可计算节点并拓扑排序"""
        nodes = []
        seen: set[int] = set()

        def collect(node) -> None:
            if id(node) in seen:
                return
            seen.add(id(node))
            nodes.append(node)
            # 递归收集依赖
            for dep in getattr(node, "_deps", []):
                collect(dep)
            # 指标的输出线可能是子指标，也要收集
            if hasattr(node, "lines"):
                for ln in node.lines:
                    if _is_computable(ln):
                        collect(ln)

        for ind in self.strategy._indicators:
            collect(ind)

        # Kahn 拓扑排序：对于每条边 dep -> n（dep 先算），n 的入度 +1
        in_degree = {id(n): 0 for n in nodes}
        for n in nodes:
            for dep in getattr(n, "_deps", []):
                if id(dep) in in_degree:
                    in_degree[id(n)] = in_degree[id(n)] + 1

        queue = [n for n in nodes if in_degree[id(n)] == 0]
        order = []
        order_ids = set()
        while queue:
            n = queue.pop(0)
            order.append(n)
            order_ids.add(id(n))
            for m in nodes:
                # 用 identity 判断 n 是否是 m 的依赖（避免 __eq__ 被重载）
                if any(n is dep for dep in getattr(m, "_deps", [])):
                    in_degree[id(m)] -= 1
                    if in_degree[id(m)] == 0:
                        queue.append(m)
        # 若有环（不应发生），剩余节点追加到末尾
        for n in nodes:
            if id(n) not in order_ids:
                order.append(n)
        self._compute_order = order

    # ---------- 主循环 ----------
    def run(self) -> dict:
        if not self.datas:
            raise RuntimeError("请先通过 adddata() 添加数据源")
        if self._strategy_cls is None:
            raise RuntimeError("请先通过 addstrategy() 添加策略")

        data = self.datas[0]  # 单数据源骨架；多数据源可扩展
        self.strategy = self._strategy_cls(data, self.broker, **self._strategy_kwargs)
        self.strategy.start()
        self._build_dag()

        # 全局最小周期：所有因子 minperiod 的最大值
        global_minperiod = 1
        for node in self._compute_order:
            mp = getattr(node, "_minperiod", 1)
            if mp > global_minperiod:
                global_minperiod = mp

        bar_count = 0
        while data.next():
            bar_count += 1
            # 更新 broker 市值（用当前收盘价）
            self.broker.update_market_value(data.close[0])

            # 1) 撮合上一根 bar 提交的订单（用当前开盘价）
            self.broker.next(data.open[0], data.current_datetime)
            self.broker.update_market_value(data.close[0])

            # 2) 按拓扑序计算所有因子
            for node in self._compute_order:
                # 先转发输出线（追加 nan 槽位）
                for out in node.output_lines:
                    out.forward(float("nan"))
                # 再计算当前值
                node.next()

            # 3) 调用策略（仅在数据量超过全局 minperiod 后）
            if bar_count >= global_minperiod:
                self.strategy.next()

            # 4) 记录当日净值
            self.broker.update_market_value(data.close[0])
            self.broker.record_equity(data.current_datetime)

        self.strategy.stop()
        return self._summary()

    def _summary(self) -> dict:
        return {
            "initial_cash": self.broker.initial_cash,
            "final_value": self.broker.value,
            "total_return": (self.broker.value - self.broker.initial_cash)
                             / self.broker.initial_cash,
            "n_trades": len(self.broker.trades),
            "equity_curve": self.broker.equity_curve,
            "datetimes": self.broker.datetimes,
            "trades": self.broker.trades,
            "position_history": self.broker.position_history,
        }
