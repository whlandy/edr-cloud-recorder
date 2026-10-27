#!/usr/bin/env python3
"""Run the fast checks for recorder visual-template changes."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run(*command: str) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def check_untracked_whitespace() -> None:
    paths = subprocess.check_output(
        ("git", "ls-files", "--others", "--exclude-standard", "-z"),
        cwd=ROOT,
    ).decode().split("\0")
    # git diff --no-index 的空侧不能用系统空设备（Windows 无对应设备路径）；
    # 用一个临时空文件，语义与退出码（0 干净 / 1 有空白问题）完全一致。
    with tempfile.TemporaryDirectory(prefix="check_visual_") as tmp:
        empty = Path(tmp) / "empty"
        empty.touch()
        for path in filter(None, paths):
            command = ("git", "diff", "--no-index", "--check", "--",
                       str(empty), path)
            print("+", " ".join(command), flush=True)
            result = subprocess.run(command, cwd=ROOT, capture_output=True,
                                    text=True)
            if result.returncode not in (0, 1):
                print(result.stdout, end="", file=sys.stderr)
                print(result.stderr, end="", file=sys.stderr)
                raise subprocess.CalledProcessError(result.returncode, command)


def main() -> int:
    try:
        run("node", "--check", "scripts/recorder-inject.mjs")
        run(
            sys.executable,
            "-m",
            "py_compile",
            "assets/rec_visual.py",
            "scripts/generate_trace.py",
            "scripts/trace_schema.py",
            "scripts/replay_trace.py",
            "scripts/rec_secrets.py",
            "scripts/record.py",
            "scripts/generate_spec.py",
        )
        run(
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "test/test_visual.py",
            "test/test_support.py",
            "test/test_trace_replay.py",
            "test/test_skill_structure.py",
        )
        run("git", "diff", "HEAD", "--check")
        check_untracked_whitespace()
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"visual checks failed: {exc}", file=sys.stderr)
        return 1

    print("visual checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
