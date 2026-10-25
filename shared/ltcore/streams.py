# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Portable CLI text streams. This helper also starts on Python 3.8."""
import os
import sys

NEEDS = []


def configure_utf8():
    """Use UTF-8 pipes and LF output, including Python children and diagnostics.

    Embedded hosts and pytest may replace streams with StringIO or None. Keep
    those objects intact; only real TextIOWrapper streams support reconfigure.
    """
    for name in ("stdin", "stdout", "stderr"):
        try:
            stream = getattr(sys, name, None)
            if stream is None or getattr(stream, "closed", False):
                continue
            if not callable(getattr(stream, "reconfigure", None)):
                continue
            if name == "stdin" and str(getattr(stream, "encoding", "")).lower().replace("-", "") == "utf8":
                continue  # Reconfiguring stdin after a read is unsupported.
            options = {"encoding": "utf-8"}
            if name != "stdin":
                options["newline"] = "\n"
            stream.reconfigure(**options)
        except Exception:
            # Closed/detached wrappers, consumed stdin and embedded host streams
            # may reject reconfiguration. CLI startup must still work.
            continue
    os.environ["PYTHONIOENCODING"] = "utf-8"
