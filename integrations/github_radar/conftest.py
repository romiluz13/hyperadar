"""Conftest for github_radar — adds this directory to sys.path so test
files using ``import agent`` (instead of ``from github_radar import agent``)
continue to work with ``__init__.py`` present.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
