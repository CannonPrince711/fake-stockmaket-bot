"""Portfolio state and trading rules. No network access happens here."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_STARTING_CASH = 100_000.00


class TradeError(Exception):
    """Raised when a trade can't be made (not enough cash, not enough shares, bad input)."""


@dataclass
class Position:
    shares: float
    avg_cost: float  # average price paid per share


@dataclass
class Portfolio:
    cash: float = DEFAULT_STARTING_CASH
    starting_cash: float = DEFAULT_STARTING_CASH
    positions: dict[str, Position] = field(default_factory=dict)
    history: list[dict] = field(default_factory=list)

    def buy(self, symbol: str, shares: float, price: float) -> dict:
        symbol = _clean_symbol(symbol)
        _check_positive(shares, price)
        cost = round(shares * price, 2)
        if cost > self.cash + 1e-9:
            raise TradeError(
                f"Not enough cash: {shares:g} {symbol} costs ${cost:,.2f}, you have ${self.cash:,.2f}"
            )
        pos = self.positions.get(symbol)
        if pos:
            total = pos.shares + shares
            pos.avg_cost = (pos.shares * pos.avg_cost + shares * price) / total
            pos.shares = total
        else:
            self.positions[symbol] = Position(shares=shares, avg_cost=price)
        self.cash = round(self.cash - cost, 2)
        return self._record("BUY", symbol, shares, price, cost)

    def sell(self, symbol: str, shares: float, price: float) -> dict:
        symbol = _clean_symbol(symbol)
        _check_positive(shares, price)
        pos = self.positions.get(symbol)
        held = pos.shares if pos else 0
        if shares > held + 1e-9:
            raise TradeError(f"You only own {held:g} shares of {symbol}")
        proceeds = round(shares * price, 2)
        pos.shares -= shares
        if pos.shares <= 1e-9:
            del self.positions[symbol]
        self.cash = round(self.cash + proceeds, 2)
        return self._record("SELL", symbol, shares, price, proceeds)

    def total_value(self, prices: dict[str, float]) -> float:
        return self.cash + sum(p.shares * prices[s] for s, p in self.positions.items())

    def _record(self, side: str, symbol: str, shares: float, price: float, total: float) -> dict:
        trade = {
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "side": side,
            "symbol": symbol,
            "shares": shares,
            "price": round(price, 4),
            "total": total,
        }
        self.history.append(trade)
        return trade

    # --- saving / loading ---

    def to_dict(self) -> dict:
        return {
            "cash": self.cash,
            "starting_cash": self.starting_cash,
            "positions": {s: vars(p) for s, p in self.positions.items()},
            "history": self.history,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Portfolio":
        return cls(
            cash=data["cash"],
            starting_cash=data.get("starting_cash", DEFAULT_STARTING_CASH),
            positions={s: Position(**p) for s, p in data.get("positions", {}).items()},
            history=data.get("history", []),
        )

    def save(self, path: Path) -> None:
        path = Path(path)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2))
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path, starting_cash: float = DEFAULT_STARTING_CASH) -> "Portfolio":
        path = Path(path)
        if not path.exists():
            return cls(cash=starting_cash, starting_cash=starting_cash)
        return cls.from_dict(json.loads(path.read_text()))


def _clean_symbol(symbol: str) -> str:
    symbol = symbol.strip().upper()
    if not symbol:
        raise TradeError("Ticker symbol is required")
    return symbol


def _check_positive(shares: float, price: float) -> None:
    if shares <= 0:
        raise TradeError("Number of shares must be greater than zero")
    if price <= 0:
        raise TradeError("Price must be greater than zero")
