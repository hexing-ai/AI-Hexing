"""Check the Git index (or candidate worktree) without printing secret values."""

import argparse
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".wav", ".mp3", ".m4a", ".zip", ".pem", ".key"}
PRIVATE_PARTS = {"data", ".venv", "node_modules", ".next", ".next-qa", "playwright-report", "test-results"}
SECRET = re.compile(rb"(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)")
LOCAL_PATH = re.compile(rb"/" + rb"Users/[^/\s]+/")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worktree", action="store_true", help="also check unignored, untracked files")
    args = parser.parse_args()
    command = ["git", "ls-files", "-z"]
    if args.worktree:
        command += ["--cached", "--others", "--exclude-standard"]
    paths = sorted(set(filter(None, subprocess.check_output(command, cwd=ROOT).decode().split("\0"))))
    failures = []
    if not paths:
        failures.append("No files to inspect; stage the release or use --worktree.")
    for name in paths:
        path = Path(name)
        private = (
            bool(set(path.parts) & PRIVATE_PARTS)
            or (path.name.startswith(".env") and path.name != ".env.example")
            or path.suffix.lower() in PRIVATE_SUFFIXES
            or "通用技术栈手册" in path.name
            or "通用前端技术栈手册" in path.name
            or name.startswith("evidence/")
            or (name.startswith("docs/") and not name.startswith("docs/public/"))
        )
        if private:
            failures.append(f"Private/unreviewed file: {name}")
            continue
        if args.worktree:
            if (ROOT / name).is_symlink():
                failures.append(f"Review symlink: {name}")
                continue
            content = (ROOT / name).read_bytes()
        else:
            content = subprocess.check_output(["git", "show", f":{name}"], cwd=ROOT)
        if len(content) > 10 * 1024 * 1024:
            failures.append(f"Review large file: {name}")
        if SECRET.search(content):
            failures.append(f"Credential-like content: {name}")
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"} and LOCAL_PATH.search(content):
            failures.append(f"Local account path: {name}")
    if failures:
        print("Release check failed:")
        print("\n".join(f"- {message}" for message in failures))
        return 1
    print(f"Release check passed: {len(paths)} files; no listed private files or credential patterns.")
    print("Heuristic scan only; review screenshots, provenance and content before publishing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
