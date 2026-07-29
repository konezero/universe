#!/usr/bin/env python3
"""Emit exact arguments and stdin metadata for native transport tests."""

from __future__ import annotations

import hashlib
import json
import sys


def main() -> int:
    stdin_bytes = sys.stdin.buffer.read()
    rendered = json.dumps(
        {
            "argv": sys.argv[1:],
            "stdin_size": len(stdin_bytes),
            "stdin_sha256": hashlib.sha256(stdin_bytes).hexdigest(),
        },
        ensure_ascii=False,
    )
    sys.stdout.buffer.write((rendered + "\n").encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
