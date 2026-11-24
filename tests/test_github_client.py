from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from gh_pr_dashboard.github import GitHubError, ci_summary, fetch_owner, graphql


@pytest.fixture
def fake_api(pr):
    """A GraphQL endpoint that pages two PRs, adds a partial error, or fails on demand."""
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def _json(self, payload, code=200):
            out = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def do_GET(self):
            # REST Actions API: o/private has a newer and an older run of
            # workflow 7 plus workflow 8; o/denied answers 403
            seen.append({"auth": self.headers["Authorization"], "get": self.path})
            base = f"http://127.0.0.1:{self.server.server_port}"
            if self.path.startswith("/repos/o/denied/"):
                return self._json({"message": "Resource not accessible"}, 403)
            if self.path.startswith("/repos/o/norun/actions/runs"):
                return self._json({"workflow_runs": []})
            if self.path.startswith("/repos/o/private/actions/runs?head_sha=abc"):
                return self._json(
                    {
                        "workflow_runs": [
                            {"workflow_id": 7, "jobs_url": base + "/jobs/new"},
                            {"workflow_id": 8, "jobs_url": base + "/jobs/other"},
                            {"workflow_id": 7, "jobs_url": base + "/jobs/old"},
                        ]
                    }
                )
            jobs = {
                "/jobs/new?per_page=100": [
                    {"name": "lint", "status": "completed", "conclusion": "success"},
                    {
                        "name": "test",
                        "status": "completed",
                        "conclusion": "failure",
                        "html_url": "https://x/job/2",
                    },
                ],
                "/jobs/other?per_page=100": [
                    {"name": "build", "status": "in_progress", "conclusion": None}
                ],
            }
            if self.path in jobs:
                return self._json({"jobs": jobs[self.path]})
            self._json({"message": "not found"}, 404)

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
                for name in ("private", "denied", "norun"):
                    if f"user:{name}" in q:
                        status = {
                            "__typename": "StatusContext",
                            "context": "ci/x",
                            "state": "SUCCESS",
                        }
                        node = pr(
                            9,
                            repo=f"o/{name}",
                            rollup={
                                "state": "FAILURE",
                                "contexts": {"nodes": [None, status, None]},
                            },
                        )
                        payload["data"]["search"] = {
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "nodes": [{}, node],
                        }
                        path = ["search", "nodes", 1, "commits", "nodes", 0, "commit"]
                        path += ["statusCheckRollup", "contexts", "nodes"]
                        payload["errors"] = [
                            {
                                "message": "Resource not accessible by personal access token",
                                "path": [*path, i],
                            }
                            for i in (0, 2)
                        ]
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


def test_unreadable_checks_are_filled_from_actions(fake_api):
    api, seen = fake_api
    prs, _, warnings = fetch_owner("private", "tok", api)
    assert warnings == []  # every null context was accounted for
    ci = ci_summary(prs[0])
    assert ci["state"] == "fail"
    assert (ci["passed"], ci["pending"]) == (2, 1)  # status context + lint; build running
    assert ci["failed"] == [{"name": "test", "url": "https://x/job/2", "result": "failure"}]
    gets = [s["get"] for s in seen if "get" in s]
    assert "/jobs/old?per_page=100" not in gets  # superseded run of the same workflow
    assert all(s["auth"] == "bearer tok" for s in seen)


@pytest.mark.parametrize("owner", ["denied", "norun"])
def test_partial_error_stays_when_backfill_cannot_cover(fake_api, owner):
    api, _ = fake_api
    prs, _, warnings = fetch_owner(owner, "tok", api)
    assert warnings == ["Resource not accessible by personal access token"]
    assert ci_summary(prs[0])["passed"] == 1  # the readable status context still counts


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
