"""Real-world stock prices via Yahoo Finance (the free yfinance library, no API key)."""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

# yfinance logs its own retries; our error message is enough.
logging.getLogger("yfinance").setLevel(logging.CRITICAL)


# Chart ranges the app offers, and the bar size used for each.
HISTORY_INTERVALS = {"5d": "30m", "1mo": "1d", "6mo": "1d", "1y": "1wk"}


class PriceError(Exception):
    """Raised when a price can't be looked up (unknown ticker, no network, etc.)."""


class YahooPrices:
    def get_price(self, symbol: str) -> float:
        import yfinance as yf  # imported lazily so tests don't need it

        symbol = symbol.strip().upper()
        try:
            price = yf.Ticker(symbol).fast_info["last_price"]
        except Exception as exc:  # yfinance raises a variety of errors
            raise PriceError(f"Couldn't get a price for {symbol}: {exc}") from exc
        if price is None or price != price or price <= 0:  # None, NaN or zero
            raise PriceError(f"No price found for {symbol}. Is the ticker right?")
        return float(price)

    def get_history(self, symbol: str, period: str = "1mo") -> list[dict]:
        """Closing prices for a chart, oldest first: [{"time": iso, "close": float}, ...]."""
        import yfinance as yf

        interval = HISTORY_INTERVALS.get(period)
        if interval is None:
            raise PriceError(f"Unknown chart range '{period}'")
        symbol = symbol.strip().upper()
        try:
            frame = yf.Ticker(symbol).history(period=period, interval=interval)
        except Exception as exc:
            raise PriceError(f"Couldn't get price history for {symbol}: {exc}") from exc
        points = [
            {"time": ts.isoformat(), "close": float(close)}
            for ts, close in frame["Close"].items()
            if close == close  # skip NaN
        ] if not frame.empty else []
        if not points:
            raise PriceError(f"No price history found for {symbol}")
        return points

    def get_prices(self, symbols) -> dict[str, float]:
        return {s: self.get_price(s) for s in symbols}


class CachedPrices:
    """Wraps a price source and remembers each price for just under a second.

    The web page asks for prices every second, possibly from several tabs; this keeps
    those requests fast and stops us hammering Yahoo with identical lookups.
    """

    def __init__(self, source, ttl: float = 0.9, clock=time.monotonic):
        self.source = source
        self.ttl = ttl
        self.clock = clock
        self.lock = threading.Lock()
        self.cache: dict[str, tuple[float, float]] = {}  # symbol -> (fetched_at, price)

    def get_price(self, symbol: str) -> float:
        symbol = symbol.strip().upper()
        with self.lock:
            hit = self.cache.get(symbol)
        if hit and self.clock() - hit[0] < self.ttl:
            return hit[1]
        return self.fresh_price(symbol)

    def fresh_price(self, symbol: str) -> float:
        """Always ask the source; used for trades so they fill at the latest price."""
        symbol = symbol.strip().upper()
        price = self.source.get_price(symbol)
        with self.lock:
            self.cache[symbol] = (self.clock(), price)
        return price

    def get_prices(self, symbols) -> dict[str, float | PriceError]:
        """Look up several prices at once, in parallel. Failed lookups come back as the PriceError."""
        symbols = list(symbols)

        def one(symbol):
            try:
                return self.get_price(symbol)
            except PriceError as exc:
                return exc

        if len(symbols) <= 1:
            return {s: one(s) for s in symbols}
        with ThreadPoolExecutor(max_workers=min(8, len(symbols))) as pool:
            return dict(zip(symbols, pool.map(one, symbols)))

    def get_history(self, symbol: str, period: str = "1mo") -> list[dict]:
        return self.source.get_history(symbol, period)
