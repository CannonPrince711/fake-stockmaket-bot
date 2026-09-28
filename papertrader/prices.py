"""Real-world stock prices via Yahoo Finance (the free yfinance library, no API key)."""

from __future__ import annotations

import logging

# yfinance logs its own retries; our error message is enough.
logging.getLogger("yfinance").setLevel(logging.CRITICAL)


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

    def get_prices(self, symbols) -> dict[str, float]:
        return {s: self.get_price(s) for s in symbols}
