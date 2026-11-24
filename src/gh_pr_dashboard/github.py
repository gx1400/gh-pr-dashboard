"""GitHub GraphQL fetch and the PR row model the page renders."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from . import __version__

log = logging.getLogger(__name__)

API = "https://api.github.com/graphql"

QUERY = """
query($q: String!, $after: String) {
  rateLimit { remaining resetAt cost }
  search(query: $q, type: ISSUE, first: 50, after: $after) {
    pageInfo { hasNextPage endCursor }
    nodes {
      ... on PullRequest {
        number title url isDraft createdAt updatedAt
        author { login __typename }
        baseRefName headRefName isCrossRepository
        mergeable mergeStateStatus reviewDecision
        labels(first: 10) { nodes { name } }
        repository { nameWithOwner defaultBranchRef { name } }
        commits(last: 1) { nodes { commit { oid statusCheckRollup {
          state
          contexts(first: 100) { nodes {
            __typename
            ... on CheckRun { name status conclusion detailsUrl }
            ... on StatusContext { context state targetUrl }
          } }
        } } } }
      }
    }
  }
}
"""

DEPENDABOT_LOGINS = {"dependabot", "dependabot[bot]", "app/dependabot", "dependabot-preview"}
FAIL_CONCLUSIONS = {"FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE"}
MAX_PAGES = 20  # 1000 open PRs per owner


class GitHubError(RuntimeError):
    pass


def graphql(token: str, variables: dict, api: str = API) -> tuple[dict, list[str]]:
    """Run QUERY. Returns (data, partial-error messages)."""
    body = json.dumps({"query": QUERY, "variables": variables}).encode()
    req = urllib.request.Request(
        api,
        data=body,
        method="POST",
        headers={
            "Authorization": "bearer " + token,
            "Content-Type": "application/json",
            # mergeStateStatus has historically sat behind this preview header
            "Accept": "application/vnd.github.merge-info-preview+json",
            "User-Agent": f"gh-pr-dashboard/{__version__}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        raise GitHubError(f"HTTP {e.code} from GitHub") from None
    except urllib.error.URLError as e:
        raise GitHubError(f"cannot reach GitHub: {e.reason}") from None
    errors = sorted({e.get("message", "?") for e in data.get("errors") or []})
    if errors and not data.get("data"):
        raise GitHubError("; ".join(errors))
    return data["data"], errors


def fetch_owner(
    owner: str, token: str, api: str = API
) -> tuple[list[dict], dict | None, list[str]]:
    """All open PRs in non-archived repos of a user or org.

    Returns (pull requests, rate limit, partial-error messages). Partial errors
    usually mean the org rejected the token for some repos (e.g. an org policy
    on fine-grained token lifetime), so results may be incomplete.
    """
    prs: list[dict] = []
    warnings: set[str] = set()
    rate, after = None, None
    query = f"is:pr is:open archived:false user:{owner}"
    for _ in range(MAX_PAGES):
        data, errors = graphql(token, {"q": query, "after": after}, api)
        warnings.update(errors)
        rate = data.get("rateLimit") or rate
        search = data["search"]
        prs.extend(n for n in search["nodes"] if n)
        if not search["pageInfo"]["hasNextPage"]:
            break
        after = search["pageInfo"]["endCursor"]
    return prs, rate, sorted(warnings)


def ci_summary(pr: dict) -> dict:
    nodes = pr["commits"]["nodes"]
    rollup = nodes[0]["commit"]["statusCheckRollup"] if nodes else None
    if not rollup:
        return {"state": "none", "passed": 0, "failed": [], "pending": 0}
    passed, pending, failed = 0, 0, []
    # A partial GraphQL error (e.g. a check the token can't read) leaves null
    # entries in this list; skip them rather than failing the whole snapshot.
    for c in (rollup.get("contexts") or {}).get("nodes") or []:
        if not c:
            continue
        if c["__typename"] == "CheckRun":
            if c["status"] != "COMPLETED":
                pending += 1
            elif c["conclusion"] in FAIL_CONCLUSIONS:
                failed.append(
                    {
                        "name": c["name"],
                        "url": c.get("detailsUrl"),
                        "result": c["conclusion"].lower(),
                    }
                )
            else:
                passed += 1
        elif c["__typename"] == "StatusContext":
            if c["state"] in ("PENDING", "EXPECTED"):
                pending += 1
            elif c["state"] in ("FAILURE", "ERROR"):
                failed.append(
                    {"name": c["context"], "url": c.get("targetUrl"), "result": c["state"].lower()}
                )
            else:
                passed += 1
    state = {
        "SUCCESS": "pass",
        "FAILURE": "fail",
        "ERROR": "fail",
        "PENDING": "pending",
        "EXPECTED": "pending",
    }.get(rollup["state"], "pending")
    return {"state": state, "passed": passed, "failed": failed, "pending": pending}


def merge_summary(pr: dict) -> tuple[str, str, str | None]:
    """(state class, short label, optional reason) from mergeable + mergeStateStatus.

    Labels stay one or two words so they fit the column; the reason is shown on
    a line of its own under the pill.
    """
    m, s = pr["mergeable"], pr["mergeStateStatus"]
    if pr["isDraft"]:
        return "draft", "Draft", None
    if m == "CONFLICTING" or s == "DIRTY":
        return "conflict", "Conflicts", None
    if m == "UNKNOWN" or s == "UNKNOWN":
        # GitHub computes mergeability lazily; the next poll usually has it
        return "unknown", "Checking", None
    if s in ("CLEAN", "HAS_HOOKS"):
        return "ready", "Ready", None
    if s == "UNSTABLE":
        # mergeable, but some non-required checks are failing or still running
        return "ready", "Mergeable", "optional checks not green"
    if s == "BEHIND":
        return "behind", "Behind base", None
    if s == "BLOCKED":
        why = {"REVIEW_REQUIRED": "review required", "CHANGES_REQUESTED": "changes requested"}.get(
            pr["reviewDecision"]
        )
        return "blocked", "Blocked", why
    return "unknown", s.title() if s else "Unknown", None


def build_rows(raw: list[dict], exclude: frozenset[str] = frozenset()) -> list[dict]:
    """Flatten GraphQL PR nodes into page rows, with stacking worked out per repo."""
    by_repo: dict[str, list[dict]] = {}
    for pr in raw:
        repo = pr["repository"]["nameWithOwner"]
        if repo.lower() not in exclude:
            by_repo.setdefault(repo, []).append(pr)
    rows = []
    for repo, prs in by_repo.items():
        # a PR is stacked when its base branch is another open PR's head branch
        heads = {p["headRefName"]: p for p in prs if not p["isCrossRepository"]}
        default = (prs[0]["repository"]["defaultBranchRef"] or {}).get("name")
        for pr in prs:
            parent = heads.get(pr["baseRefName"])
            parent = parent if parent is not None and parent is not pr else None
            children = sorted(
                p["number"]
                for p in prs
                if p is not pr
                and p["baseRefName"] == pr["headRefName"]
                and not pr["isCrossRepository"]
            )
            author_node = pr.get("author") or {}
            author = author_node.get("login") or "ghost"
            merge, merge_label, merge_reason = merge_summary(pr)
            rows.append(
                {
                    "repo": repo,
                    "owner": repo.split("/")[0],
                    "number": pr["number"],
                    "title": pr["title"],
                    "url": pr["url"],
                    "author": author,
                    "bot": author_node.get("__typename") == "Bot",
                    "dependabot": author.lower() in DEPENDABOT_LOGINS,
                    "draft": pr["isDraft"],
                    "base": pr["baseRefName"],
                    "head": pr["headRefName"],
                    "default_branch": default,
                    "stacked_on": parent["number"] if parent else None,
                    "stacked_children": children,
                    "off_default": bool(default) and pr["baseRefName"] != default and not parent,
                    "merge": merge,
                    "merge_label": merge_label,
                    "merge_reason": merge_reason,
                    "review": pr["reviewDecision"],
                    "ci": ci_summary(pr),
                    "labels": [label["name"] for label in pr["labels"]["nodes"] if label],
                    "created": pr["createdAt"],
                    "updated": pr["updatedAt"],
                }
            )
    rows.sort(key=lambda r: (r["repo"].lower(), r["number"]))
    return rows
