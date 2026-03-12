#!/usr/bin/env python3
"""Backward-compatible wrapper for the renamed CLI module."""

from instagram_followback_checker import main


if __name__ == "__main__":
    raise SystemExit(main())
