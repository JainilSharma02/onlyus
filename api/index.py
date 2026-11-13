import sys
from pathlib import Path

# Add onlyus folder to sys.path so imports work smoothly
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR / "onlyus"))

from onlyus.main import app
