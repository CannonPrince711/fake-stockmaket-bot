import http.cookiejar
import json
import threading
import urllib.error
import urllib.request

import pytest

from papertrader.accounts import Accounts
from papertrader.portfolio import TradeError
from papertrader.profiles import Profiles
from papertrader.web import make_server
from tests.test_trading import FakePrices


def test_create_and_check_password(tmp_path):
    accounts = Accounts(tmp_path)
    assert accounts.create("Alex", "correct horse") == "Alex"
    assert accounts.check_password("alex", "correct horse") == "Alex"
    with pytest.raises(TradeError):
        accounts.check_password("alex", "wrong password")
    with pytest.raises(TradeError):
        accounts.create("ALEX", "another password")  # taken, whatever the case
    with pytest.raises(TradeError):
        accounts.create("Sam", "short")
    assert "correct horse" not in (tmp_path / "accounts" / "users.json").read_text()


def test_tokens(tmp_path):
    accounts = Accounts(tmp_path)
    accounts.create("Alex", "correct horse")
    token = accounts.make_token("Alex")
    assert accounts.user_for_token(token) == "Alex"
    assert Accounts(tmp_path).user_for_token(token) == "Alex"  # survives a restart
    assert accounts.user_for_token(token[:-2] + "xx") is None
    assert accounts.user_for_token("garbage") is None
    assert accounts.user_for_token(None) is None


def test_signups_can_be_closed_after_the_first(tmp_path):
    accounts = Accounts(tmp_path, allow_signups=False)
    assert accounts.signups_open()
    accounts.create("Owner", "correct horse")
    assert not accounts.signups_open()
    with pytest.raises(TradeError):
        accounts.create("Stranger", "correct horse")


def test_first_account_keeps_existing_profiles(tmp_path):
    Profiles(tmp_path).create("Old", 5000)
    accounts = Accounts(tmp_path)
    accounts.create("Owner", "correct horse")
    accounts.create("Second", "correct horse")
    assert accounts.profiles("Owner").names() == ["Old"]
    assert accounts.profiles("Second").names() == []


@pytest.fixture
def site(tmp_path):
    srv = make_server(Profiles(tmp_path), port=0, prices=FakePrices({"AAPL": 100.0}), price_ttl=0,
                      accounts=Accounts(tmp_path))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    srv.base = f"http://127.0.0.1:{srv.server_address[1]}"
    yield srv
    srv.shutdown()
    srv.server_close()


class Browser:
    """Keeps cookies between requests, like a real browser tab."""

    def __init__(self, base):
        self.base = base
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(self, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, headers={"Content-Type": "application/json"})
        try:
            with self.opener.open(req) as res:
                return res.status, json.loads(res.read())
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read())


def test_login_required_and_accounts_are_separate(site):
    alex, sam = Browser(site.base), Browser(site.base)
    assert alex.call("/api/me")[1] == {"accounts": True, "user": None, "signups": True, "first": True}
    status, err = alex.call("/api/portfolio")
    assert status == 401 and err["login"]
    assert alex.call("/api/catalog")[0] == 200

    assert alex.call("/api/signup", {"username": "Alex", "password": "correct horse"})[0] == 200
    assert alex.call("/api/me")[1]["user"] == "Alex"
    assert alex.call("/api/buy", {"symbol": "AAPL", "shares": 10})[0] == 200
    assert alex.call("/api/profiles", {"name": "Second", "cash": "5k"})[0] == 200

    assert sam.call("/api/signup", {"username": "Sam", "password": "battery staple"})[0] == 200
    _, p = sam.call("/api/portfolio")
    assert p["cash"] == 100_000 and p["holdings"] == []
    assert [x["name"] for x in sam.call("/api/profiles")[1]["profiles"]] == ["default"]
    assert sam.call("/api/portfolio?profile=Second")[0] == 400

    assert alex.call("/api/logout", {})[0] == 200
    assert alex.call("/api/portfolio")[0] == 401
    assert alex.call("/api/login", {"username": "alex", "password": "nope nope"})[0] == 400
    assert alex.call("/api/login", {"username": "alex", "password": "correct horse"})[0] == 200
    assert alex.call("/api/portfolio")[1]["cash"] == 99_000


def test_too_many_wrong_passwords(site):
    b = Browser(site.base)
    b.call("/api/signup", {"username": "Alex", "password": "correct horse"})
    site.RequestHandlerClass.site.limiter.max_failures = 3
    for _ in range(3):
        assert b.call("/api/login", {"username": "Alex", "password": "wrong!!!"})[0] == 400
    assert b.call("/api/login", {"username": "Alex", "password": "correct horse"})[0] == 429
