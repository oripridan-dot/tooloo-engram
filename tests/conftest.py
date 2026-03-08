"""Conftest for tooloo-engram tests — sets up sys.path for module resolution."""

import sys
from pathlib import Path

# Add workspace root (for experiments.project_engram.*)
_workspace = Path(__file__).parent.parent.parent
if str(_workspace) not in sys.path:
    sys.path.insert(0, str(_workspace))

# Add tooloo-engram root (for training_camp.*)
_tooloo_engram_root = Path(__file__).parent.parent
if str(_tooloo_engram_root) not in sys.path:
    sys.path.insert(0, str(_tooloo_engram_root))
