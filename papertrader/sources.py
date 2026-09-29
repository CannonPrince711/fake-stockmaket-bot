"""Backup price services, and the logic that tries them in order when one is down.

- CoinGecko: crypto prices and charts. No key needed (an optional free demo key raises the limits).
- Finnhub: real-time US stock quotes. Needs a free API key (FINNHUB_API_KEY). No charts on the free plan.
- Stooq: delayed US stock quotes and daily charts. No key needed.
"""

from __future__ import annotations

import csv
import io
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .prices import HISTORY_INTERVALS, PriceError, SourceDown, YahooPrices

TIMEOUT = 6  # seconds per request
USER_AGENT = "papertrader/1.0 (+https://github.com/CannonPrince711/fake-stockmaket-bot)"


def http_get(url: str, headers: dict | None = None) -> str:
    """GET a URL and return the body. Outages and rate limits raise SourceDown."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
            return res.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise PriceError(f"Not found ({url.split('?')[0]})") from exc
        raise SourceDown(f"HTTP {exc.code} from {urllib.parse.urlparse(url).netloc}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SourceDown(f"Couldn't reach {urllib.parse.urlparse(url).netloc}: {exc}") from exc


def is_crypto(symbol: str) -> bool:
    return symbol.upper().endswith("-USD")


# Days of data to ask for, per chart range.
PERIOD_DAYS = {"5d": 5, "1mo": 30, "6mo": 182, "1y": 365}


class CoinGeckoPrices:
    key = "coingecko"
    name = "CoinGecko"
    BASE = "https://api.coingecko.com/api/v3"

    # CoinGecko names coins by id, not ticker. These cover the catalog; others are looked up.
    IDS = {
        "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "XRP": "ripple", "BNB": "binancecoin",
        "DOGE": "dogecoin", "ADA": "cardano", "TRX": "tron", "AVAX": "avalanche-2", "LINK": "chainlink",
        "SHIB": "shiba-inu", "DOT": "polkadot", "BCH": "bitcoin-cash", "LTC": "litecoin", "XLM": "stellar",
        "HBAR": "hedera-hashgraph", "ATOM": "cosmos", "ETC": "ethereum-classic", "XMR": "monero",
    }

    def __init__(self, api_key: str | None = None, fetch=http_get):
        self.fetch = fetch
        self.headers = {"x-cg-demo-api-key": api_key} if api_key else {}
        self.ids = dict(self.IDS)

    def supports(self, symbol: str) -> bool:
        return is_crypto(symbol)

    def _get(self, path: str, **params) -> dict | list:
        return json.loads(self.fetch(f"{self.BASE}{path}?{urllib.parse.urlencode(params)}", self.headers))

    def coin_id(self, symbol: str) -> str:
        code = symbol.upper().removesuffix("-USD")
        if code not in self.ids:
            # Several coins can share a ticker; pick the biggest one.
            coins = [c for c in self._get("/search", query=code).get("coins", [])
                     if str(c.get("symbol", "")).upper() == code]
            if not coins:
                raise PriceError(f"CoinGecko doesn't list {symbol}")
            coins.sort(key=lambda c: c.get("market_cap_rank") or 10**9)
            self.ids[code] = coins[0]["id"]
        return self.ids[code]

    def get_price(self, symbol: str) -> float:
        coin = self.coin_id(symbol)
        price = self._get("/simple/price", ids=coin, vs_currencies="usd").get(coin, {}).get("usd")
        if not price:
            raise PriceError(f"CoinGecko has no price for {symbol}")
        return float(price)

    def get_history(self, symbol: str, period: str = "1mo") -> list[dict]:
        days = PERIOD_DAYS.get(period)
        if days is None:
            raise PriceError(f"Unknown chart range '{period}'")
        data = self._get(f"/coins/{self.coin_id(symbol)}/market_chart", vs_currency="usd", days=days)
        points = [{"time": datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat(), "close": float(p)}
                  for ms, p in data.get("prices", []) if p]
        if not points:
            raise PriceError(f"CoinGecko has no history for {symbol}")
        return points


class FinnhubPrices:
    key = "finnhub"
    name = "Finnhub"
    BASE = "https://finnhub.io/api/v1"

    def __init__(self, api_key: str, fetch=http_get):
        self.api_key = api_key
        self.fetch = fetch

    def supports(self, symbol: str) -> bool:
        return not is_crypto(symbol)

    def get_price(self, symbol: str) -> float:
        url = f"{self.BASE}/quote?" + urllib.parse.urlencode({"symbol": symbol.upper(), "token": self.api_key})
        data = json.loads(self.fetch(url, {}))
        if isinstance(data, dict) and data.get("error"):
            raise SourceDown(f"Finnhub: {data['error']}")
        price = (data or {}).get("c")
        if not price:  # Finnhub answers 0 for tickers it doesn't know
            raise PriceError(f"Finnhub has no price for {symbol}")
        return float(price)

    def get_history(self, symbol: str, period: str = "1mo") -> list[dict]:
        raise PriceError("Finnhub's free plan doesn't include charts")


class StooqPrices:
    key = "stooq"
    name = "Stooq"
    BASE = "https://stooq.com/q"

    def __init__(self, fetch=http_get):
        self.fetch = fetch

    def supports(self, symbol: str) -> bool:
        # Stooq covers US listings; tickers with an exchange suffix (SHOP.TO) are left to others.
        return not is_crypto(symbol) and "." not in symbol

    @staticmethod
    def code(symbol: str) -> str:
        return f"{symbol.lower()}.us"

    def _rows(self, url: str) -> list[dict]:
        text = self.fetch(url, {})
        if "limit" in text.lower() and "," not in text:
            raise SourceDown(f"Stooq: {text.strip()[:80]}")
        return list(csv.DictReader(io.StringIO(text)))

    def get_price(self, symbol: str) -> float:
        rows = self._rows(f"{self.BASE}/l/?s={self.code(symbol)}&f=sd2t2ohlcv&h&e=csv")
        close = rows[0].get("Close") if rows else None
        try:
            return float(close)
        except (TypeError, ValueError):  # "N/D" for unknown tickers
            raise PriceError(f"Stooq has no price for {symbol}") from None

    def get_history(self, symbol: str, period: str = "1mo") -> list[dict]:
        days = PERIOD_DAYS.get(period)
        if days is None:
            raise PriceError(f"Unknown chart range '{period}'")
        start = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y%m%d")
        rows = self._rows(f"{self.BASE}/d/l/?s={self.code(symbol)}&i=d&d1={start}")
        points = []
        for row in rows:
            try:
                points.append({"time": f"{row['Date']}T00:00:00", "close": float(row["Close"])})
            except (KeyError, TypeError, ValueError):
                continue
        if not points:
            raise PriceError(f"Stooq has no history for {symbol}")
        return points


class FallbackPrices:
    """Tries each source in order until one answers.

    A source that is down (not merely missing a ticker) is skipped for `cooldown` seconds, so a
    Yahoo outage doesn't slow every refresh while we wait for it to time out.
    """

    def __init__(self, sources, cooldown: float = 30.0, clock=time.monotonic):
        self.sources = list(sources)
        self.cooldown = cooldown
        self.clock = clock
        self.down_until: dict[str, float] = {}
        self.last_source: dict[str, str] = {}
        self.lock = threading.Lock()
        self.preferred: str | None = None  # a source key to try first; None means the normal order

    def set_preferred(self, key: str | None) -> None:
        key = (key or "").strip().lower() or None
        if key in (None, "auto"):
            self.preferred = None
            return
        if key not in SOURCE_INFO:
            raise PriceError(f"Unknown price source '{key}'. Choose from: auto, {', '.join(SOURCE_INFO)}")
        if key not in [getattr(s, "key", None) for s in self.sources]:
            hint = " Set FINNHUB_API_KEY to turn it on." if key == "finnhub" else ""
            raise PriceError(f"{SOURCE_INFO[key][0]} isn't turned on.{hint}")
        self.preferred = key
        with self.lock:
            self.down_until.pop(SOURCE_INFO[key][0], None)  # give it a fresh chance

    def describe(self) -> dict:
        enabled = {getattr(s, "key", None) for s in self.sources}
        return {
            "preferred": self.preferred or "auto",
            "sources": [{"id": key, "name": name, "covers": covers, "enabled": key in enabled}
                        for key, (name, covers) in SOURCE_INFO.items()],
        }

    @property
    def names(self) -> list[str]:
        return [s.name for s in self.sources]

    def _candidates(self, symbol: str):
        usable = [s for s in self.sources if s.supports(symbol)]
        # The chosen source goes first; the rest stay as backups (e.g. for crypto when Stooq is chosen).
        usable.sort(key=lambda s: getattr(s, "key", None) != self.preferred)
        now = self.clock()
        up = [s for s in usable if self.down_until.get(s.name, 0) <= now]
        return up or usable  # if everything is cooling down, try anyway

    def _try(self, symbol: str, call):
        errors = []
        for source in self._candidates(symbol):
            try:
                result = call(source)
            except SourceDown as exc:
                with self.lock:
                    self.down_until[source.name] = self.clock() + self.cooldown
                errors.append(exc)
            except PriceError as exc:
                errors.append(exc)
            else:
                with self.lock:
                    self.down_until.pop(source.name, None)
                return source, result
        if not errors:
            raise PriceError(f"No price source covers {symbol}")
        # Prefer a "not found" message over an outage when some source did answer.
        not_found = [e for e in errors if not isinstance(e, SourceDown)]
        raise (not_found or errors)[0]

    def get_price(self, symbol: str) -> float:
        symbol = symbol.strip().upper()
        source, price = self._try(symbol, lambda s: s.get_price(symbol))
        with self.lock:
            self.last_source[symbol] = source.name
        return price

    def get_history(self, symbol: str, period: str = "1mo") -> list[dict]:
        if period not in HISTORY_INTERVALS:
            raise PriceError(f"Unknown chart range '{period}'")
        symbol = symbol.strip().upper()
        return self._try(symbol, lambda s: s.get_history(symbol, period))[1]

    def get_prices(self, symbols) -> dict[str, float]:
        return {s: self.get_price(s) for s in symbols}


SOURCE_INFO = {
    "yahoo": ("Yahoo Finance", "Stocks, ETFs and crypto, with charts"),
    "finnhub": ("Finnhub", "US stocks, real-time (needs FINNHUB_API_KEY)"),
    "coingecko": ("CoinGecko", "Crypto only, with charts"),
    "stooq": ("Stooq", "US stocks, may be delayed, daily charts"),
}
SOURCE_NAMES = tuple(SOURCE_INFO)
PREFERENCE_FILE = "price-source.setting"  # not .json, so it's never mistaken for a profile


def load_preference(directory) -> str | None:
    try:
        return (Path(directory) / PREFERENCE_FILE).read_text().strip() or None
    except OSError:
        return None


def save_preference(directory, key: str | None) -> None:
    path = Path(directory) / PREFERENCE_FILE
    if key in (None, "auto"):
        path.unlink(missing_ok=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(key)


def build_sources(env=os.environ) -> FallbackPrices:
    """The price sources to use, in order, from PAPERTRADER_PRICE_SOURCES and API key settings."""
    order = [s.strip().lower() for s in env.get("PAPERTRADER_PRICE_SOURCES", ",".join(SOURCE_NAMES)).split(",") if s.strip()]
    unknown = [s for s in order if s not in SOURCE_NAMES]
    if unknown:
        raise ValueError(f"Unknown price source(s) {', '.join(unknown)}. Choose from: {', '.join(SOURCE_NAMES)}")
    sources = []
    for name in order:
        if name == "yahoo":
            sources.append(YahooPrices())
        elif name == "finnhub" and env.get("FINNHUB_API_KEY"):
            sources.append(FinnhubPrices(env["FINNHUB_API_KEY"]))
        elif name == "coingecko":
            sources.append(CoinGeckoPrices(env.get("COINGECKO_API_KEY") or None))
        elif name == "stooq":
            sources.append(StooqPrices())
    if not sources:
        raise ValueError("No price sources are enabled")
    return FallbackPrices(sources)

