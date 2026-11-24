# gh-pr-dashboard

[![CI](https://github.com/gx1400/gh-pr-dashboard/actions/workflows/ci.yml/badge.svg)](https://github.com/gx1400/gh-pr-dashboard/actions/workflows/ci.yml)
[![Release](https://github.com/gx1400/gh-pr-dashboard/actions/workflows/release.yml/badge.svg)](https://github.com/gx1400/gh-pr-dashboard/actions/workflows/release.yml)
[![GitHub release](https://img.shields.io/github/v/release/gx1400/gh-pr-dashboard?sort=semver)](https://github.com/gx1400/gh-pr-dashboard/releases)
[![Container](https://img.shields.io/badge/ghcr.io-gx1400%2Fgh--pr--dashboard-2496ed?logo=docker&logoColor=white)](https://github.com/gx1400/gh-pr-dashboard/pkgs/container/gh-pr-dashboard)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![AI-DECLARATION: copilot](https://img.shields.io/badge/䷼%20AI--DECLARATION-copilot-fee2e2?labelColor=fee2e2)](AI-DECLARATION.md)

One page showing every open pull request across the GitHub users and orgs you choose. For each PR it shows:

| Column | Meaning |
|---|---|
| **Type** | `dependabot` or `bot`, `draft`, `stacked on #n` (its base branch is another open PR's head), `base of #n` (other PRs are stacked on it), `→ branch` (targets a non-default branch without being stacked) |
| **Mergeable** | GitHub's merge state: Ready, Blocked (review required or changes requested), Behind base, Conflicts, Draft, or Checking (GitHub computes mergeability lazily, and the next poll has it) |
| **CI** | the head commit's check rollup: passed, failed, running, or no CI, with each failing check linked to its run |

The counters across the top (open, ready to merge, CI failing, conflicts, stacked, Dependabot) act as filters, alongside
an owner filter, hide-Dependabot and hide-drafts checkboxes, and text search. An auto-refresh picker next to
**Refresh now** works like Grafana's: Off, 1m, 5m, 15m, 30m (the default) or 1h, remembered per browser.

It's one small Python process with no dependencies beyond the standard library and no database. It runs one GitHub
GraphQL search per owner per poll, so it uses very little of the API budget.

## Run it

```bash
docker run -d -p 8080:8080 -e OWNERS=octocat,my-org -e GITHUB_TOKEN=github_pat_... \
  --read-only ghcr.io/gx1400/gh-pr-dashboard:latest
```

Pin a version tag (`0.1.0`) or digest for real deployments. There are also examples for
[Docker Compose](docker-compose.example.yml) and [Kubernetes](deploy/kubernetes/) (kustomize, with the tokens mounted
from a Secret).

`gh-pr-dashboard --check-config` prints each owner and where its token comes from (never the token itself), then exits
non-zero if any token is missing.

## Configure

### Owners and tokens: the config file

A **fine-grained token belongs to one resource owner** (a user or an org). An org can also reject other owners'
tokens, even for its public repos, for example when its policy caps token lifetime. So the config is an **array of
owners**, and each owner can bring its own token:

```toml
# /config/config.toml (or set PRD_CONFIG to another path)
refresh_seconds = 1800
exclude_repos = ["my-org/archived-thing"]
default_token_env = "GITHUB_TOKEN"        # for owners without their own

[[owners]]
name = "octocat"                          # GITHUB_TOKEN_OCTOCAT if set, else the default

[[owners]]
name = "my-org"
token_env = "MY_ORG_TOKEN"                # token from this env var

[[owners]]
name = "other-org"
token_file = "/run/secrets/other-org"     # or from a file, re-read on every poll
```

Each owner's token comes from the first of these that applies:

1. `token_file` or `token_env` on that owner;
2. env `GITHUB_TOKEN_<OWNER>`, with the name upper-cased and every non-alphanumeric character turned into `_` (`APRS-NET` → `GITHUB_TOKEN_APRS_NET`);
3. the default token, set by `default_token_env` (`GITHUB_TOKEN` unless changed) or `default_token_file`.

Tokens are deliberately **not** allowed in the config file. The config is meant to be committed or kept in a
ConfigMap, while tokens belong in env vars or a Secret.

Full annotated example: [config.example.toml](config.example.toml).

### Env-only mode

Without a config file, it reads `OWNERS` (comma-separated), `GITHUB_TOKEN`, `GITHUB_TOKEN_<OWNER>`, `EXCLUDE_REPOS`,
`REFRESH_SECONDS` (default 1800, minimum 60) and `PORT` (default 8080). `LOG_LEVEL` works in both modes.

### Token permissions

Create a fine-grained personal access token for each owner, with access to **all repositories** and these read-only
repository permissions: Actions, Commit statuses, Contents, and Pull requests. The app only reads.

Fine-grained tokens can't read check runs (GitHub's Checks API accepts only GitHub App tokens), so on private repos
GitHub returns those checks as unreadable. The dashboard then reads the same results from the Actions API (workflow
jobs for the PR's head commit, covered by the Actions permission). Checks from other apps on private repos stay
unknown, and the page notes that as "partial results".

## Endpoints

| Path | |
|---|---|
| `GET /` | the dashboard |
| `GET /api/prs` | the cached snapshot as JSON (rows, per-owner errors, rate limit, version) |
| `POST /api/refresh` | poll GitHub now. Limited to one poll every 30 s across all clients; returns 202 when queued, 429 when it's too soon |
| `GET /healthz` | liveness/readiness |

If one owner's fetch fails, the page keeps that owner's last good data and shows the error in a banner. Partial
GraphQL errors (an org rejecting the token for some repos) show up there too, so missing PRs never go unnoticed.

The page has no authentication. Run it on a trusted network, or put an auth proxy in front of it, because it shows the
titles and branch names of private-repo PRs.

## Releases and deployment

- PR titles follow [Conventional Commits](https://www.conventionalcommits.org/), and the `pr-title` workflow
  checks them.
- [release-please](https://github.com/googleapis/release-please) keeps a release PR open. Merging it tags
  `vX.Y.Z` and publishes `ghcr.io/gx1400/gh-pr-dashboard:X.Y.Z` for amd64 and arm64, with a build provenance
  attestation.
- After publishing, the **deploy** job opens a PR in the GitOps repo that pins the new image as `tag@digest`.
  Merging that PR is the deploy, and Argo CD syncs it. To enable it, set these in the repository's Actions settings:
  - secret `DEPLOY_PR_TOKEN`: a fine-grained token for the deploy repo only, with Contents and Pull requests set to
    read/write;
  - optional variables `DEPLOY_REPO` (default `gx1400/k8s-argocd-deploy`), `DEPLOY_FILE` (default
    `manifest_deployments/pr-dashboard/deployment.yaml`) and `DEPLOY_BASE` (default `main`).

  Without the secret, the job is skipped with a notice.
- Dependabot keeps the pinned actions, dev tools and base image current.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
ruff check . && ruff format --check . && pytest --cov=gh_pr_dashboard
PRD_CONFIG=config.toml GITHUB_TOKEN=... gh-pr-dashboard
```

Python 3.11 or newer, because it uses `tomllib`.

## AI assistance

This project is developed with an AI coding assistant working as a copilot, and the maintainer reviews and merges
every change. See [AI-DECLARATION.md](AI-DECLARATION.md).

## License

[MIT](LICENSE)
