# Fake Stock Market

Trade real-world stocks with pretend money. You start with **$100,000** in fake cash,
buy and sell at live prices from Yahoo Finance, and your portfolio is saved to
`portfolio.json` so it's still there next time.

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
sell, see a price chart for any stock, track your holdings and allocation with gain/loss, and scroll back through every trade. It follows your system light/dark setting and works on a phone-sized window. Press Ctrl+C in the
terminal to stop it. Use `--port 9000` to pick another port, or `--no-browser` to skip opening a tab.

The browser and the terminal use the same `portfolio.json`, so you can switch between them.

## Play in the terminal

Start the interactive shell:

```bash
python -m papertrader
```

```
trade> quote AAPL TSLA
AAPL     $227.52
TSLA     $260.46
trade> buy AAPL 10
Bought 10 AAPL @ $227.52 for $2,275.20. Cash left: $97,724.80
trade> portfolio
Cash: $97,724.80

Symbol      Shares    Avg cost       Price         Value           P/L
AAPL            10      227.52      229.10      2,291.00        +15.80

Total value: $100,015.80  (+15.80, +0.02% since start)
trade> sell AAPL all
trade> quit
```

(Prices above are just an example.)

Or run a single command straight from your terminal:

```bash
python -m papertrader buy NVDA 5
python -m papertrader portfolio
```

## Commands

| Command | What it does |
| --- | --- |
| `quote SYMBOL [SYMBOL ...]` | Show the current price |
| `buy SYMBOL SHARES` | Buy at the current price (fractional shares are fine) |
| `sell SYMBOL SHARES` or `sell SYMBOL all` | Sell at the current price |
| `portfolio` | Cash, holdings, and profit/loss |
| `history` | Every trade you've made |
| `reset [CASH]` | Start over, optionally with a different amount of cash |
| `help` / `quit` | |

Use `--file other.json` to keep a separate portfolio, e.g. one per person.

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
