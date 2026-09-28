import json
import threading
import urllib.error
import urllib.request

import pytest

from papertrader.web import make_server
from tests.test_trading import FakePrices


@pytest.fixture
def server(tmp_path):
    prices = FakePrices({"AAPL": 200.0, "MSFT": 400.0})
    srv = make_server(tmp_path / "p.json", port=0, prices=prices)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    srv.base = f"http://127.0.0.1:{srv.server_address[1]}"
    srv.prices = prices
    yield srv
    srv.shutdown()
    srv.server_close()


def call(srv, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(srv.base + path, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as res:
            return res.status, json.loads(res.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read())


def test_page_is_served(server):
    with urllib.request.urlopen(server.base + "/") as res:
        assert b"Fake Stock Market" in res.read()


def test_buy_quote_and_portfolio(server):
    assert call(server, "/api/quote?symbol=aapl") == (200, {"symbol": "AAPL", "price": 200.0})
    status, trade = call(server, "/api/buy", {"symbol": "aapl", "shares": "10"})
    assert status == 200 and trade["total"] == 2000
    server.prices.prices["AAPL"] = 250.0
    _, p = call(server, "/api/portfolio")
    assert p["cash"] == 98_000
    assert p["total"] == 100_500
    assert p["holdings"][0]["pl"] == 500


def test_sell_all_and_errors(server):
    call(server, "/api/buy", {"symbol": "MSFT", "shares": 2})
    status, trade = call(server, "/api/sell", {"symbol": "MSFT", "shares": "all"})
    assert status == 200 and trade["shares"] == 2
    assert call(server, "/api/sell", {"symbol": "MSFT", "shares": 1})[0] == 400
    status, err = call(server, "/api/buy", {"symbol": "AAPL", "shares": 10_000})
    assert status == 400 and "Not enough cash" in err["error"]
    assert call(server, "/api/quote?symbol=NOPE")[0] == 400


def test_reset(server):
    call(server, "/api/buy", {"symbol": "AAPL", "shares": 1})
    assert call(server, "/api/reset", {"cash": "5000"})[0] == 200
    _, p = call(server, "/api/portfolio")
    assert p["cash"] == 5000 and p["holdings"] == []
