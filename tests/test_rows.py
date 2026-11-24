from __future__ import annotations

from gh_pr_dashboard.github import build_rows, ci_summary, merge_summary


def by_number(rows):
    return {r["number"]: r for r in rows}


def test_stack_chain_and_fork_and_off_default(pr):
    rows = by_number(
        build_rows(
            [
                pr(1, "main", "a"),
                pr(2, "a", "b"),
                pr(3, "b", "c"),
                pr(4, "release", "d"),
                # a fork PR whose head branch shares a name must not become a stack parent
                pr(5, "main", "a", cross=True),
            ]
        )
    )
    assert (rows[1]["stacked_on"], rows[1]["stacked_children"]) == (None, [2])
    assert (rows[2]["stacked_on"], rows[2]["stacked_children"]) == (1, [3])
    assert (rows[3]["stacked_on"], rows[3]["stacked_children"]) == (2, [])
    assert rows[4]["off_default"] is True and rows[4]["stacked_on"] is None
    assert rows[2]["off_default"] is False  # stacked, not merely off-default
    assert rows[5]["stacked_children"] == []


def test_stacking_is_per_repo(pr):
    rows = build_rows([pr(1, "main", "a", repo="o/x"), pr(2, "a", "b", repo="o/y")])
    assert all(r["stacked_on"] is None for r in rows)


def test_dependabot_bot_and_exclude(pr):
    rows = by_number(
        build_rows(
            [
                pr(1, author="dependabot", author_type="Bot"),
                pr(2, author="github-actions", author_type="Bot"),
                pr(3, repo="o/hidden"),
            ],
            exclude=frozenset({"o/hidden"}),
        )
    )
    assert rows[1]["dependabot"] and rows[1]["bot"]
    assert not rows[2]["dependabot"] and rows[2]["bot"]
    assert 3 not in rows


def test_ghost_author_and_sorting(pr):
    a = pr(2, repo="B/z")
    a["author"] = None
    rows = build_rows([a, pr(9, repo="a/y"), pr(1, repo="B/z")])
    assert [(r["repo"], r["number"]) for r in rows] == [("a/y", 9), ("B/z", 1), ("B/z", 2)]
    assert rows[2]["author"] == "ghost"


def test_ci_summary(pr):
    assert ci_summary(pr(1))["state"] == "none"
    rollup = {
        "state": "FAILURE",
        "contexts": {
            "nodes": [
                {
                    "__typename": "CheckRun",
                    "name": "lint",
                    "status": "COMPLETED",
                    "conclusion": "SUCCESS",
                },
                {
                    "__typename": "CheckRun",
                    "name": "test",
                    "status": "COMPLETED",
                    "conclusion": "FAILURE",
                    "detailsUrl": "u",
                },
                {
                    "__typename": "CheckRun",
                    "name": "slow",
                    "status": "IN_PROGRESS",
                    "conclusion": None,
                },
                {
                    "__typename": "StatusContext",
                    "context": "ext",
                    "state": "ERROR",
                    "targetUrl": "t",
                },
                {"__typename": "StatusContext", "context": "ok", "state": "SUCCESS"},
                {"__typename": "StatusContext", "context": "wait", "state": "PENDING"},
            ]
        },
    }
    ci = ci_summary(pr(1, rollup=rollup))
    assert ci["state"] == "fail"
    assert (ci["passed"], ci["pending"]) == (2, 2)
    assert [f["name"] for f in ci["failed"]] == ["test", "ext"]
    assert (
        ci_summary(pr(1, rollup={"state": "SUCCESS", "contexts": {"nodes": []}}))["state"] == "pass"
    )
    assert (
        ci_summary(pr(1, rollup={"state": "EXPECTED", "contexts": {"nodes": []}}))["state"]
        == "pending"
    )


def test_ci_summary_without_commits(pr):
    p = pr(1)
    p["commits"]["nodes"] = []
    assert ci_summary(p)["state"] == "none"


def test_merge_summary(pr):
    assert merge_summary(pr(1, draft=True)) == ("draft", "Draft")
    assert merge_summary(pr(1, mergeable="CONFLICTING"))[0] == "conflict"
    assert merge_summary(pr(1, merge_state="DIRTY"))[0] == "conflict"
    assert merge_summary(pr(1, mergeable="UNKNOWN"))[0] == "unknown"
    assert merge_summary(pr(1)) == ("ready", "Ready")
    assert merge_summary(pr(1, merge_state="HAS_HOOKS"))[0] == "ready"
    assert merge_summary(pr(1, merge_state="UNSTABLE"))[0] == "ready"
    assert merge_summary(pr(1, merge_state="BEHIND")) == ("behind", "Behind base")
    assert merge_summary(pr(1, merge_state="BLOCKED", review="REVIEW_REQUIRED")) == (
        "blocked",
        "Blocked (review required)",
    )
    assert merge_summary(pr(1, merge_state="BLOCKED")) == ("blocked", "Blocked")
    assert merge_summary(pr(1, merge_state="SOMETHING_NEW")) == ("unknown", "Something_New")
