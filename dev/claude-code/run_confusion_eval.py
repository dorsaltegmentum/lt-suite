# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Claude Code only: score recorded single-label routing observations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from eval_support import InputError, read_cases, score_confusion


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expectations", required=True, type=Path)
    parser.add_argument("--observations", required=True, type=Path)
    args = parser.parse_args(argv)

    try:
        report = score_confusion(
            read_cases(args.expectations),
            read_cases(args.observations),
        )
    except InputError as exc:
        print(f"Input error: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
