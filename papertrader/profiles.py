"""Profiles: several separate portfolios, each saved as its own JSON file in one folder."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from .portfolio import DEFAULT_STARTING_CASH, Portfolio, TradeError

DEFAULT_DIR = Path("profiles")
DEFAULT_PROFILE = "default"
LEGACY_FILE = Path("portfolio.json")  # where portfolios lived before profiles existed

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{0,29}$")
MAX_STARTING_CASH = 1_000_000_000_000


def parse_cash(text) -> float:
    """Turn '$50,000', '50k', '2.5m' or 50000 into a positive dollar amount."""
    if text is None or str(text).strip() == "":
        return DEFAULT_STARTING_CASH
    raw = str(text).strip().lower().replace(",", "").replace("$", "").replace(" ", "")
    multiplier = {"k": 1e3, "m": 1e6, "b": 1e9}.get(raw[-1:], 1)
    if multiplier != 1:
        raw = raw[:-1]
    try:
        cash = round(float(raw) * multiplier, 2)
    except ValueError:
        raise TradeError(f"'{text}' isn't a dollar amount. Try something like 50000 or 50k.") from None
    if not 0 < cash <= MAX_STARTING_CASH:
        raise TradeError("Starting cash must be more than $0 and at most $1 trillion")
    return cash


class Profiles:
    def __init__(self, directory: Path = DEFAULT_DIR, legacy_file: Path | None = None):
        self.dir = Path(directory)
        self.legacy_file = legacy_file

    def path(self, name: str) -> Path:
        return self.dir / f"{check_name(name)}.json"

    def names(self) -> list[str]:
        if not self.dir.exists():
            return []
        return sorted((p.stem for p in self.dir.glob("*.json")), key=str.lower)

    def exists(self, name: str) -> bool:
        return self.path(name).exists()

    def find(self, name: str) -> str:
        """The stored spelling of a profile name, matched without caring about case."""
        name = check_name(name)
        for existing in self.names():
            if existing.lower() == name.lower():
                return existing
        raise TradeError(f"There's no profile called '{name}'")

    def create(self, name: str, cash=None) -> Portfolio:
        name = check_name(name)
        if any(existing.lower() == name.lower() for existing in self.names()):
            raise TradeError(f"A profile called '{name}' already exists")
        cash = parse_cash(cash)
        self.dir.mkdir(parents=True, exist_ok=True)
        portfolio = Portfolio(cash=cash, starting_cash=cash)
        portfolio.save(self.path(name))
        return portfolio

    def load(self, name: str) -> Portfolio:
        return Portfolio.load(self.path(self.find(name)))

    def save(self, name: str, portfolio: Portfolio) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        portfolio.save(self.path(name))

    def delete(self, name: str) -> None:
        self.path(self.find(name)).unlink()

    def ensure(self, name: str = DEFAULT_PROFILE, cash=None) -> str:
        """Make sure a profile exists, creating it if needed. Returns its stored name."""
        if self.legacy_file:
            self.import_legacy(self.legacy_file)
        try:
            return self.find(name)
        except TradeError:
            self.create(name, cash)
            return check_name(name)

    def import_legacy(self, legacy: Path) -> None:
        """Carry an old single-file portfolio.json over as the 'default' profile, once."""
        target = self.dir / f"{DEFAULT_PROFILE}.json"
        if Path(legacy).exists() and not target.exists():
            self.dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(legacy, target)


def check_name(name: str) -> str:
    name = (name or "").strip()
    if not NAME_RE.match(name):
        raise TradeError(
            "Profile names can be up to 30 letters, numbers, spaces, dashes or underscores"
        )
    return name
