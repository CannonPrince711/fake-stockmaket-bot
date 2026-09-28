"""Command-line interface: run one command, or start an interactive trading shell."""

from __future__ import annotations

import argparse
import shlex
import sys
from pathlib import Path

from .portfolio import DEFAULT_STARTING_CASH, Portfolio, TradeError
from .prices import PriceError, YahooPrices

DEFAULT_FILE = Path("portfolio.json")

HELP = """Commands:
  quote SYMBOL [SYMBOL ...]   show the current real-world price
  buy SYMBOL SHARES           buy shares at the current price
  sell SYMBOL SHARES|all      sell shares at the current price
  portfolio                   show cash, holdings and profit/loss
  history                     show every trade you've made
  reset [CASH]                start over (default $100,000)
  help                        show this list
  quit                        leave"""


class App:
    def __init__(self, path: Path, prices=None, out=sys.stdout):
        self.path = Path(path)
        self.prices = prices or YahooPrices()
        self.out = out
        self.portfolio = Portfolio.load(self.path)

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
            self.say(f"{symbol.upper():<8} ${self.prices.get_price(symbol):,.2f}")

    def cmd_buy(self, rest):
        symbol, shares = self._symbol_and_shares(rest, "buy")
        price = self.prices.get_price(symbol)
        trade = self.portfolio.buy(symbol, float(shares), price)
        self.portfolio.save(self.path)
        self.say(
            f"Bought {trade['shares']:g} {trade['symbol']} @ ${price:,.2f} "
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
            f"Sold {trade['shares']:g} {trade['symbol']} @ ${price:,.2f} "
            f"for ${trade['total']:,.2f}. Cash now: ${self.portfolio.cash:,.2f}"
        )

    def cmd_portfolio(self, rest):
        p = self.portfolio
        prices = self.prices.get_prices(p.positions) if p.positions else {}
        self.say(f"Cash: ${p.cash:,.2f}")
        if p.positions:
            self.say()
            self.say(f"{'Symbol':<8}{'Shares':>10}{'Avg cost':>12}{'Price':>12}{'Value':>14}{'P/L':>14}")
            for symbol, pos in sorted(p.positions.items()):
                price = prices[symbol]
                value = pos.shares * price
                pl = value - pos.shares * pos.avg_cost
                self.say(
                    f"{symbol:<8}{pos.shares:>10g}{pos.avg_cost:>12,.2f}{price:>12,.2f}"
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
            self.say(f"{t['time']}  {t['side']:<4} {t['shares']:>8g} {t['symbol']:<6} @ ${t['price']:,.2f}  = ${t['total']:,.2f}")

    def cmd_reset(self, rest):
        cash = float(rest[0].replace(",", "").lstrip("$")) if rest else DEFAULT_STARTING_CASH
        if cash <= 0:
            raise TradeError("Starting cash must be greater than zero")
        self.portfolio = Portfolio(cash=cash, starting_cash=cash)
        self.portfolio.save(self.path)
        self.say(f"Portfolio reset. You have ${cash:,.2f} to trade with.")

    @staticmethod
    def _symbol_and_shares(rest, verb):
        if len(rest) != 2:
            raise TradeError(f"Usage: {verb} SYMBOL SHARES")
        symbol, shares = rest
        if shares.lower() == "all" and verb == "sell":
            return symbol, "all"
        try:
            return symbol, float(shares)
        except ValueError:
            raise TradeError(f"'{shares}' isn't a number of shares") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="papertrader",
        description="Fake stock market: trade real-world stocks with pretend money.",
        epilog=HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--file", type=Path, default=DEFAULT_FILE, help="where to save your portfolio (default: portfolio.json)")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="a command to run once; leave empty for the interactive shell")
    opts = parser.parse_args(argv)

    app = App(opts.file)
    if opts.command:
        app.run(opts.command)
        return 0

    app.say(f"Fake Stock Market. Portfolio file: {opts.file}. Type 'help' for commands.")
    app.say(f"Cash: ${app.portfolio.cash:,.2f}")
    while True:
        try:
            line = input("trade> ")
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
