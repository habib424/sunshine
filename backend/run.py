import os
import sys

# Ensure backend directory is on the path
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import uvicorn

if __name__ == "__main__":
    # reload=False: uvicorn's auto-reloader crashes on Windows + Python 3.14
    # (WinError 6 in CTRL_C_EVENT signal handling). Restart the server manually
    # after backend code changes.
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)
