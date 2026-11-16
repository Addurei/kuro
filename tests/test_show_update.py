"""The Show panel (wheel, coin, dice, charts) and the self-updater."""
import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import updater as U
from app.ui.showcase import spin_spec


# ---- what the brain may ask the panel to show -----------------------------
def test_wheel_spec_picks_a_real_option():
    sp = spin_spec("wheel", ["a", "b", "c"])
    assert sp["kind"] == "wheel" and sp["options"][sp["result"]] == sp["text"]
    with pytest.raises(ValueError):
        spin_spec("wheel", ["only one"])


def test_coin_and_dice_land_inside_their_range():
    assert spin_spec("coin")["result"] in ("Heads", "Tails")
    sp = spin_spec("dice", sides=20)
    assert 1 <= sp["result"] <= 20 and sp["sides"] == 20
    assert spin_spec("dice", sides=9999)["sides"] == 100


def test_chart_spec_checks_its_numbers():
    sp = spin_spec("chart", labels=["a", "b"], values=[1, 2], style="line")
    assert sp["style"] == "line" and sp["values"] == [1.0, 2.0]
    assert spin_spec("chart", labels=["a"], values=[-1], style="pie")["style"] == "bar"
    with pytest.raises(ValueError):
        spin_spec("chart", labels=["a"], values=[1, 2])
    with pytest.raises(ValueError):
        spin_spec("chart", labels=["a"], values=["x"])
    with pytest.raises(ValueError):
        spin_spec("juggling")


def test_the_panel_animates_and_lands(qapp):
    from app.ui.showcase import ShowPage
    pg = ShowPage()
    pg.resize(330, 390)
    sp = spin_spec("wheel", ["x", "y", "z"])
    pg.load(sp)
    pg.anim.stop()
    pg.k = 1.0
    pg._land()
    assert pg.landed and pg.result.text() == sp["text"] and pg._sparks
    pg._spark_timer.stop()
    before = sp["result"]
    pg.rerun()                                   # free replays stay inside the options
    assert 0 <= pg.spec["result"] < 3
    del before


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


# ---- updater: versions, staging, applying ---------------------------------
def test_version_compare():
    assert U.is_newer("1.0.1", "1.0.0") and U.is_newer("2.0", "1.9.9")
    assert not U.is_newer("1.0.0", "1.0.0") and not U.is_newer("0.9", "1.0")
    assert U.is_newer("v1.2", "1.1.5")


def test_only_kuros_own_paths_may_be_replaced():
    assert U.safe_member("app/brain.py") and U.safe_member("main.py")
    assert not U.safe_member("config.json")          # settings are never touched
    assert not U.safe_member("../evil.py")
    assert not U.safe_member("/etc/passwd")
    assert not U.safe_member("C:/Windows/notepad.exe")
    assert not U.safe_member("tests/x.py")


def make_zip(tmp_path, version="1.1.0", extra=()):
    z = tmp_path / "rel.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("main.py", f"# kuro {version}")
        zf.writestr("app/brain.py", "new = True")
        zf.writestr("app/version.py", f'VERSION = "{version}"')
        for name, data in extra:
            zf.writestr(name, data)
    return z


def test_stage_then_apply_swaps_the_code_and_keeps_config(tmp_path):
    data, appdir = tmp_path / "data", tmp_path / "install"
    (appdir / "app").mkdir(parents=True)
    (appdir / "main.py").write_text("# kuro 1.0.0")
    (appdir / "app" / "brain.py").write_text("new = False")
    (appdir / "config.json").write_text("{\"user\": \"drey\"}")
    z = make_zip(tmp_path, extra=[("config.json", "{}"), ("../oops.py", "x")])
    U.stage_zip(z, U.update_dir(data), "1.1.0")
    assert U.apply_pending(appdir, data) == "1.1.0"
    assert (appdir / "main.py").read_text() == "# kuro 1.1.0"
    assert (appdir / "app" / "brain.py").read_text() == "new = True"
    assert (appdir / "config.json").read_text() == "{\"user\": \"drey\"}"   # untouched
    assert not (appdir / "oops.py").exists()
    assert U.apply_pending(appdir, data) == ""        # nothing pending now


def test_a_zip_that_is_not_kuro_is_refused(tmp_path):
    z = tmp_path / "bad.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("readme.txt", "hi")
    with pytest.raises(ValueError):
        U.stage_zip(z, U.update_dir(tmp_path / "d"), "9.9.9")


def test_checker_stages_only_newer_versions(tmp_path, monkeypatch):
    calls = []
    info = {"version": "0.0.1", "zip": "http://x/kuro.zip"}
    monkeypatch.setattr(U, "fetch_latest", lambda url, timeout=15.0: dict(info))
    monkeypatch.setattr(U, "download_and_stage",
                        lambda i, d, timeout=120.0: calls.append(i["version"]))
    s = SimpleNamespace(update_url="http://x/latest.json", auto_update=True)
    up = U.Updater(s, tmp_path, on_ready=lambda v: calls.append("ready " + v))
    assert up.check_now() == "" and calls == []       # older than us: ignored
    info["version"] = "99.0.0"
    assert up.check_now() == "99.0.0"
    assert calls == ["99.0.0", "ready 99.0.0"]
    assert not up.due()                               # checked just now


def test_updater_respects_the_off_switch(tmp_path):
    ran = []
    up = U.Updater(SimpleNamespace(update_url="", auto_update=True), tmp_path)
    up.check_now = lambda: ran.append(1)              # type: ignore[method-assign]
    up.start(delay=0)
    assert up._thread is None and not ran


def test_release_zip_round_trips_through_the_updater(tmp_path):
    """make_release's zip must be exactly what stage_zip accepts.

    It runs against the real repo, so it snapshots version.py and releases/
    first and puts back exactly those bytes, whatever version is current."""
    import shutil, subprocess, sys
    root = Path(__file__).parent.parent
    vp = root / "app" / "version.py"
    saved_version = vp.read_bytes()
    saved_releases = tmp_path / "releases-backup"
    if (root / "releases").exists():
        shutil.copytree(root / "releases", saved_releases)
    try:
        _round_trip(tmp_path, subprocess, sys)
    finally:
        vp.write_bytes(saved_version)
        shutil.rmtree(root / "releases", ignore_errors=True)
        if saved_releases.exists():
            shutil.copytree(saved_releases, root / "releases")


def _round_trip(tmp_path, subprocess, sys):
    r = subprocess.run([sys.executable, "scripts/make_release.py", "1.0.1", "--notes", "t"],
                       capture_output=True, text=True, cwd=Path(__file__).parent.parent)
    assert r.returncode == 0, r.stdout + r.stderr
    dist = Path(__file__).parent.parent / "releases"
    info = json.loads((dist / "latest.json").read_text())
    assert info["version"] == "1.0.1" and info["sha256"]
    pending = U.stage_zip(dist / info["zip"], U.update_dir(tmp_path), info["version"])
    assert (pending / "main.py").is_file() and (pending / "app" / "updater.py").is_file()
    assert not list(pending.rglob("__pycache__"))
