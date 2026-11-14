import sys
import os
from pathlib import Path

# Add backend directory to sys.path
backend_path = Path(__file__).resolve().parent.parent / "backend"
if backend_path.exists() and str(backend_path) not in sys.path:
    sys.path.insert(0, str(backend_path))
else:
    alt_path = Path(__file__).resolve().parent / "backend"
    if alt_path.exists() and str(alt_path) not in sys.path:
        sys.path.insert(0, str(alt_path))
    cwd_backend = Path(os.getcwd()) / "backend"
    if cwd_backend.exists() and str(cwd_backend) not in sys.path:
        sys.path.insert(0, str(cwd_backend))

from app import app
