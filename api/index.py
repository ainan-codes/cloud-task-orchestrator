import sys
import os
import traceback
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import JSONResponse

# Top-level assignment for Vercel static AST analyzer
app = FastAPI()

# Search paths
for base in [
    Path(__file__).resolve().parent.parent,
    Path(__file__).resolve().parent,
    Path(os.getcwd()),
]:
    for cand in [base, base / "backend", base / "api" / "backend"]:
        p_str = str(cand)
        if cand.exists() and p_str not in sys.path:
            sys.path.insert(0, p_str)

_load_err = None
try:
    from app import app as backend_app
    app = backend_app
except Exception as e:
    _load_err = {
        "error": str(e),
        "traceback": traceback.format_exc(),
        "sys_path": sys.path,
        "cwd": os.getcwd(),
        "dir_contents": os.listdir(os.getcwd()),
        "file_parent": os.listdir(str(Path(__file__).resolve().parent)),
    }

if _load_err:
    @app.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
    def error_fallback(full_path: str):
        return JSONResponse(status_code=500, content=_load_err)
