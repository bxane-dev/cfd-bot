from __future__ import annotations

import multiprocessing
import os
import runpy
import shutil
import sys
from pathlib import Path


def bundle_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])).resolve()


def prepare_data_root() -> Path:
    root = Path(os.getenv("CFD_DATA_DIR") or bundle_root()).resolve()
    root.mkdir(parents=True, exist_ok=True)
    (root / "logs").mkdir(parents=True, exist_ok=True)

    bundled = bundle_root()
    for name in ("config.yaml", ".env.example"):
        src = bundled / name
        dst = root / name
        if src.exists() and not dst.exists():
            shutil.copy2(src, dst)

    env_file = root / ".env"
    env_example = root / ".env.example"
    if not env_file.exists() and env_example.exists():
        shutil.copy2(env_example, env_file)

    os.environ["CFD_DATA_DIR"] = str(root)
    os.chdir(root)
    return root


def dispatch() -> int:
    prepare_data_root()

    import app  # noqa: F401

    if len(sys.argv) >= 3 and sys.argv[1] == "-m" and sys.argv[2] == "research.walk":
        sys.argv = [sys.argv[0], *sys.argv[3:]]
        runpy.run_module("research.walk", run_name="__main__")
        return 0

    from app.auto import main

    main()
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(dispatch())
