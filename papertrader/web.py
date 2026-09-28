"""Browser interface: a small local web server with a JSON API and one HTML page."""

from __future__ import annotations

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .portfolio import DEFAULT_STARTING_CASH, Portfolio, TradeError
from .prices import PriceError, YahooPrices

PAGE = Path(__file__).with_name("static") / "index.html"


class Trader:
    """The portfolio plus a price source, safe to use from several requests at once."""

    def __init__(self, path: Path, prices=None):
        self.path = Path(path)
        self.prices = prices or YahooPrices()
        self.lock = threading.Lock()
        self.portfolio = Portfolio.load(self.path)

    def summary(self) -> dict:
        with self.lock:
            p = self.portfolio
            positions = {s: (pos.shares, pos.avg_cost) for s, pos in p.positions.items()}
            data = {"cash": p.cash, "starting_cash": p.starting_cash, "history": list(reversed(p.history))}
        holdings, total, stale = [], data["cash"], False
        for symbol, (shares, avg_cost) in sorted(positions.items()):
            try:
                price = self.prices.get_price(symbol)
            except PriceError:
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
        return {"symbol": symbol.strip().upper(), "price": self.prices.get_price(symbol)}

    def history(self, symbol: str, period: str) -> dict:
        return {"symbol": symbol.strip().upper(), "period": period, "points": self.prices.get_history(symbol, period)}

    def trade(self, side: str, symbol: str, shares) -> dict:
        symbol = symbol.strip().upper()
        with self.lock:
            if side == "sell" and str(shares).lower() == "all":
                pos = self.portfolio.positions.get(symbol)
                if not pos:
                    raise TradeError(f"You don't own any {symbol}")
                shares = pos.shares
            try:
                shares = float(shares)
            except (TypeError, ValueError):
                raise TradeError(f"'{shares}' isn't a number of shares") from None
            price = self.prices.get_price(symbol)
            do = self.portfolio.buy if side == "buy" else self.portfolio.sell
            trade = do(symbol, shares, price)
            self.portfolio.save(self.path)
            return trade

    def reset(self, cash) -> None:
        cash = float(cash or DEFAULT_STARTING_CASH)
        if cash <= 0:
            raise TradeError("Starting cash must be greater than zero")
        with self.lock:
            self.portfolio = Portfolio(cash=cash, starting_cash=cash)
            self.portfolio.save(self.path)


def make_handler(trader: Trader):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # keep the terminal quiet
            pass

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
            if url.path == "/":
                self.send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            elif url.path == "/api/portfolio":
                self.handle_api(trader.summary)
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
            url = urlparse(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return self.send_json({"error": "Bad request"}, 400)
            if url.path in ("/api/buy", "/api/sell"):
                side = url.path.rsplit("/", 1)[1]
                self.handle_api(lambda: trader.trade(side, body.get("symbol", ""), body.get("shares")))
            elif url.path == "/api/reset":
                self.handle_api(lambda: trader.reset(body.get("cash")) or {"ok": True})
            else:
                self.send_json({"error": "Not found"}, 404)

    return Handler


def make_server(path: Path, host: str = "127.0.0.1", port: int = 8000, prices=None) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), make_handler(Trader(path, prices)))


def serve(path: Path, port: int = 8000, open_browser: bool = True) -> None:
    server = make_server(path, port=port)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    print(f"Fake Stock Market running at {url}  (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(0.5, webbrowser.open, [url]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()
