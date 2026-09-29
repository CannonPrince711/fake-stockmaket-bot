"""Command-line interface: run one command, or start an interactive trading shell."""

from __future__ import annotations

import argparse
import os
import shlex
import sys
from pathlib import Path

from . import catalog
from .portfolio import Portfolio, TradeError
from .prices import PriceError
from .sources import build_sources, load_preference, save_preference
from .profiles import DEFAULT_DIR, DEFAULT_PROFILE, LEGACY_FILE, Profiles, parse_cash

HELP = """Commands:
  quote SYMBOL [SYMBOL ...]   show the current real-world price
  buy SYMBOL AMOUNT           buy shares (or coins) at the current price
  sell SYMBOL AMOUNT|all      sell shares (or coins) at the current price
  source [NAME]               show or switch where prices come from (auto, yahoo,
                              finnhub, coingecko, stooq)
  stocks                      list popular stocks and ETFs
  crypto                      list popular cryptocurrencies you can trade
  portfolio                   show cash, holdings and profit/loss
  history                     show every trade you've made
  reset [CASH]                start this profile over (default $100,000)

Profiles (separate portfolios, each with its own starting cash):
  profiles                    list your profiles
  new NAME [CASH]             create a profile and switch to it, e.g. new Alex 25k
  switch NAME                 switch to another profile
  delete NAME                 delete a profile
  help                        show this list

Stocks use their ticker (AAPL, SPY). Crypto uses COIN-USD (BTC-USD, ETH-USD),
and you can buy fractions, e.g. buy BTC-USD 0.05
  quit                        leave"""


class App:
    def __init__(self, profiles: Profiles, profile: str = DEFAULT_PROFILE, cash=None, prices=None, out=sys.stdout):
        self.profiles = profiles
        self.prices = prices or build_sources()
        if hasattr(self.prices, "set_preferred"):
            try:
                self.prices.set_preferred(load_preference(profiles.dir))
            except PriceError:
                pass  # e.g. Finnhub was chosen but its key has since been removed
        self.out = out
        self.profile = profiles.ensure(profile, cash)
        self.portfolio = profiles.load(self.profile)

    @property
    def path(self) -> Path:
        return self.profiles.path(self.profile)

    def say(self, text: str = "") -> None:
        print(text, file=self.out)

    def run(self, args: list[str]) -> bool:
        """Run one command. Returns False when the user wants to quit."""
        if not args:
            return True
        cmd, rest = args[0].lower(), args[1:]
        try:
            if cmd in ("quit", "exit", "q"):
                return False
            handler = getattr(self, f"cmd_{cmd}", None)
            if handler is None:
                self.say(f"Unknown command '{cmd}'. Type 'help' for the list.")
            else:
                handler(rest)
        except (TradeError, PriceError) as exc:
            self.say(f"Error: {exc}")
        return True

    def cmd_help(self, rest):
        self.say(HELP)

    def cmd_quote(self, rest):
        if not rest:
            raise TradeError("Usage: quote SYMBOL [SYMBOL ...]")
        for symbol in rest:
            self.say(f"{symbol.upper():<10} {money(self.prices.get_price(symbol))}")

    def cmd_crypto(self, rest):
        self.say("Popular cryptocurrencies (use these tickers to quote, buy and sell):")
        for symbol, name in catalog.CRYPTO.items():
            self.say(f"  {symbol:<10} {name}")
        self.say("Any coin Yahoo Finance lists as COIN-USD works too.")

    def cmd_source(self, rest):
        if not hasattr(self.prices, "describe"):
            raise TradeError("This price source can't be switched")
        if rest:
            self.prices.set_preferred(rest[0])
            save_preference(self.profiles.dir, self.prices.preferred)
        info = self.prices.describe()
        self.say("Price sources (the chosen one is tried first, the others are backups):")
        mark = "*" if info["preferred"] == "auto" else " "
        self.say(f"{mark} {'auto':<10} Yahoo first, then the others")
        for s in info["sources"]:
            mark = "*" if s["id"] == info["preferred"] else " "
            off = "" if s["enabled"] else "  [off]"
            self.say(f"{mark} {s['id']:<10} {s['name']}: {s['covers']}{off}")
        self.say("Switch with: source NAME   (e.g. source coingecko, or source auto)")

    def cmd_stocks(self, rest):
        self.say("Popular stocks and ETFs (use these tickers to quote, buy and sell):")
        for symbol, name in catalog.STOCKS.items():
            self.say(f"  {symbol:<10} {name}")
        self.say("Any ticker Yahoo Finance lists works too.")

    def cmd_buy(self, rest):
        symbol, shares = self._symbol_and_shares(rest, "buy")
        price = self.prices.get_price(symbol)
        trade = self.portfolio.buy(symbol, float(shares), price)
        self.portfolio.save(self.path)
        self.say(
            f"Bought {trade['shares']:g} {trade['symbol']} @ {money(price)} "
            f"for ${trade['total']:,.2f}. Cash left: ${self.portfolio.cash:,.2f}"
        )

    def cmd_sell(self, rest):
        symbol, shares = self._symbol_and_shares(rest, "sell")
        symbol = symbol.upper()
        if shares == "all":
            pos = self.portfolio.positions.get(symbol)
            if not pos:
                raise TradeError(f"You don't own any {symbol}")
            shares = pos.shares
        price = self.prices.get_price(symbol)
        trade = self.portfolio.sell(symbol, float(shares), price)
        self.portfolio.save(self.path)
        self.say(
            f"Sold {trade['shares']:g} {trade['symbol']} @ {money(price)} "
            f"for ${trade['total']:,.2f}. Cash now: ${self.portfolio.cash:,.2f}"
        )

    def cmd_portfolio(self, rest):
        p = self.portfolio
        prices = self.prices.get_prices(p.positions) if p.positions else {}
        self.say(f"Cash: ${p.cash:,.2f}")
        if p.positions:
            self.say()
            self.say(f"{'Symbol':<10}{'Amount':>12}{'Avg cost':>14}{'Price':>14}{'Value':>14}{'P/L':>14}")
            for symbol, pos in sorted(p.positions.items()):
                price = prices[symbol]
                value = pos.shares * price
                pl = value - pos.shares * pos.avg_cost
                self.say(
                    f"{symbol:<10}{pos.shares:>12g}{money(pos.avg_cost):>14}{money(price):>14}"
                    f"{value:>14,.2f}{pl:>+14,.2f}"
                )
            self.say()
        total = p.total_value(prices)
        change = total - p.starting_cash
        pct = change / p.starting_cash * 100 if p.starting_cash else 0
        self.say(f"Total value: ${total:,.2f}  ({change:+,.2f}, {pct:+.2f}% since start)")

    def cmd_history(self, rest):
        if not self.portfolio.history:
            self.say("No trades yet.")
        for t in self.portfolio.history:
            self.say(f"{t['time']}  {t['side']:<4} {t['shares']:>10g} {t['symbol']:<9} @ {money(t['price'])}  = ${t['total']:,.2f}")

    def cmd_reset(self, rest):
        cash = parse_cash(rest[0] if rest else None)
        self.portfolio = Portfolio(cash=cash, starting_cash=cash)
        self.portfolio.save(self.path)
        self.say(f"Profile '{self.profile}' reset. You have ${cash:,.2f} to trade with.")

    def cmd_profiles(self, rest):
        for name in self.profiles.names():
            p = self.profiles.load(name)
            marker = "*" if name == self.profile else " "
            self.say(f"{marker} {name:<30} cash ${p.cash:>16,.2f}   started with ${p.starting_cash:,.2f}")
        self.say("(* = the profile you're using)")

    def cmd_new(self, rest):
        if not rest:
            raise TradeError("Usage: new NAME [CASH], e.g. new Alex 25000")
        name, cash = rest[0], rest[1] if len(rest) > 1 else None
        self.profiles.create(name, cash)
        self._switch_to(name)
        self.say(f"Created profile '{self.profile}' with ${self.portfolio.cash:,.2f}. You're now using it.")

    def cmd_switch(self, rest):
        if len(rest) != 1:
            raise TradeError("Usage: switch NAME")
        self._switch_to(rest[0])
        self.say(f"Switched to '{self.profile}'. Cash: ${self.portfolio.cash:,.2f}")

    def cmd_delete(self, rest):
        if len(rest) != 1:
            raise TradeError("Usage: delete NAME")
        name = self.profiles.find(rest[0])
        if name == self.profile:
            raise TradeError("You can't delete the profile you're using. Switch to another one first.")
        self.profiles.delete(name)
        self.say(f"Deleted profile '{name}'.")

    def _switch_to(self, name):
        self.profile = self.profiles.find(name)
        self.portfolio = self.profiles.load(self.profile)

    @staticmethod
    def _symbol_and_shares(rest, verb):
        if len(rest) != 2:
            raise TradeError(f"Usage: {verb} SYMBOL AMOUNT")
        symbol, shares = rest
        if shares.lower() == "all" and verb == "sell":
            return symbol, "all"
        try:
            return symbol, float(shares)
        except ValueError:
            raise TradeError(f"'{shares}' isn't a number of shares") from None


def default_data_dir() -> Path:
    """Where profiles live: $PAPERTRADER_DATA_DIR, else a Railway volume if one is attached, else ./profiles."""
    for var in ("PAPERTRADER_DATA_DIR", "RAILWAY_VOLUME_MOUNT_PATH"):
        if os.environ.get(var):
            return Path(os.environ[var])
    return DEFAULT_DIR



def money(amount: float) -> str:
    """Dollars with cents, or more decimals for prices under $1 (like DOGE)."""
    if abs(amount) >= 1 or amount == 0:
        return f"${amount:,.2f}"
    return f"${amount:,.6f}".rstrip("0")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="papertrader",
        description="Fake stock market: trade real-world stocks with pretend money.",
        epilog=HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--profile", default=DEFAULT_PROFILE, help="which profile to use; it's created if it doesn't exist (default: default)")
    parser.add_argument("--cash", help="starting cash for a new profile, e.g. 50000 or 50k (default: 100k)")
    parser.add_argument("--data-dir", type=Path, default=default_data_dir(),
                        help="folder where profiles are saved (default: profiles, or $PAPERTRADER_DATA_DIR)")
    parser.add_argument("--web", action="store_true", help="open the browser interface instead of the terminal")
    parser.add_argument("--host", default=os.environ.get("HOST") or ("0.0.0.0" if "PORT" in os.environ else "127.0.0.1"),
                        help="address for --web to listen on (default: 127.0.0.1, or 0.0.0.0 when $PORT is set)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT") or 8000),
                        help="port for --web (default: 8000, or $PORT)")
    parser.add_argument("--no-browser", action="store_true", help="with --web, don't open a browser tab automatically")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="a command to run once; leave empty for the interactive shell")
    opts = parser.parse_args(argv)
    profiles = Profiles(opts.data_dir, legacy_file=LEGACY_FILE)

    if opts.web:
        from .web import serve

        try:
            profiles.ensure(opts.profile, opts.cash)
        except TradeError as exc:
            parser.error(str(exc))
        local = opts.host in ("127.0.0.1", "localhost")
        serve(profiles, host=opts.host, port=opts.port, open_browser=local and not opts.no_browser,
              start_profile=opts.profile, password=os.environ.get("PAPERTRADER_PASSWORD") or None)
        return 0

    try:
        app = App(profiles, opts.profile, opts.cash)
    except TradeError as exc:
        parser.error(str(exc))
    if opts.command:
        app.run(opts.command)
        return 0

    app.say(f"Fake Stock Market. Profile: {app.profile}. Type 'help' for commands.")
    app.say(f"Cash: ${app.portfolio.cash:,.2f}")
    while True:
        try:
            line = input(f"trade ({app.profile})> ")
        except (EOFError, KeyboardInterrupt):
            app.say()
            break
        try:
            words = shlex.split(line)
        except ValueError:
            words = line.split()
        if not app.run(words):
            break
    return 0


if __name__ == "__main__":
    sys.exit(main())
