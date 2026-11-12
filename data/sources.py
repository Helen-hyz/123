"""
数据源实现：akshare（A 股）与 yfinance（美股）。

两者都被统一为「预加载全部 bar -> DataFeed.load_bars -> 引擎逐根推进」。
这样策略层完全不感知底层数据源差异。
"""
from __future__ import annotations

from .feed import DataFeed


class AkshareFeed(DataFeed):
    """akshare A 股日线数据源

    优先使用新浪源 ``stock_zh_a_daily``（更稳定，不易被东方财富 push2 限流）；
    symbol 格式：带交易所前缀，如 ``sh600519`` / ``sz000001``。
    """

    def __init__(self, symbol: str, start_date: str, end_date: str,
                 adjust: str = "qfq") -> None:
        super().__init__()
        import akshare as ak
        # 新浪源：date, open, high, low, close, volume, amount, ...
        df = ak.stock_zh_a_daily(symbol=symbol, start_date=start_date,
                                 end_date=end_date, adjust=adjust)
        df = df.rename(columns={"date": "datetime"})
        if df.empty:
            raise ValueError(f"akshare 返回空数据：symbol={symbol} "
                             f"{start_date}~{end_date}")
        bars = df[["datetime", "open", "high", "low", "close", "volume"]].to_dict("records")
        for b in bars:
            b["datetime"] = str(b["datetime"])
        self.load_bars(bars)


class AkshareIndexFeed(DataFeed):
    """akshare 指数日线数据源（如沪深300 ``sh000300``）

    指数不可复权，仅取 OHLCV。用于回测的市场基准。
    """

    def __init__(self, symbol: str = "sh000300",
                 start_date: str = "20200101",
                 end_date: str = "20231231") -> None:
        super().__init__()
        import akshare as ak
        df = ak.stock_zh_index_daily(symbol=symbol)
        df = df.rename(columns={"date": "datetime"})
        df["datetime"] = df["datetime"].astype(str)
        lo, hi = _iso_date(start_date), _iso_date(end_date)
        df = df[(df["datetime"] >= lo) & (df["datetime"] <= hi)]
        if df.empty:
            raise ValueError(f"akshare 指数返回空数据：symbol={symbol} "
                             f"{start_date}~{end_date}")
        cols = ["datetime", "open", "high", "low", "close"]
        if "volume" in df.columns:
            cols.append("volume")
        bars = df[cols].to_dict("records")
        for b in bars:
            if "volume" not in b:
                b["volume"] = 0.0
        self.load_bars(bars)


def _iso_date(s: str) -> str:
    """20200101 -> 2020-01-01；已是 ISO 格式则原样返回"""
    s = str(s)
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s


class YahooFeed(DataFeed):
    """yfinance 美股/全球数据源"""

    def __init__(self, ticker: str, start: str, end: str) -> None:
        super().__init__()
        import yfinance as yf
        df = yf.download(ticker, start=start, end=end, auto_adjust=False,
                         progress=False)
        df = df.reset_index()
        # 多级列名处理（yfinance 新版返回 MultiIndex）
        if isinstance(df.columns, __import__("pandas").MultiIndex):
            df.columns = [c[0] for c in df.columns]
        df = df.rename(columns={
            "Date": "datetime", "Open": "open", "High": "high",
            "Low": "low", "Close": "close", "Volume": "volume",
        })
        bars = df[["datetime", "open", "high", "low", "close", "volume"]].to_dict("records")
        if not bars:
            raise ValueError(f"yfinance 返回空数据：{ticker} {start}~{end}"
                             f"（可能被限流，稍后重试）")
        for b in bars:
            b["datetime"] = str(b["datetime"])
        self.load_bars(bars)


class BaostockFeed(DataFeed):
    """baostock A 股日线数据源（免费、无需 token，稳定性较好）

    股票代码格式：sh.600519 / sz.000001
    """

    def __init__(self, code: str, start_date: str, end_date: str,
                 adjustflag: str = "2") -> None:
        """
        :param adjustflag: 复权类型 "1"=后复权 "2"=前复权 "3"=不复权
        """
        super().__init__()
        import baostock as bs
        lg = bs.login()
        try:
            rs = bs.query_history_k_data_plus(
                code,
                "date,open,high,low,close,volume",
                start_date=start_date, end_date=end_date,
                frequency="d", adjustflag=adjustflag,
            )
            bars = []
            while (rs.error_code == "0") and rs.next():
                row = rs.get_row_data()
                bars.append({
                    "datetime": row[0],
                    "open": float(row[1]),
                    "high": float(row[2]),
                    "low": float(row[3]),
                    "close": float(row[4]),
                    "volume": float(row[5]),
                })
            if not bars:
                raise ValueError(
                    f"baostock 返回空数据：{code} {start_date}~{end_date}")
            self.load_bars(bars)
        finally:
            bs.logout()


class CsvFeed(DataFeed):
    """本地 CSV 数据源，列需含 datetime/open/high/low/close/volume"""

    def __init__(self, path: str) -> None:
        super().__init__()
        import pandas as pd
        df = pd.read_csv(path)
        bars = df.to_dict("records")
        self.load_bars(bars)


class RandomWalkFeed(DataFeed):
    """合成随机游走数据，用于无网络时的演示/测试"""

    def __init__(self, n_bars: int = 500, start_price: float = 100.0,
                 seed: int = 42) -> None:
        super().__init__()
        import random
        from datetime import date, timedelta
        random.seed(seed)
        price = start_price
        bars = []
        d = date(2020, 1, 1)
        for _ in range(n_bars):
            ret = random.gauss(0.0002, 0.02)
            open_p = price
            close_p = price * (1 + ret)
            high_p = max(open_p, close_p) * (1 + abs(random.gauss(0, 0.005)))
            low_p = min(open_p, close_p) * (1 - abs(random.gauss(0, 0.005)))
            vol = int(random.uniform(1e6, 1e7))
            bars.append({
                "datetime": str(d),
                "open": round(open_p, 2),
                "high": round(high_p, 2),
                "low": round(low_p, 2),
                "close": round(close_p, 2),
                "volume": vol,
            })
            price = close_p
            d += timedelta(days=1)
        self.load_bars(bars)
