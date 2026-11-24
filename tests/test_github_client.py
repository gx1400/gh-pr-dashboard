from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from gh_pr_dashboard.github import GitHubError, fetch_owner, graphql


@pytest.fixture
def fake_api(pr):
    """A GraphQL endpoint that pages two PRs, adds a partial error, or fails on demand."""
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append({"auth": self.headers["Authorization"], **body["variables"]})
            q = body["variables"]["q"]
            if "user:down" in q:
                self.send_response(502)
                self.end_headers()
                return
            if "user:broken" in q:
                payload = {"errors": [{"message": "Bad credentials"}]}
            else:
                first = body["variables"]["after"] is None
                payload = {
                    "data": {
                        "rateLimit": {"remaining": 4990, "resetAt": "x", "cost": 1},
                        "search": {
                            "pageInfo": {"hasNextPage": first, "endCursor": "c1"},
                            "nodes": [pr(1) if first else pr(2), {}],
                        },
                    }
                }
                if "user:strict" in q:
                    payload["errors"] = [{"message": "org forbids token"}] * 2
            out = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}/graphql", seen
    httpd.shutdown()


def test_fetch_owner_pages_and_sends_token(fake_api):
    api, seen = fake_api
    prs, rate, warnings = fetch_owner("someone", "tok", api)
    assert [p["number"] for p in prs] == [1, 2]
    assert rate["remaining"] == 4990
    assert warnings == []
    assert [s["after"] for s in seen] == [None, "c1"]
    assert seen[0]["auth"] == "bearer tok"
    assert seen[0]["q"] == "is:pr is:open archived:false user:someone"


def test_partial_errors_are_deduplicated(fake_api):
    api, _ = fake_api
    prs, _, warnings = fetch_owner("strict", "tok", api)
    assert len(prs) == 2
    assert warnings == ["org forbids token"]


def test_errors_without_data_raise(fake_api):
    api, _ = fake_api
    with pytest.raises(GitHubError, match="Bad credentials"):
        graphql("tok", {"q": "user:broken", "after": None}, api)


def test_http_error_raises(fake_api):
    api, _ = fake_api
    with pytest.raises(GitHubError, match="HTTP 502"):
        fetch_owner("down", "tok", api)


def test_unreachable_raises():
    with pytest.raises(GitHubError, match="cannot reach GitHub"):
        graphql("tok", {"q": "x", "after": None}, "http://127.0.0.1:9/graphql")
