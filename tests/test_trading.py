import io
import json

import pytest

from papertrader.cli import App
from papertrader.portfolio import Portfolio, TradeError
from papertrader.prices import PriceError
from papertrader.profiles import Profiles, parse_cash


class FakePrices:
    def __init__(self, prices):
        self.prices = prices

    def get_price(self, symbol):
        symbol = symbol.upper()
        if symbol not in self.prices:
            raise PriceError(f"No price found for {symbol}")
        return self.prices[symbol]

    def get_history(self, symbol, period="1mo"):
        price = self.get_price(symbol)
        return [
            {"time": f"2026-09-{day:02d}T00:00:00", "close": price * (0.9 + day / 200)}
            for day in range(1, 21)
        ]

    def get_prices(self, symbols):
        return {s: self.get_price(s) for s in symbols}


def make_app(tmp_path, **prices):
    out = io.StringIO()
    app = App(Profiles(tmp_path / "profiles"), prices=FakePrices(prices or {"AAPL": 200.0}), out=out)
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
    saved = json.loads((tmp_path / "profiles" / "default.json").read_text())
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


def test_parse_cash():
    assert parse_cash("$50,000") == 50_000
    assert parse_cash("25k") == 25_000
    assert parse_cash("1.5M") == 1_500_000
    assert parse_cash(None) == 100_000
    for bad in ("abc", "0", "-5", "2000b"):
        with pytest.raises(TradeError):
            parse_cash(bad)


def test_profiles_are_separate(tmp_path):
    profiles = Profiles(tmp_path)
    profiles.create("Alex", "25k")
    profiles.create("Sam")
    alex = profiles.load("alex")  # names match without caring about case
    alex.buy("AAPL", 10, 100)
    profiles.save("Alex", alex)
    assert profiles.names() == ["Alex", "Sam"]
    assert profiles.load("Alex").cash == 24_000
    assert profiles.load("Sam").cash == 100_000
    with pytest.raises(TradeError):
        profiles.create("alex")
    with pytest.raises(TradeError):
        profiles.create("../evil")
    profiles.delete("Sam")
    assert profiles.names() == ["Alex"]


def test_old_portfolio_file_becomes_default_profile(tmp_path):
    legacy = tmp_path / "portfolio.json"
    Portfolio(cash=1234, starting_cash=5000).save(legacy)
    profiles = Profiles(tmp_path / "profiles", legacy_file=legacy)
    assert profiles.ensure() == "default"
    assert profiles.load("default").cash == 1234


def test_cli_profile_commands(tmp_path):
    out = io.StringIO()
    app = App(Profiles(tmp_path), profile="Main", cash="10k", prices=FakePrices({"AAPL": 100.0}), out=out)
    assert app.portfolio.cash == 10_000
    app.run(["buy", "AAPL", "5"])
    app.run(["new", "Risky", "1m"])
    assert app.profile == "Risky" and app.portfolio.cash == 1_000_000
    app.run(["delete", "Risky"])
    assert "can't delete the profile you're using" in out.getvalue()
    app.run(["switch", "main"])
    assert app.profile == "Main" and app.portfolio.cash == 9_500
    app.run(["delete", "Risky"])
    app.run(["profiles"])
    assert "* Main" in out.getvalue() and "Risky" not in app.profiles.names()
