"""
策略信号模块 —— Indicator 基类与参数系统

借鉴 Backtrader 的设计要点：
  1. 元类 MetaParams 自动收集 params 元组，在 __init__ 前注入到 self.p。
  2. Indicator 在 __init__ 中用运算符声明式地组装因子，框架自动构建 DAG。
  3. minperiod 沿输入线自动传播，预热期正确性由框架保证。
  4. 引擎每根 bar 调用 indicator.next()，只在 minperiod 满足后产出有效值。
"""
from __future__ import annotations

import operator
from typing import Any, Iterable

from data.feed import Line, LineDelay, LineOperation, LineOwnOperation, Lines
from data.feed import _is_computable


# ===================== 参数系统（元类自动注入）=====================

class Params:
    """参数容器，通过元类自动填充字段"""

    def __init__(self, items: dict) -> None:
        for k, v in items.items():
            setattr(self, k, v)
        self._defaults = dict(items)

    def isdefault(self, name: str) -> bool:
        return getattr(self, name) == self._defaults.get(name)

    def notdefault(self, name: str) -> bool:
        return not self.isdefault(name)


class MetaParams(type):
    """
    元类：在类创建时收集 params 元组，合并基类 params；
    在实例化时从 kwargs 中提取参数值注入到 self.p。
    """

    def __new__(meta, name, bases, dct):
        # 取出本类声明的 params
        new_params = dict(dct.pop("params", {}))
        # 合并所有基类的 params
        merged: dict = {}
        for base in bases:
            base_params = getattr(base, "_param_defs", {})
            merged.update(base_params)
        merged.update(new_params)
        dct["_param_defs"] = merged
        return super().__new__(meta, name, bases, dct)

    def __call__(cls, *args, **kwargs):
        # 实例化前：把 kwargs 中匹配的参数取出来，剩下的交给 __init__
        param_defs = getattr(cls, "_param_defs", {})
        params = Params(param_defs)
        for pname, pdef in param_defs.items():
            if pname in kwargs:
                setattr(params, pname, kwargs.pop(pname))
        # 先创建对象（不经 __init__），注入 params，再调 __init__
        obj = cls.__new__(cls)
        obj.p = obj.params = params
        obj.__init__(*args, **kwargs)
        return obj


# ===================== Indicator 基类 =====================

class Indicator(metaclass=MetaParams):
    """
    因子/指标基类。

    子类只需声明：
        lines  = ('sma',)            # 输出线名称
        params = {'period': 30}      # 参数及默认值

    并在 __init__ 中用运算符组装公式，例如：
        self.lines.sma = Average(self.data, period=self.p.period)

    minperiod 会从输入线与 period 自动传播，无需手写预热判断。
    """

    # 子类覆盖：输出线名称
    lines: tuple = ()
    # 子类覆盖：参数默认值
    params: dict = {}

    def __init__(self, *args, **kwargs) -> None:
        # 收集输入线（args 中所有 Line/Indicator）
        self._inputs: list[Line] = []
        for a in args:
            if isinstance(a, Indicator):
                # 取指标的第一条输出线作为输入
                self._inputs.append(a.lines[0])
            elif isinstance(a, Line):
                self._inputs.append(a)
        # 第一条输入线作为 self.data（若存在）
        self.data: Line | None = self._inputs[0] if self._inputs else None

        # 创建输出 line 容器
        self.lines = Lines(self.__class__.lines)

        # minperiod 初始化：取所有输入线的最大 minperiod
        self._minperiod = 1
        for inp in self._inputs:
            if inp._minperiod > self._minperiod:
                self._minperiod = inp._minperiod

        # 依赖：原始操作数中的可计算节点（Indicator / LineOperation 等）
        # 注意要用原始 args，而不是解析后的普通 Line，否则会丢失对指标节点的依赖
        self._deps = [a for a in args if _is_computable(a)]

        # 注册到引擎（由 Cerebro 在创建时设置 _owner）
        self._owner = None

    @property
    def line(self) -> Line:
        """第一条输出线的别名（self.line 等价于 self.lines[0]）"""
        return self.lines[0]

    @property
    def output_lines(self) -> list:
        """需要引擎转发的输出线：仅包含「非子指标」的纯 Line。
        若某条输出线被赋值为子指标，则由子指标自身负责转发。"""
        outs = []
        for ln in self.lines:
            if not _is_computable(ln):
                outs.append(ln)
        return outs

    def addminperiod(self, n: int) -> None:
        """在 period 基础上累加（period 类调用）"""
        self._minperiod = max(self._minperiod, self._minperiod + n - 1)
        for line in self.lines:
            line.updateminperiod(self._minperiod)

    def updateminperiod(self, n: int) -> None:
        if n > self._minperiod:
            self._minperiod = n
            for line in self.lines:
                line.updateminperiod(n)

    def next(self) -> None:
        """
        每根 bar 由引擎调用。默认：若只声明了 lines 并在 __init__ 里
        赋值为运算结果，则无需覆写 next；运算线会自己 next()。
        需自定义滑动窗口逻辑时覆写此方法。
        """
        pass

    # 让 Indicator 可像 Line 一样被引用（取第一条输出线）
    def __getitem__(self, ago: int) -> float:
        return self.lines[0][ago]

    def __len__(self) -> int:
        return len(self.lines[0])

    # ---------- 运算符重载：把指标自身作为操作数传入 LineOperation ----------
    # （不传 self.lines[0]，否则 LineOperation 无法建立对本指标的依赖）
    def __add__(self, other): return LineOperation(self, other, operator.add)
    def __radd__(self, other): return LineOperation(other, self, operator.add)
    def __sub__(self, other): return LineOperation(self, other, operator.sub)
    def __rsub__(self, other): return LineOperation(other, self, operator.sub)
    def __mul__(self, other): return LineOperation(self, other, operator.mul)
    def __rmul__(self, other): return LineOperation(other, self, operator.mul)
    def __truediv__(self, other): return LineOperation(self, other, operator.truediv)
    def __rtruediv__(self, other): return LineOperation(other, self, operator.truediv)
    def __lt__(self, other): return LineOperation(self, other, operator.lt)
    def __le__(self, other): return LineOperation(self, other, operator.le)
    def __gt__(self, other): return LineOperation(self, other, operator.gt)
    def __ge__(self, other): return LineOperation(self, other, operator.ge)
    def __neg__(self): return LineOwnOperation(self, operator.neg)
    def __abs__(self): return LineOwnOperation(self, operator.abs)
    def __call__(self, ago: int = 0):
        return self.lines[0](ago)

    @property
    def _minperiod_line(self) -> int:
        return max((l._minperiod for l in self.lines), default=1)


# ===================== 基础运算指标（构成因子的原子节点）=====================

class PeriodN(Indicator):
    """带 period 参数的指标基类，自动把 period 计入 minperiod"""
    params = {"period": 1}

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.addminperiod(self.p.period)


class OperationN(PeriodN):
    """对过去 period 个值执行 func 的滑动窗口指标"""
    lines = ("line",)
    func = None  # 子类指定

    def next(self) -> None:
        if self.data is not None and self.func is not None:
            window = self.data.get(size=self.p.period)
            if len(window) < self.p.period:
                self.line.set(float("nan"))
                return
            self.line.set(self.func(window))


class SumN(OperationN):
    func = sum


class Average(OperationN):
    """简单移动平均的底层实现"""

    def next(self) -> None:
        if self.data is not None:
            window = self.data.get(size=self.p.period)
            if len(window) < self.p.period:
                self.line.set(float("nan"))
                return
            self.line.set(sum(window) / len(window))


class Max(PeriodN):
    lines = ("line",)

    def next(self) -> None:
        if self.data is not None:
            window = self.data.get(size=self.p.period)
            if len(window) < self.p.period:
                self.line.set(float("nan"))
                return
            self.line.set(max(window))


class Min(PeriodN):
    lines = ("line",)

    def next(self) -> None:
        if self.data is not None:
            window = self.data.get(size=self.p.period)
            if len(window) < self.p.period:
                self.line.set(float("nan"))
                return
            self.line.set(min(window))


class StdDev(PeriodN):
    """滚动标准差"""
    lines = ("line",)
    params = {"period": 20}

    def next(self) -> None:
        if self.data is not None:
            window = self.data.get(size=self.p.period)
            if len(window) < self.p.period:
                self.line.set(float("nan"))
                return
            mean = sum(window) / len(window)
            var = sum((x - mean) ** 2 for x in window) / len(window)
            self.line.set(var ** 0.5)
