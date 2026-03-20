"""Root conftest — add repo root to sys.path so custom_components is importable."""
import sys
import os

# Ensure the repo root is on the path so pytest can find custom_components/
sys.path.insert(0, os.path.dirname(__file__))
