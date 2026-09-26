"""simgen package.

Reconfigures stdout/stderr to UTF-8 (with lossy fallback instead of a crash) the moment
anything imports simgen. Root cause: generated content and blueprints are full of unicode
math (theta, arrows, fractions, superscripts) and a stock Windows console is still stuck on
cp1252/cp437, so a bare print() of that text raises UnicodeEncodeError. Every entry point
(app.py, __main__.py, make_blueprints.py, dryrun.py) imports this package, so fixing it here
once covers all of them instead of patching each script.
"""
import sys

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")
