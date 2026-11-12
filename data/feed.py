"""
数据接入模块 —— Line 统一抽象与 DataFeed 基类

借鉴 Backtrader 的核心设计：
  1. 一切数据皆是 Line，索引 [0] 永远表示「当前时刻」，[-1] 取前一根，[1] 取后一根。
  2. Line 内部用 list + 游标 idx 实现零索引语义，避免在公式中到处传偏移量。
  3. DataFeed 是持有 OHLCV 多条 Line 的数据源，由引擎每根 bar 调用 forward()。
  4. 运算符重载返回 LineOperation（本身也是 Line），从而声明式地组装因子 DAG。
"""
from __future__ import annotations

import math
import operator
from array import array
from typing import Any, Iterable, Optional


def _is_computable(obj: Any) -> bool:
    """判断一个对象是否是「可计算节点」（有 next() 方法，需引擎驱动求值）"""
    return hasattr(obj, "next") and callable(getattr(obj, "next"))


class Line:
    """
    核心数据载体：一条时间序列线。

    索引语义（关键设计）：
        line[0]   -> 当前 bar 的值（游标所在位置）
        line[-1]  -> 上一根 bar 的值
        line[-n]  -> 前 n 根 bar 的值
        line[1]   -> 下一根 bar 的值（仅在数据预加载时可用）

    游标 idx 初始为 -1，每 forward() 一次追加一个槽位并前移。
    """

    def __init__(self) -> None:
        # 底层存储：双精度数组，比 list 省内存、数值访问更快
        self._array: array = array("d")
        # 游标：指向「当前」位置，-1 表示尚无数据
        self._idx: int = -1
        # 最小周期：该 line 至少需要多少根 bar 才能产出有效值
        self._minperiod: int = 1
        # 依赖节点列表（用于引擎拓扑排序；普通数据线为空）
        self._deps: list = []

    # ---------- 长度与游标 ----------
    def __len__(self) -> int:
        """已产出的 bar 数量（不是缓冲长度）"""
        return self._idx + 1

    def buflen(self) -> int:
        """底层缓冲实际长度（>= len，允许 lookahead）"""
        return len(self._array)

    @property
    def minperiod(self) -> int:
        return self._minperiod

    def addminperiod(self, n: int) -> None:
        """累加最小周期（组合指标时由框架自动调用）"""
        self._minperiod = max(self._minperiod, self._minperiod + n - 1)

    def updateminperiod(self, n: int) -> None:
        """取 max 更新最小周期"""
        if n > self._minperiod:
            self._minperiod = n

    # ---------- 索引访问 ----------
    def __getitem__(self, ago: int) -> float:
        """ago=0 当前，ago=-1 前一根，ago=1 后一根。
        历史不足或越界返回 NaN，避免负索引静默回绕到数组尾部。
        """
        idx = self._idx + ago
        if idx < 0 or idx >= len(self._array):
            return float("nan")
        return self._array[idx]

    def __setitem__(self, ago: int, value: float) -> None:
        self._array[self._idx + ago] = float(value)

    def get(self, ago: int = 0, size: int = 1) -> list[float]:
        """取相对当前位置的连续切片；历史不足时返回空列表（由调用方置 NaN）"""
        start = self._idx + ago - size + 1
        end = self._idx + ago + 1
        if start < 0 or end > len(self._array):
            return []
        return list(self._array[start:end])

    # ---------- 游标推进 ----------
    def forward(self, value: float = float("nan")) -> None:
        """追加一个新槽位并前移游标（引擎每根 bar 调用）"""
        self._idx += 1
        self._array.append(float(value))

    def set(self, value: float, ago: int = 0) -> None:
        """在相对当前位置写入值"""
        self._array[self._idx + ago] = float(value)

    def home(self) -> None:
        """游标回到起点（once 向量化模式用）"""
        self._idx = -1

    # ---------- 运算符重载（声明式因子组装的关键）----------
    # 每个运算返回一个 LineOperation，它本身也是 Line，可继续被组合
    def _op(self, other: Any, op) -> "LineOperation":
        return LineOperation(self, other, op)

    def _rop(self, other: Any, op) -> "LineOperation":
        return LineOperation(other, self, op)

    def __add__(self, other): return self._op(other, operator.add)
    def __radd__(self, other): return self._rop(other, operator.add)
    def __sub__(self, other): return self._op(other, operator.sub)
    def __rsub__(self, other): return self._rop(other, operator.sub)
    def __mul__(self, other): return self._op(other, operator.mul)
    def __rmul__(self, other): return self._rop(other, operator.mul)
    def __truediv__(self, other): return self._op(other, operator.truediv)
    def __rtruediv__(self, other): return self._rop(other, operator.truediv)
    def __pow__(self, other): return self._op(other, operator.pow)

    def __lt__(self, other): return self._op(other, operator.lt)
    def __le__(self, other): return self._op(other, operator.le)
    def __gt__(self, other): return self._op(other, operator.gt)
    def __ge__(self, other): return self._op(other, operator.ge)
    def __eq__(self, other): return self._op(other, operator.eq)
    def __ne__(self, other): return self._op(other, operator.ne)

    def __neg__(self): return LineOwnOperation(self, operator.neg)
    def __abs__(self): return LineOwnOperation(self, operator.abs)

    # 延迟绑定：line(-n) 返回取前 n 根的 LineDelay
    def __call__(self, ago: int = 0) -> "LineDelay":
        return LineDelay(self, ago)

    # 让 Line 可作为 hashable key（避免重写 __eq__ 后破坏 hash）
    __hash__ = object.__hash__


class LineDelay(Line):
    """取另一条线的 ago 根之前的值（实现 self.data(-1) 语义）"""

    def __init__(self, src: Line, ago: int) -> None:
        super().__init__()
        self._src = src
        self._ago = ago
        self.updateminperiod(src._minperiod + abs(ago))
        # 依赖：若 src 是可计算节点（有 next 方法），则依赖它
        self._deps = [src] if _is_computable(src) else []

    def next(self) -> None:
        self.set(self._src[self._ago])

    @property
    def output_lines(self) -> list:
        return [self]


class LineOperation(Line):
    """两条线（或线与标量）的二元运算结果，本身也是 Line"""

    def __init__(self, a, b, op) -> None:
        super().__init__()
        self._a = a
        self._b = b
        self._operation = op   # 注意：不能命名为 _op，否则会覆盖 Line._op 方法
        # 自动继承输入线/指标中较大的 minperiod
        ma = getattr(a, "_minperiod", 1)
        mb = getattr(b, "_minperiod", 1)
        self.updateminperiod(max(ma, mb))
        # 依赖：可计算的操作数
        self._deps = [x for x in (a, b) if _is_computable(x)]

    @property
    def output_lines(self) -> list:
        return [self]

    @staticmethod
    def _val(x):
        """从操作数取当前值：Line/Indicator 取 [0]，标量直接返回"""
        if isinstance(x, Line):
            return x[0]
        if hasattr(x, "__getitem__") and not isinstance(x, (str, list, tuple, dict)):
            return x[0]
        return x

    def next(self) -> None:
        self.set(self._operation(self._val(self._a), self._val(self._b)))


class LineOwnOperation(Line):
    """单目运算（abs / neg）"""

    def __init__(self, a, op) -> None:
        super().__init__()
        self._a = a
        self._operation = op   # 不能命名为 _op
        self.updateminperiod(a._minperiod)
        self._deps = [a] if _is_computable(a) else []

    @property
    def output_lines(self) -> list:
        return [self]

    def next(self) -> None:
        self.set(self._operation(LineOperation._val(self._a)))


class Lines:
    """
    多条 Line 的容器，支持按名称访问：
        self.lines.close    -> 对应 Line
        self.lines[0]       -> 第一条 Line
    """

    def __init__(self, names: Iterable[str]) -> None:
        object.__setattr__(self, "_names", list(names))
        object.__setattr__(self, "_lines", [Line() for _ in self._names])

    def __getattr__(self, name: str) -> Line:
        # 注意：__getattr__ 仅在常规属性找不到时调用
        if name.startswith("_"):
            raise AttributeError(name)
        return self._lines[self._names.index(name)]

    def __setattr__(self, name: str, value) -> None:
        # 允许 self.lines.sma = SomeIndicator(...) 替换底层 line
        if name in ("_names", "_lines"):
            object.__setattr__(self, name, value)
        elif name in self._names:
            self._lines[self._names.index(name)] = value
        else:
            object.__setattr__(self, name, value)

    def __getitem__(self, idx: int) -> Line:
        return self._lines[idx]

    def __iter__(self):
        return iter(self._lines)

    def __len__(self) -> int:
        return len(self._lines)

    @property
    def names(self) -> list[str]:
        return list(self._names)


class DataFeed:
    """
    数据源基类：持有一组 OHLCV Line。

    子类需实现 _load() 逐 bar 产出数据，或在 __init__ 中加载完整历史后
    由引擎逐根 forward。本骨架采用「预加载 + 引擎逐根推进」模式。
    """

    # 子类声明此数据源包含哪些 line
    lines = ("datetime", "open", "high", "low", "close", "volume")

    def __init__(self) -> None:
        self.lines = Lines(self.__class__.lines)
        self._bars: list[dict] = []   # 预加载的全部 bar
        self._cursor: int = 0          # 引擎推进用游标
        self._minperiod: int = 1

    def load_bars(self, bars: list[dict]) -> None:
        """注入预加载 bar 列表（每个 bar 为 dict，含 datetime/open/high/low/close/volume）"""
        self._bars = bars

    def __len__(self) -> int:
        return len(self._bars)

    @staticmethod
    def _to_float(value) -> float:
        """把 bar 字段值转成 float；datetime 字符串转成日期 ordinal"""
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            # 尝试按日期解析为 ordinal；失败则返回 nan
            try:
                from datetime import datetime
                return float(datetime.strptime(value[:10], "%Y-%m-%d").toordinal())
            except ValueError:
                return float("nan")
        return float("nan")

    def next(self) -> bool:
        """
        由引擎调用，推进一根 bar 到自身 lines。
        返回 True 表示成功产出一根，False 表示数据耗尽。
        """
        if self._cursor >= len(self._bars):
            return False
        bar = self._bars[self._cursor]
        for i, name in enumerate(self.lines.names):
            self.lines[i].forward(self._to_float(bar.get(name)))
        self._cursor += 1
        return True

    @property
    def current_datetime(self) -> str:
        """返回当前 bar 的原始日期字符串（用于输出）"""
        if 0 < self._cursor <= len(self._bars):
            return str(self._bars[self._cursor - 1].get("datetime", ""))
        return ""

    # 便捷属性：self.data.close 等价于 self.lines.close
    @property
    def open(self) -> Line: return self.lines.open
    @property
    def high(self) -> Line: return self.lines.high
    @property
    def low(self) -> Line: return self.lines.low
    @property
    def close(self) -> Line: return self.lines.close
    @property
    def volume(self) -> Line: return self.lines.volume

    def close_line(self) -> Line:
        """返回第一条价格线（默认 close），供指标作为默认输入"""
        return self.lines.close
