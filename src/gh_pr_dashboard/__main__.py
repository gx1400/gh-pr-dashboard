"""Entry point: `gh-pr-dashboard` / `python -m gh_pr_dashboard`."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys

from . import __version__
from .config import ConfigError, load


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gh-pr-dashboard", description="Open GitHub PRs across users and orgs on one page."
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="print the resolved owners and where each token comes from (never the token), then exit",
    )
    parser.add_argument("--host", default="0.0.0.0", help="listen address (default 0.0.0.0)")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        config = load()
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2

    if args.check_config:
        print(
            f"config: {config.source}, refresh every {config.refresh_seconds}s, port {config.port}"
        )
        bad = 0
        for owner in config.owners:
            try:
                owner.token.read(os.environ)
                status = "ok"
            except ConfigError as e:
                status, bad = f"MISSING ({e})", bad + 1
            print(f"  {owner.name}: token from {owner.token.describe()}: {status}")
        return 1 if bad else 0

    # PID 1 in a container: exit promptly on SIGTERM instead of waiting for SIGKILL
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    logging.getLogger(__name__).info(
        "gh-pr-dashboard %s: owners=%s refresh=%ss (%s)",
        __version__,
        ",".join(o.name for o in config.owners),
        config.refresh_seconds,
        config.source,
    )
    from .server import serve

    serve(config, os.environ, host=args.host)
    return 0


if __name__ == "__main__":
    sys.exit(main())
