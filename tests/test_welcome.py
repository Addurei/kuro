"""The first-run welcome: bundled type, character-aware copy, sane flow."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

QtWidgets = pytest.importorskip("PySide6.QtWidgets")
app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from app.config import Settings  # noqa: E402
from app.ui import fonts  # noqa: E402
from app.ui.welcome import WelcomePage  # noqa: E402


def test_bundled_fonts_load_as_one_family_with_real_weights():
    from PySide6.QtGui import QFontDatabase
    fams = fonts.load()
    assert "Geist" in fams and "Geist Mono" in fams
    styles = set(QFontDatabase.styles("Geist"))
    assert {"Regular", "Medium", "SemiBold", "Bold"} <= styles


def _page(**kw):
    s = Settings()
    for k, v in kw.items():
        setattr(s, k, v)
    s.save = lambda: None
    return s, WelcomePage(s, lambda *a: "model")


def test_introduces_the_current_character_not_a_hardcoded_one():
    s, w = _page(cat_name="Mochi")
    hello = w.steps.widget(0)
    assert hello.title.text() == "Hi, I'm Mochi."
    assert "Kuro" not in hello.title.text()


def test_flow_saves_name_and_finishes():
    s, w = _page(user_name="friend")
    steps = []
    w.step_changed.connect(steps.append)
    done = []
    w.finished.connect(lambda: done.append(True))
    w.go(1)
    w.name_edit.setText("  Drey ")
    w._save_name()
    assert s.user_name == "Drey" and w.steps.currentIndex() == 2
    w._test()                                  # empty key: stays, shows an error
    assert w.steps.currentIndex() == 2 and w.key_field.property("error") == "true"
    w.go(3)
    assert "Drey" in w.steps.widget(3).title.text()
    w._finish()
    assert s.onboarded and done and steps[-1] == 3


def test_each_step_asks_for_a_height_that_fits():
    _, w = _page()
    heights = [w.wanted_height(i) for i in range(4)]
    assert all(250 <= h <= 600 for h in heights)
    assert heights[1] < heights[0]             # the name step is short: no dead space
