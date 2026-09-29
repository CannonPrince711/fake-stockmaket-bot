import json

import pytest

from papertrader import catalog
from papertrader.prices import PriceError, SourceDown, _yahoo_error
from papertrader.sources import (
    CoinGeckoPrices, FallbackPrices, FinnhubPrices, StooqPrices, build_sources,
)


class FakeWeb:
    """Answers URLs containing a key with canned text; anything else raises the given error."""

    def __init__(self, routes, missing=PriceError("Not found")):
        self.routes, self.missing, self.urls = routes, missing, []

    def __call__(self, url, headers):
        self.urls.append(url)
        for part, body in self.routes.items():
            if part in url:
                if isinstance(body, Exception):
                    raise body
                return body if isinstance(body, str) else json.dumps(body)
        raise self.missing


def test_coingecko_price_history_and_search():
    web = FakeWeb({
        "simple/price?ids=bitcoin": {"bitcoin": {"usd": 64000.5}},
        "search?query=PEPE": {"coins": [
            {"id": "pepe-knockoff", "symbol": "pepe", "market_cap_rank": 900},
            {"id": "pepe", "symbol": "pepe", "market_cap_rank": 30},
        ]},
        "simple/price?ids=pepe": {"pepe": {"usd": 0.0000123}},
        "coins/bitcoin/market_chart": {"prices": [[1_700_000_000_000, 60000], [1_700_086_400_000, 61000]]},
    })
    cg = CoinGeckoPrices(api_key="demo", fetch=web)
    assert cg.supports("BTC-USD") and not cg.supports("AAPL")
    assert cg.get_price("BTC-USD") == 64000.5
    assert cg.get_price("PEPE-USD") == 0.0000123  # picked the bigger coin
    history = cg.get_history("BTC-USD", "1mo")
    assert [p["close"] for p in history] == [60000.0, 61000.0]
    assert "days=30" in web.urls[-1]


def test_finnhub_quote_and_unknown_ticker():
    web = FakeWeb({"symbol=AAPL": {"c": 227.5}, "symbol=NOPE": {"c": 0}})
    fh = FinnhubPrices("key", fetch=web)
    assert fh.get_price("aapl") == 227.5 and "token=key" in web.urls[0]
    with pytest.raises(PriceError):
        fh.get_price("NOPE")
    with pytest.raises(SourceDown):
        FinnhubPrices("bad", fetch=FakeWeb({"quote": {"error": "Invalid API key"}})).get_price("AAPL")


def test_stooq_quote_and_history():
    web = FakeWeb({
        "d/l/?s=aapl.us": "Date,Open,High,Low,Close,Volume\n2026-09-25,1,1,1,220.1,5\n2026-09-26,1,1,1,225.3,5\n",
        "q/l/?s=aapl.us": "Symbol,Date,Time,Open,High,Low,Close,Volume\nAAPL.US,2026-09-28,22:00:00,1,2,0.5,227.52,100\n",
        "q/l/?s=nope.us": "Symbol,Date,Time,Open,High,Low,Close,Volume\nNOPE.US,N/D,N/D,N/D,N/D,N/D,N/D,N/D\n",
    })
    st = StooqPrices(fetch=web)
    assert st.get_price("AAPL") == 227.52
    with pytest.raises(PriceError):
        st.get_price("NOPE")
    assert [p["close"] for p in st.get_history("AAPL", "5d")] == [220.1, 225.3]
    assert not st.supports("SHOP.TO") and not st.supports("BTC-USD")


class Source:
    def __init__(self, name, prices=None, error=None, crypto_only=False):
        self.name, self.prices, self.error, self.crypto_only, self.calls = name, prices or {}, error, crypto_only, 0

    def supports(self, symbol):
        return symbol.endswith("-USD") or not self.crypto_only

    def get_price(self, symbol):
        self.calls += 1
        if self.error:
            raise self.error
        if symbol not in self.prices:
            raise PriceError(f"{self.name} has no {symbol}")
        return self.prices[symbol]

    def get_history(self, symbol, period):
        return [{"time": "t", "close": self.get_price(symbol)}]


def test_fallback_uses_next_source_and_cools_down_broken_one():
    now = [0.0]
    yahoo = Source("Yahoo", error=SourceDown("429 Too Many Requests"))
    gecko = Source("CoinGecko", {"BTC-USD": 64000.0}, crypto_only=True)
    stooq = Source("Stooq", {"AAPL": 227.0})
    fb = FallbackPrices([yahoo, gecko, stooq], cooldown=30, clock=lambda: now[0])

    assert fb.get_price("AAPL") == 227.0 and fb.last_source["AAPL"] == "Stooq"
    assert fb.get_price("BTC-USD") == 64000.0 and fb.last_source["BTC-USD"] == "CoinGecko"
    assert yahoo.calls == 1  # skipped while cooling down
    now[0] = 31
    yahoo.error, yahoo.prices = None, {"AAPL": 228.0}
    assert fb.get_price("AAPL") == 228.0 and fb.last_source["AAPL"] == "Yahoo"


def test_fallback_reports_not_found_over_outage():
    fb = FallbackPrices([Source("Yahoo", error=SourceDown("down")), Source("Stooq", {})])
    with pytest.raises(PriceError) as err:
        fb.get_price("ZZZZ")
    assert not isinstance(err.value, SourceDown) and "Stooq" in str(err.value)


def test_yahoo_errors_are_classified():
    assert isinstance(_yahoo_error("x", Exception("429 Client Error: Too Many Requests")), SourceDown)
    assert not isinstance(_yahoo_error("x", KeyError("currentTradingPeriod")), SourceDown)


def test_build_sources_from_settings():
    assert build_sources({}).names == ["Yahoo Finance", "CoinGecko", "Stooq"]
    assert build_sources({"FINNHUB_API_KEY": "k"}).names == ["Yahoo Finance", "Finnhub", "CoinGecko", "Stooq"]
    assert build_sources({"PAPERTRADER_PRICE_SOURCES": "stooq, coingecko"}).names == ["Stooq", "CoinGecko"]
    with pytest.raises(ValueError):
        build_sources({"PAPERTRADER_PRICE_SOURCES": "bloomberg"})


def test_every_catalog_coin_has_a_coingecko_id():
    missing = [s for s in catalog.CRYPTO if s.removesuffix("-USD") not in CoinGeckoPrices.IDS]
    assert not missing


def test_choose_preferred_source_keeps_backups(tmp_path):
    from papertrader.sources import load_preference, save_preference

    yahoo = Source("Yahoo Finance", {"AAPL": 1.0, "BTC-USD": 9.0}); yahoo.key = "yahoo"
    stooq = Source("Stooq", {"AAPL": 2.0}); stooq.key = "stooq"
    fb = FallbackPrices([yahoo, stooq])
    fb.set_preferred("stooq")
    assert fb.get_price("AAPL") == 2.0
    assert fb.get_price("BTC-USD") == 9.0  # Stooq has no crypto, so Yahoo still answers
    with pytest.raises(PriceError):
        fb.set_preferred("finnhub")  # not turned on
    with pytest.raises(PriceError):
        fb.set_preferred("bloomberg")
    fb.set_preferred("auto")
    assert fb.get_price("AAPL") == 1.0
    assert [s["id"] for s in fb.describe()["sources"] if s["enabled"]] == ["yahoo", "stooq"]

    save_preference(tmp_path, "stooq")
    assert load_preference(tmp_path) == "stooq"
    save_preference(tmp_path, "auto")
    assert load_preference(tmp_path) is None


def test_switch_source_over_web_and_cli(tmp_path):
    import io
    import threading
    import urllib.request

    from papertrader.cli import App
    from papertrader.profiles import Profiles
    from papertrader.web import make_server

    def sources():
        y = Source("Yahoo Finance", {"AAPL": 1.0}); y.key = "yahoo"
        s = Source("Stooq", {"AAPL": 2.0}); s.key = "stooq"
        return FallbackPrices([y, s])

    srv = make_server(Profiles(tmp_path), port=0, prices=sources(), price_ttl=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def post(path, body):
        req = urllib.request.Request(base + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as res:
            return json.loads(res.read())

    try:
        assert post("/api/sources", {"source": "stooq"})["preferred"] == "stooq"
        with urllib.request.urlopen(base + "/api/quote?symbol=AAPL") as res:
            quote = json.loads(res.read())
        assert quote["price"] == 2.0 and quote["source"] == "Stooq"
    finally:
        srv.shutdown()
        srv.server_close()

    # The choice is saved, so the terminal app starts with it too.
    out = io.StringIO()
    app = App(Profiles(tmp_path), prices=sources(), out=out)
    assert app.prices.preferred == "stooq"
    app.run(["source", "auto"])
    assert app.prices.preferred is None and "* auto" in out.getvalue()
