#!/usr/bin/env python3
"""Entry point: python run.py [--prompt "..."] [--provider openrouter] [-m model]"""

from __future__ import annotations

import sys

from agent.cli import main

if __name__ == "__main__":
    sys.exit(main())
