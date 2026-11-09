"""Crash safety: Kuro comes back after a crash, and doesn't loop forever."""
import main as M


def run(monkeypatch, tmp_path, codes):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    calls, boxes = [], []

    def fake_call(cmd):
        calls.append(cmd)
        return codes.pop(0)
    monkeypatch.setattr(M.subprocess, "call", fake_call)
    monkeypatch.setattr(M.time, "sleep", lambda s: None)
    monkeypatch.setattr(M, "_message_box", boxes.append)
    return M.supervise(), calls, boxes, (tmp_path / "Kuro" / "crash.log")


def test_restarts_after_a_crash_and_says_so(monkeypatch, tmp_path):
    code, calls, boxes, log = run(monkeypatch, tmp_path, [3, 0])
    assert code == 0 and len(calls) == 2 and not boxes
    assert M.CHILD_FLAG in calls[0] and M.RECOVERED_FLAG not in calls[0]
    assert M.RECOVERED_FLAG in calls[1]                 # "Oops, I tripped... I'm back!"
    assert "exit code 3" in log.read_text()


def test_normal_quit_is_not_a_crash(monkeypatch, tmp_path):
    code, calls, _boxes, log = run(monkeypatch, tmp_path, [0])
    assert code == 0 and len(calls) == 1 and not log.exists()


def test_gives_up_after_repeated_crashes(monkeypatch, tmp_path):
    code, calls, boxes, log = run(monkeypatch, tmp_path, [1, 1, 1, 1, 1, 1])
    assert len(calls) == M.MAX_CRASHES and code == 1
    assert boxes and "crash.log" in boxes[0]
    assert "not restarting" in log.read_text()
