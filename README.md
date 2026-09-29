# Fake Stock Market

Trade real-world stocks and crypto with pretend money. Pick how much fake cash you start with
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

Prices, holdings and your total value refresh on their own every second while the tab is
open (a green "Live" dot shows it's working), and numbers flash green or red when they move.
Trades always fill at a freshly fetched price.

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
| `buy SYMBOL AMOUNT` | Buy shares or coins at the current price (fractions are fine) |
| `sell SYMBOL AMOUNT` or `sell SYMBOL all` | Sell at the current price |
| `stocks` | List popular stocks and ETFs |
| `crypto` | List popular cryptocurrencies you can trade |
| `portfolio` | Cash, holdings, and profit/loss |
| `history` | Every trade you've made |
| `reset [CASH]` | Start this profile over, optionally with a different amount of cash |
| `profiles` | List your profiles |
| `new NAME [CASH]` | Create a profile (e.g. `new Alex 25k`) and switch to it |
| `switch NAME` | Switch to another profile |
| `delete NAME` | Delete a profile |
| `help` / `quit` | |

Profile names with spaces need quotes: `new "Retirement Fund" 1m`.

## Host it online (Railway)

The repo is ready to deploy on [Railway](https://railway.com) as is:

1. On Railway, click **New Project → Deploy from GitHub repo** and pick this repo. Railway
   installs `requirements.txt` and starts the app using `railway.json`.
2. In the service's **Settings → Networking**, click **Generate Domain** to get a public URL.
3. **Add a volume so profiles survive redeploys.** Add a volume to the service (right-click it on the
   project canvas, or press ⌘K / Ctrl+K and search for "volume") and mount it at `/data`. The app finds Railway volumes on its own
   and saves profiles there. Without a volume, every redeploy starts everyone from scratch.
4. **Set a password.** Under **Variables**, add `PAPERTRADER_PASSWORD`. Your browser will ask
   for it when you open the site (any username works). Without one, anyone with the link can
   trade and delete your profiles.

Settings the app reads from the environment:

| Variable | What it does |
| --- | --- |
| `PORT` | Port to listen on. Railway sets this; when it's set the app listens on all addresses and doesn't try to open a browser. |
| `PAPERTRADER_PASSWORD` | Require this password to use the site. |
| `PAPERTRADER_DATA_DIR` | Folder to save profiles in. Defaults to the Railway volume if one is attached, otherwise `profiles/`. |

A `Procfile` is included too, so hosts like Render or Heroku can start it with the same command.
Yahoo Finance sometimes rate-limits requests from cloud servers; if quotes start failing on the
hosted site, wait a bit and try again.

## Crypto

Crypto trades just like stocks, using Yahoo's `COIN-USD` tickers: `BTC-USD` (Bitcoin),
`ETH-USD` (Ethereum), `SOL-USD`, `XRP-USD`, `DOGE-USD`, `ADA-USD`, `LTC-USD` and any other coin
Yahoo lists. You can buy fractions of a coin:

```bash
python -m papertrader buy BTC-USD 0.05
python -m papertrader buy DOGE-USD 10000
```

In the browser, switch the quick picks to **Crypto** to see popular coins, and click **+ more**
to see the full list. The search box also suggests popular stocks and coins as you type, and
understands names like "Apple" or "Bitcoin". Crypto prices update
around the clock, including weekends. Note that a plain `BTC` is a stock ticker (a Bitcoin ETF),
not Bitcoin itself, so use `BTC-USD` for the coin.

## Price sources

Prices come from Yahoo Finance first. If Yahoo is down or rate-limiting, the app automatically
falls back to other free services, and the page shows which one a price came from ("via Stooq"):

| Source | Covers | Key needed? |
| --- | --- | --- |
| Yahoo Finance | Stocks, ETFs, crypto, charts | No |
| [Finnhub](https://finnhub.io) | Real-time US stock quotes (no charts on the free plan) | Yes, free: sign up at finnhub.io and copy your API key |
| [CoinGecko](https://www.coingecko.com/en/api) | Crypto prices and charts | No (an optional free demo key gives higher limits) |
| [Stooq](https://stooq.com) | US stock quotes (can be delayed) and daily charts | No |

A service that's down is skipped for 30 seconds so it doesn't slow down refreshes. To turn on
Finnhub or change the order, set these (locally, or under **Variables** on Railway):

| Variable | What it does |
| --- | --- |
| `FINNHUB_API_KEY` | Adds Finnhub as the first backup for stocks. |
| `COINGECKO_API_KEY` | Uses your free CoinGecko demo key. |
| `PAPERTRADER_PRICE_SOURCES` | Which sources to use and in what order, e.g. `finnhub,yahoo,coingecko,stooq`. Default: `yahoo,finnhub,coingecko,stooq`. |

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
