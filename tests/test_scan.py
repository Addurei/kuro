"""Kuro's PC scan reads names only and turns them into memory facts."""

from app import scan
from app.memory import MemoryStore


def test_installed_apps_skip_noise(tmp_path):
    sm = tmp_path / "Programs"
    (sm / "Blender Foundation").mkdir(parents=True)
    for n in ("Blender Foundation/Blender 4.2.lnk", "Spotify.lnk", "Uninstall Spotify.lnk",
              "Unity Hub.lnk", "Python 3.12 Manuals.lnk"):
        (sm / n).write_text("")
    assert scan.installed_apps([sm]) == ["Blender 4.2", "Spotify", "Unity Hub"]


def test_projects_are_classified_by_names(tmp_path):
    docs = tmp_path / "Documents"
    (docs / "Kuro" / "app").mkdir(parents=True)
    (docs / "Kuro" / "main.py").write_text("print(1)")
    (docs / "Kuro" / "requirements.txt").write_text("")
    (docs / "CampusMap" / "Assets").mkdir(parents=True)
    (docs / "CampusMap" / "ProjectSettings").mkdir()
    (docs / "Renders").mkdir()
    (docs / "Renders" / "school.blend").write_text("")
    (docs / "Empty").mkdir()
    (docs / "Kuro" / "node_modules" / "x").mkdir(parents=True)
    found = {p["name"]: p["kinds"] for p in scan.projects([docs])}
    assert found["Kuro"] == ["Python"]
    assert "Unity" in found["CampusMap"]
    assert found["Renders"] == ["Blender"]
    assert "Empty" not in found


def test_summary_and_save_roundtrip(tmp_path):
    data = {"scanned": "2026-11-07 10:00", "system": {"os": "Windows-11", "cpu": "Ryzen 5",
                                                      "ram_gb": 16},
            "apps": ["Blender", "Spotify"], "projects": [
                {"name": "Kuro", "where": "Documents", "kinds": ["Python"], "modified": "2026-11-07"}],
            "recent": ["thesis.docx"]}
    text = scan.summary(data)
    assert "Ryzen 5" in text and "Kuro (Python" in text and "Blender" in text and "thesis.docx" in text
    scan.save(data, tmp_path / "scan.json")
    assert scan.load(tmp_path / "scan.json") == data
    assert scan.load(tmp_path / "missing.json") is None


def test_facts_from_scan_go_to_memory(tmp_path):
    seen = {}

    def complete(prompt):
        seen["prompt"] = prompt
        return 'Sure! [{"text": "Drey makes 3D models in Blender.", "category": "profile"},' \
               ' {"nope": 1}]'
    facts = scan.facts_from_scan(complete, "Apps: Blender", "Kuro", "Drey")
    assert facts == [{"text": "Drey makes 3D models in Blender.", "category": "profile"}]
    assert "no contents" in seen["prompt"]
    assert scan.facts_from_scan(lambda p: "no json here", "x", "Kuro", "Drey") == []
    mem = MemoryStore(tmp_path / "m.json")
    mem.add(facts[0]["text"], facts[0]["category"], source="scan")
    assert "Blender" in mem.prompt_block()


def test_scan_tool_registered(tmp_path):
    from app import tools as T
    from app.reminders import ReminderStore
    fx = []
    tools = T.build_tools(ReminderStore(tmp_path / "r.json"), [], scan=lambda: "Scan done.",
                          scan_fx=fx.append)
    out, err = T.run_tool(tools["scan"], {"what": "pc"})
    assert not err and out.endswith("Scan done.") and fx == [True, False]


def test_run_scan_smoke(monkeypatch, tmp_path):
    monkeypatch.setattr(scan, "project_roots", lambda: [tmp_path])
    monkeypatch.setattr(scan, "start_menu_dirs", lambda: [])
    msgs = []
    r = scan.run_scan(msgs.append)
    assert r["system"]["os"] and "apps" in r and len(msgs) >= 3


def test_file_census_counts_by_kind(tmp_path):
    (tmp_path / "a").mkdir()
    for n in ("school.blend", "map.fbx", "notes.docx", "x.py", "y.py", "pic.png", "skip.xyz"):
        (tmp_path / "a" / n).write_bytes(b"0" * 10)
    (tmp_path / "a" / "node_modules").mkdir()
    (tmp_path / "a" / "node_modules" / "z.js").write_text("")
    c = scan.file_census([tmp_path])
    assert c["3D"]["files"] == 2 and c["code"]["files"] == 2 and c["documents"]["files"] == 1
    assert "videos" not in c


def test_dev_tools_and_games(tmp_path, monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, "which", lambda exe: "/x" if exe in ("git", "python", "blender") else None)
    assert scan.dev_tools() == ["Blender", "Git", "Python"]
    common = tmp_path / "Steam" / "steamapps" / "common"
    (common / "Stardew Valley").mkdir(parents=True)
    (common / "Steamworks Shared").mkdir()
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path))
    assert "Stardew Valley" in scan.games() and "Steamworks Shared" not in scan.games()


def test_changes_since_last_scan():
    old = {"apps": ["Blender"], "projects": [{"name": "Kuro"}], "games": []}
    new = {"apps": ["Blender", "Figma"], "projects": [{"name": "Kuro"}, {"name": "Thesis"}],
           "games": ["Valorant"]}
    assert scan.changes(old, new) == {"apps": ["Figma"], "games": ["Valorant"],
                                      "projects": ["Thesis"]}
    assert scan.changes(None, new) == {}
    new["projects"] = [{"name": n, "kinds": ["Python"], "modified": "2026-11-08"} for n in ("Kuro", "Thesis")]
    s = scan.summary({**new, "changes": scan.changes(old, new), "tools": ["Git"],
                      "files": {"3D": {"files": 12, "mb": 300}}})
    assert "New since last scan" in s and "Dev tools: Git" in s and "12 3D" in s
