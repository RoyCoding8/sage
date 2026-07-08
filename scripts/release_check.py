"""Run the complete non-live release gate with external credentials disabled."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"


def run(command: list[str], cwd: Path) -> None:
    """Run one release command and stop on the first failure."""
    print(f"[{cwd.name}] {' '.join(command)}", flush=True)
    subprocess.run(command, cwd=cwd, check=True, env=_safe_environment())


def _safe_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "SAGE_ENABLE_LIVE": "false",
            "SAGE_ALLOW_CLOUD_MUTATIONS": "false",
            "SAGE_QWEN_API_KEY": "invalid-release-check-key-no-billing",
            "SAGE_ALIBABA_ACCESS_KEY_ID": "",
            "SAGE_ALIBABA_ACCESS_KEY_SECRET": "",
        }
    )
    return environment


def main() -> None:
    run(["uv", "sync", "--all-groups", "--frozen"], ROOT)
    run(["uv", "run", "ruff", "check", "api.py", "src", "tests"], ROOT)
    run(["uv", "run", "pytest", "-q", "--tb=short"], ROOT)
    run(["npm", "ci"], FRONTEND)
    run(["npm", "run", "lint"], FRONTEND)
    run(["npm", "test"], FRONTEND)
    run(["npm", "exec", "tsc", "--", "--noEmit"], FRONTEND)
    run(["npm", "run", "build"], FRONTEND)
    print("Release gate passed without live model or cloud access.")


if __name__ == "__main__":
    main()
