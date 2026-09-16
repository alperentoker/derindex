#!/usr/bin/env python3
"""Derindex: Personal Search Engine & Semantic Search System entrypoint."""

import sys
import logging
from pathlib import Path

# Configure logging format and level
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.cli.cli import main

if __name__ == "__main__":
    main()
