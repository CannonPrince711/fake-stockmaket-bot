"""Browser interface: a small local web server with a JSON API and one HTML page."""

from __future__ import annotations

import base64
import hmac
import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import catalog
from .portfolio import Portfolio, TradeError
from .prices import CachedPrices, PriceError
from .sources import build_sources
from .profiles import DEFAULT_PROFILE, Profiles, parse_cash

PAGE = Path(__file__).with_name("static") / "index.html"


class Trader:
    """All profiles plus a price source, safe to use from several requests at once."""

    def __init__(self, profiles: Profiles, prices=None, start_profile: str = DEFAULT_PROFILE, price_ttl: float = 0.9):
        self.profiles = profiles
        self.prices = CachedPrices(prices or build_sources(), ttl=price_ttl)
        self.lock = threading.Lock()
        self.start_profile = profiles.ensure(start_profile)

    def list_profiles(self) -> dict:
        with self.lock:
            rows = []
            for name in self.profiles.names():
                p = self.profiles.load(name)
                rows.append({"name": name, "cash": p.cash, "starting_cash": p.starting_cash,
                             "holdings": len(p.positions), "trades": len(p.history)})
        return {"profiles": rows, "start": self.start_profile}

    def create_profile(self, name: str, cash) -> dict:
        with self.lock:
            self.profiles.create(name, cash)
            return {"name": self.profiles.find(name)}

    def delete_profile(self, name: str) -> dict:
        with self.lock:
            name = self.profiles.find(name)
            if len(self.profiles.names()) <= 1:
                raise TradeError("You need at least one profile")
            self.profiles.delete(name)
            if name == self.start_profile:
                self.start_profile = self.profiles.names()[0]
        return {"ok": True}

    def summary(self, profile: str) -> dict:
        with self.lock:
            name = self.profiles.find(profile)
            p = self.profiles.load(name)
            positions = {s: (pos.shares, pos.avg_cost) for s, pos in p.positions.items()}
            data = {"profile": name, "cash": p.cash, "starting_cash": p.starting_cash,
                    "history": list(reversed(p.history))}
        holdings, total, stale = [], data["cash"], False
        prices = self.prices.get_prices(positions)
        for symbol, (shares, avg_cost) in sorted(positions.items()):
            price = prices[symbol]
            if isinstance(price, PriceError):
                price, stale = avg_cost, True  # fall back so totals still add up
            value = shares * price
            total += value
            holdings.append({
                "symbol": symbol, "shares": shares, "avg_cost": avg_cost, "price": price,
                "value": value, "pl": value - shares * avg_cost,
            })
        data.update(holdings=holdings, total=total, prices_stale=stale)
        return data

    def quote(self, symbol: str) -> dict:
        price = self.prices.get_price(symbol)
        return {"symbol": symbol.strip().upper(), "price": price, "source": self.prices.source_of(symbol)}

    def history(self, symbol: str, period: str) -> dict:
        return {"symbol": symbol.strip().upper(), "period": period, "points": self.prices.get_history(symbol, period)}

    def trade(self, profile: str, side: str, symbol: str, shares) -> dict:
        symbol = symbol.strip().upper()
        with self.lock:
            name = self.profiles.find(profile)
            portfolio = self.profiles.load(name)
            if side == "sell" and str(shares).lower() == "all":
                pos = portfolio.positions.get(symbol)
                if not pos:
                    raise TradeError(f"You don't own any {symbol}")
                shares = pos.shares
            try:
                shares = float(shares)
            except (TypeError, ValueError):
                raise TradeError(f"'{shares}' isn't a number of shares") from None
            price = self.prices.fresh_price(symbol)
            do = portfolio.buy if side == "buy" else portfolio.sell
            trade = do(symbol, shares, price)
            self.profiles.save(name, portfolio)
            return trade

    def reset(self, profile: str, cash) -> dict:
        cash = parse_cash(cash)
        with self.lock:
            name = self.profiles.find(profile)
            self.profiles.save(name, Portfolio(cash=cash, starting_cash=cash))
        return {"ok": True}


def make_handler(trader: Trader, password: str | None = None):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # keep the terminal quiet
            pass

        def authorized(self) -> bool:
            """With a password set, ask the browser for it (any username works)."""
            if not password:
                return True
            header = self.headers.get("Authorization", "")
            if header.startswith("Basic "):
                try:
                    _, _, given = base64.b64decode(header[6:]).decode().partition(":")
                except ValueError:
                    given = ""
                if hmac.compare_digest(given.encode(), password.encode()):
                    return True
            body = b"Password required"
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="Fake Stock Market", charset="UTF-8"')
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return False

        def send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, data, status: int = 200) -> None:
            self.send(status, json.dumps(data).encode(), "application/json")

        def handle_api(self, action):
            try:
                self.send_json(action())
            except (TradeError, PriceError, ValueError) as exc:
                self.send_json({"error": str(exc)}, 400)

        def do_GET(self):
            url = urlparse(self.path)
            if url.path == "/healthz":  # for hosting health checks; no password needed
                return self.send(200, b"ok", "text/plain")
            if not self.authorized():
                return
            if url.path == "/":
                self.send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            elif url.path == "/api/profiles":
                self.handle_api(trader.list_profiles)
            elif url.path == "/api/catalog":
                self.send_json({"stocks": catalog.STOCKS, "crypto": catalog.CRYPTO})
            elif url.path == "/api/portfolio":
                profile = parse_qs(url.query).get("profile", [trader.start_profile])[0]
                self.handle_api(lambda: trader.summary(profile))
            elif url.path == "/api/quote":
                symbol = parse_qs(url.query).get("symbol", [""])[0]
                self.handle_api(lambda: trader.quote(symbol))
            elif url.path == "/api/history":
                query = parse_qs(url.query)
                symbol = query.get("symbol", [""])[0]
                period = query.get("period", ["1mo"])[0]
                self.handle_api(lambda: trader.history(symbol, period))
            else:
                self.send_json({"error": "Not found"}, 404)

        def do_POST(self):
            if not self.authorized():
                return
            url = urlparse(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return self.send_json({"error": "Bad request"}, 400)
            if not isinstance(body, dict):
                return self.send_json({"error": "Bad request"}, 400)
            profile = str(body.get("profile") or trader.start_profile)
            if url.path in ("/api/buy", "/api/sell"):
                side = url.path.rsplit("/", 1)[1]
                self.handle_api(lambda: trader.trade(profile, side, str(body.get("symbol", "")), body.get("shares")))
            elif url.path == "/api/reset":
                self.handle_api(lambda: trader.reset(profile, body.get("cash")))
            elif url.path == "/api/profiles":
                self.handle_api(lambda: trader.create_profile(str(body.get("name", "")), body.get("cash")))
            elif url.path == "/api/profiles/delete":
                self.handle_api(lambda: trader.delete_profile(str(body.get("name", ""))))
            else:
                self.send_json({"error": "Not found"}, 404)

    Handler.trader = trader
    return Handler


def make_server(profiles: Profiles, host: str = "127.0.0.1", port: int = 8000, prices=None,
                start_profile: str = DEFAULT_PROFILE, password: str | None = None,
                price_ttl: float = 0.9) -> ThreadingHTTPServer:
    handler = make_handler(Trader(profiles, prices, start_profile, price_ttl), password)
    return ThreadingHTTPServer((host, port), handler)


def serve(profiles: Profiles, host: str = "127.0.0.1", port: int = 8000, open_browser: bool = True,
          start_profile: str = DEFAULT_PROFILE, password: str | None = None) -> None:
    server = make_server(profiles, host=host, port=port, start_profile=start_profile, password=password)
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}" if host in ("0.0.0.0", "::", "") else f"http://{host}:{port}"
    print(f"Fake Stock Market listening on {host}:{port}, open {url}  (Ctrl+C to stop)", flush=True)
    print(f"Saving profiles in {profiles.dir.resolve()}", flush=True)
    names = getattr(server.RequestHandlerClass.trader.prices.source, "names", None)
    if names:
        print(f"Price sources, in order: {', '.join(names)}", flush=True)
    if host not in ("127.0.0.1", "localhost") and not password:
        print("Warning: anyone who can reach this address can trade and delete profiles. "
              "Set PAPERTRADER_PASSWORD to require a password.", flush=True)
    if open_browser:
        threading.Timer(0.5, webbrowser.open, [url]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()
