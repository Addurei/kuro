"""Make a Kuro release: a zip of the code plus the latest.json the updater reads.

    python scripts/make_release.py 1.1.0 [--notes "What changed"]

Writes dist/kuro-1.1.0.zip and dist/latest.json, and stamps app/version.py.
Upload BOTH files somewhere public (e.g. attach them to a GitHub release and
point settings.update_url at latest.json's download URL). Every running Kuro
with that update_url picks it up within a day and applies it on its next start.
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
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
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
    print(f"dist/{zname.name}  ({zname.stat().st_size / 1e6:.1f} MB)\n"
          f"dist/latest.json   (sha256 {sha[:12]}...)\n"
          "Upload both to the same place; update_url points at latest.json.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
