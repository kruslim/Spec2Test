"""PostToolUse hook: format a Python file Claude just edited, and sort its imports.

Reads the hook event (JSON) on stdin. Only `ruff format` and the import-sorting fix
run: a full `ruff check --fix` would delete an import added one edit before its first
use. Lint errors are left for `uv run ruff check .`.
"""

import json
import subprocess
import sys
from pathlib import Path


def main() -> None:
    event = json.load(sys.stdin)
    path = Path(event.get("tool_input", {}).get("file_path", ""))
    if path.suffix != ".py" or not path.is_file():
        return
    ruff = [sys.executable, "-m", "ruff"]
    subprocess.run([*ruff, "format", "--quiet", str(path)], check=False)
    subprocess.run([*ruff, "check", "--quiet", "--fix", "--select", "I", str(path)], check=False)


if __name__ == "__main__":
    main()
