import sys
import os
import traceback
from pathlib import Path

# Add paths to sys.path
root_dir = Path(__file__).resolve().parent.parent
backend_dir = root_dir / "backend"
for p in [str(backend_dir), str(root_dir)]:
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    from app import app
except Exception as e:
    tb = traceback.format_exc()
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    app = FastAPI()
    
    @app.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
    def catch_all(full_path: str):
        return JSONResponse(
            status_code=500,
            content={
                "error": str(e),
                "traceback": tb,
                "sys_path": sys.path,
                "cwd": os.getcwd(),
                "root_files": os.listdir(str(root_dir)) if root_dir.exists() else [],
            }
        )
