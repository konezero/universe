#!/usr/bin/env python3
"""Compatibility command for previously installed Stop hooks; reports only to Host."""
from universe_host_turn_hook import main, normalize_event, run_hook
if __name__ == "__main__": raise SystemExit(main())
