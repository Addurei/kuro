"""Make a Kuro release: a zip of the code plus the latest.json the updater reads.

    python scripts/make_release.py 1.1.0 [--notes "What changed"]

Writes releases/kuro-1.1.0.zip and releases/latest.json, and stamps
app/version.py. The releases/ folder is part of the repo: commit and push it,
and every running Kuro (update_url points at latest.json's raw GitHub URL)
picks the new version up within a day and applies it on its next start.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INCLUDE = ["main.py", "run.bat", "setup.bat", "requirements.txt", "README.md",
           "TESTING.md", "pytest.ini"]
INCLUDE_DIRS = ["app", "assets", "server"]
SELF = "releases"
SKIP = {"__pycache__", ".pyc", ".pyo"}


def files():
    for name in INCLUDE:
        p = ROOT / name
        if p.is_file():
            yield p
    for d in INCLUDE_DIRS:
        for p in sorted((ROOT / d).rglob("*")):
            if p.is_file() and not any(part in SKIP for part in p.parts) \
                    and p.suffix not in (".pyc", ".pyo"):
                yield p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("version")
    ap.add_argument("--notes", default="")
    args = ap.parse_args()
    if not re.fullmatch(r"\d+\.\d+\.\d+", args.version):
        print("version must look like 1.2.3")
        return 1
    # stamp
    vp = ROOT / "app" / "version.py"
    vp.write_text('"""Kuro\'s version. scripts/make_release.py bumps this and stamps '
                  'releases."""\nVERSION = "%s"\n' % args.version, encoding="utf-8")
    dist = ROOT / "releases"
    dist.mkdir(exist_ok=True)
    for old in dist.glob("kuro-*.zip"):           # keep the repo slim: newest zip only
        old.unlink()
    zname = dist / f"kuro-{args.version}.zip"
    with zipfile.ZipFile(zname, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files():
            z.write(p, p.relative_to(ROOT).as_posix())
    sha = hashlib.sha256(zname.read_bytes()).hexdigest()
    (dist / "latest.json").write_text(json.dumps({
        "version": args.version,
        "zip": zname.name,          # same folder as latest.json; or paste a full URL
        "sha256": sha,
        "notes": args.notes,
    }, indent=2), encoding="utf-8")
    print(f"releases/{zname.name}  ({zname.stat().st_size / 1e6:.1f} MB)\n"
          f"releases/latest.json   (sha256 {sha[:12]}...)\n"
          "Now: git add -A && git commit -m \"release " + args.version + "\" && git push")
    return 0


if __name__ == "__main__":
    sys.exit(main())
