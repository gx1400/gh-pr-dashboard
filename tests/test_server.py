from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from gh_pr_dashboard import __version__
from gh_pr_dashboard.config import Config, Owner, TokenSource
from gh_pr_dashboard.github import GitHubError
from gh_pr_dashboard.server import State, make_handler


def config(*names, refresh=1800):
    return Config(
        owners=tuple(Owner(n, TokenSource(env=f"TOK_{n.upper()}")) for n in names),
        refresh_seconds=refresh,
    )


def test_poll_keeps_last_good_data_and_reports_errors(pr):
    calls = {"n": 0}

    def fetcher(owner, token):
        calls["n"] += 1
        if owner == "bad":
            raise GitHubError("HTTP 401 from GitHub")
        if owner == "partial":
            return [pr(7, repo="partial/r")], None, ["org forbids this token"]
        if calls["n"] > 3:  # second poll: good owner now fails
            raise GitHubError("HTTP 502 from GitHub")
        return [pr(1, repo="good/r")], {"remaining": 4999}, []

    env = {"TOK_GOOD": "t", "TOK_BAD": "t", "TOK_PARTIAL": "t"}
    state = State(config("good", "bad", "partial", "notoken"), env, fetcher)
    state.poll()
    snap = state.snapshot()
    assert [p["number"] for p in snap["prs"]] == [1, 7]
    assert snap["errors"]["bad"] == "HTTP 401 from GitHub"
    assert snap["errors"]["partial"].startswith("partial results: org forbids")
    assert "empty or unset" in snap["errors"]["notoken"]
    assert snap["rate_limit"] == {"remaining": 4999}
    assert snap["version"] == __version__

    state.poll()  # good owner fails this time: its PRs stay, with an error
    snap = state.snapshot()
    assert [p["number"] for p in snap["prs"]] == [1, 7]
    assert snap["errors"]["good"] == "HTTP 502 from GitHub"


def test_refresh_is_throttled():
    state = State(config("x"), {}, lambda o, t: ([], None, []))
    assert state.request_refresh() is True
    assert state.wake.is_set()
    assert state.request_refresh() is False


@pytest.fixture
def server(pr):
    state = State(config("o"), {"TOK_O": "t"}, lambda o, t: ([pr(3)], None, []))
    state.poll()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def fetch(url, method="GET"):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method=method), timeout=5) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def test_http_endpoints(server):
    status, headers, body = fetch(server + "/")
    assert status == 200 and b"Open pull requests" in body
    assert headers["Content-Type"].startswith("text/html")

    status, _, body = fetch(server + "/api/prs")
    data = json.loads(body)
    assert status == 200 and data["owners"] == ["o"] and data["prs"][0]["number"] == 3

    assert fetch(server + "/healthz")[0] == 200
    assert fetch(server + "/nope")[0] == 404
    assert fetch(server + "/api/refresh", "POST")[0] == 202
    assert fetch(server + "/api/refresh", "POST")[0] == 429
    assert fetch(server + "/elsewhere", "POST")[0] == 404


def test_api_error_is_json(pr):
    bad = pr(4)
    bad["labels"] = None  # anything build_rows can't handle
    state = State(config("o"), {"TOK_O": "t"}, lambda o, t: ([bad], None, []))
    state.poll()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        status, headers, body = fetch(f"http://127.0.0.1:{httpd.server_port}/api/prs")
    finally:
        httpd.shutdown()
    assert status == 500 and headers["Content-Type"] == "application/json"
    assert json.loads(body)["error"].startswith("TypeError")
