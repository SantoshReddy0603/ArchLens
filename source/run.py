"""Start the ArchLens web app on http://127.0.0.1:8000."""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def load_env(path):
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main():
    load_env(HERE / ".env")
    import uvicorn
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    print("ArchLens is running at http://%s:%d  (Ctrl+C to stop)" % (host, port), flush=True)
    uvicorn.run("stacksense.web.app:app", host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
