from __future__ import annotations

import pytest


def make_pr(
    number: int,
    base: str = "main",
    head: str | None = None,
    *,
    repo: str = "o/r",
    default: str = "main",
    author: str = "someone",
    author_type: str = "User",
    cross: bool = False,
    draft: bool = False,
    mergeable: str = "MERGEABLE",
    merge_state: str = "CLEAN",
    review: str | None = None,
    rollup: dict | None = None,
) -> dict:
    """A GraphQL PullRequest node shaped like the dashboard's QUERY returns."""
    return {
        "number": number,
        "title": f"PR {number}",
        "url": f"https://github.com/{repo}/pull/{number}",
        "isDraft": draft,
        "createdAt": "2026-11-01T00:00:00Z",
        "updatedAt": "2026-11-02T00:00:00Z",
        "author": {"login": author, "__typename": author_type},
        "baseRefName": base,
        "headRefName": head or f"branch-{number}",
        "isCrossRepository": cross,
        "mergeable": mergeable,
        "mergeStateStatus": merge_state,
        "reviewDecision": review,
        "labels": {"nodes": []},
        "repository": {"nameWithOwner": repo, "defaultBranchRef": {"name": default}},
        "commits": {"nodes": [{"commit": {"oid": "abc", "statusCheckRollup": rollup}}]},
    }


@pytest.fixture
def pr():
    return make_pr
