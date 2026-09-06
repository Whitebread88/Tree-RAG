"""Shared test setup.

`db` reads DATABASE_URL at import time and every service module imports it, so
a placeholder has to be in the environment before collection. create_engine
does not connect, so no database is needed — the tests here exercise pure
logic and never touch the engine.
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost:5432/test")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
