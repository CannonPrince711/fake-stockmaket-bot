import io
import json

import pytest

from papertrader.cli import App
from papertrader.portfolio import Portfolio, TradeError
from papertrader.prices import PriceError


class FakePrices:
    def __init__(self, prices):
        self.prices = prices

    def get_price(self, symbol):
        symbol = symbol.upper()
        if symbol not in self.prices:
            raise PriceError(f"No price found for {symbol}")
        return self.prices[symbol]

    def get_prices(self, symbols):
        return {s: self.get_price(s) for s in symbols}


def make_app(tmp_path, **prices):
    out = io.StringIO()
    app = App(tmp_path / "p.json", prices=FakePrices(prices or {"AAPL": 200.0}), out=out)
    return app, out


def test_buy_and_sell_updates_cash_and_positions():
    p = Portfolio()
    p.buy("aapl", 10, 200)
    assert p.cash == 98_000
    assert p.positions["AAPL"].shares == 10
    p.sell("AAPL", 4, 250)
    assert p.cash == 99_000
    assert p.positions["AAPL"].shares == 6


def test_average_cost_across_buys():
    p = Portfolio()
    p.buy("MSFT", 10, 100)
    p.buy("MSFT", 10, 200)
    assert p.positions["MSFT"].avg_cost == 150


def test_selling_everything_removes_position():
    p = Portfolio()
    p.buy("TSLA", 3, 100)
    p.sell("TSLA", 3, 100)
    assert "TSLA" not in p.positions


def test_cannot_overspend_or_oversell():
    p = Portfolio(cash=1000, starting_cash=1000)
    with pytest.raises(TradeError):
        p.buy("AAPL", 10, 200)
    with pytest.raises(TradeError):
        p.sell("AAPL", 1, 200)
    with pytest.raises(TradeError):
        p.buy("AAPL", 0, 200)


def test_save_and_load_round_trip(tmp_path):
    path = tmp_path / "p.json"
    p = Portfolio()
    p.buy("AAPL", 2, 150)
    p.save(path)
    loaded = Portfolio.load(path)
    assert loaded.cash == p.cash
    assert loaded.positions["AAPL"].shares == 2
    assert len(loaded.history) == 1


def test_cli_buy_persists_and_portfolio_shows_profit(tmp_path):
    app, out = make_app(tmp_path, AAPL=200.0)
    app.run(["buy", "AAPL", "10"])
    saved = json.loads((tmp_path / "p.json").read_text())
    assert saved["cash"] == 98_000

    app.prices.prices["AAPL"] = 210.0
    app.run(["portfolio"])
    text = out.getvalue()
    assert "+100.00" in text
    assert "Total value: $100,100.00" in text


def test_cli_sell_all_and_errors(tmp_path):
    app, out = make_app(tmp_path, AAPL=200.0)
    app.run(["buy", "AAPL", "5"])
    app.run(["sell", "AAPL", "all"])
    assert app.portfolio.cash == 100_000
    app.run(["sell", "AAPL", "1"])
    app.run(["quote", "NOPE"])
    app.run(["buy", "AAPL", "lots"])
    text = out.getvalue()
    assert "only own 0" in text
    assert "No price found for NOPE" in text
    assert "isn't a number" in text


def test_cli_quit_and_reset(tmp_path):
    app, _ = make_app(tmp_path)
    assert app.run(["quit"]) is False
    app.run(["reset", "5000"])
    assert app.portfolio.cash == 5000
