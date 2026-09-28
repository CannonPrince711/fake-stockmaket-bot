# Fake Stock Market

Trade real-world stocks with pretend money. Pick how much fake cash you start with
($100,000 unless you say otherwise), buy and sell at live prices from Yahoo Finance,
and keep as many separate **profiles** as you like, one per person or per strategy.
Everything is saved in the `profiles/` folder so it's still there next time.

## Setup

Needs Python 3.9 or newer.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Play in your browser

```bash
python -m papertrader --web
```

This opens http://127.0.0.1:8000 in your browser. From there you can look up prices, buy and
sell, see a price chart for any stock, track your holdings and allocation with gain/loss, and
scroll back through every trade. It follows your system light/dark setting and works on a
phone-sized window. Press Ctrl+C in the terminal to stop it. Use `--port 9000` to pick another
port, or `--no-browser` to skip opening a tab.

Click the profile button in the top right to switch profiles, create a new one with its own
starting cash, reset the current one with a new amount, or delete it.

The browser and the terminal share the same profiles, so you can switch between them.

## Play in the terminal

Start the interactive shell:

```bash
python -m papertrader
```

```
trade (default)> quote AAPL TSLA
AAPL     $227.52
TSLA     $260.46
trade (default)> buy AAPL 10
Bought 10 AAPL @ $227.52 for $2,275.20. Cash left: $97,724.80
trade (default)> portfolio
Cash: $97,724.80

Symbol      Shares    Avg cost       Price         Value           P/L
AAPL            10      227.52      229.10      2,291.00        +15.80

Total value: $100,015.80  (+15.80, +0.02% since start)
trade (default)> sell AAPL all
trade (default)> quit
```

(Prices above are just an example.)

Or run a single command straight from your terminal:

```bash
python -m papertrader buy NVDA 5
python -m papertrader portfolio
```

## Profiles and starting cash

Each profile is its own portfolio with its own cash, holdings and history.

```bash
python -m papertrader --profile Alex --cash 25k   # creates Alex with $25,000 the first time
python -m papertrader --profile Alex              # comes back to Alex later
python -m papertrader --web --profile Alex        # opens the browser on Alex
```

Amounts can be written as `25000`, `$25,000`, `25k` or `1.5m`. Without `--profile` you use the
profile called `default`, and `--cash` only matters when a profile is first created.

## Commands

| Command | What it does |
| --- | --- |
| `quote SYMBOL [SYMBOL ...]` | Show the current price |
| `buy SYMBOL SHARES` | Buy at the current price (fractional shares are fine) |
| `sell SYMBOL SHARES` or `sell SYMBOL all` | Sell at the current price |
| `portfolio` | Cash, holdings, and profit/loss |
| `history` | Every trade you've made |
| `reset [CASH]` | Start this profile over, optionally with a different amount of cash |
| `profiles` | List your profiles |
| `new NAME [CASH]` | Create a profile (e.g. `new Alex 25k`) and switch to it |
| `switch NAME` | Switch to another profile |
| `delete NAME` | Delete a profile |
| `help` / `quit` | |

Profile names with spaces need quotes: `new "Retirement Fund" 1m`.

## Notes

- Tickers are Yahoo Finance symbols: `AAPL`, `MSFT`, `SPY`, `BTC-USD`, `SHOP.TO`, and so on.
- Trades fill at Yahoo's latest price. Outside market hours that's the last close.
- No fees, no shorting, no margin: you can only spend the cash you have and sell what you own.

## Tests

```bash
pip install pytest
python -m pytest
```

The tests use fake prices, so they don't need an internet connection.
