"""merdag package."""

import sys
import io

# Ensure UTF-8 output on Windows for emoji-heavy CLI output
if sys.stdout and sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr and sys.stderr.encoding != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

__all__ = ["__version__"]

__version__ = "0.1.0"
