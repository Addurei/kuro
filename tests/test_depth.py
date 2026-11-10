"""Kuro's shading: the cached skull must look the same as painting it live,
and the cache must actually be used while he just sits and breathes."""
import pytest
from PySide6.QtCore import QPointF
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication

from app.ui import cat_rig as R
from app.ui import theme


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def render(pose, scale=0.82, size=(205, 156)):
    img = QImage(*size, QImage.Format_ARGB32_Premultiplied)
    img.fill(0)
    p = QPainter(img)
    geo = {}
    R.draw_cat(p, pose, QPointF(size[0] / 2, size[1] - 6), scale, geo)
    p.end()
    return img, geo


def diff(a: QImage, b: QImage) -> float:
    """Mean absolute difference per channel, 0..255."""
    a = a.convertToFormat(QImage.Format_RGBA8888)
    b = b.convertToFormat(QImage.Format_RGBA8888)
    x, y = bytes(a.constBits()), bytes(b.constBits())
    return sum(abs(i - j) for i, j in zip(x, y)) / len(x)


def test_cached_skull_matches_painting_it_live(qapp, monkeypatch):
    pose = R.Pose(t=1.0, yaw=12, head_tilt=4, breath=0.4)
    R._HEAD_CACHE.clear()
    cached, _ = render(pose)
    assert len(R._HEAD_CACHE) == 1
    monkeypatch.setattr(R, "HEAD_CACHE_PX", 0)            # too big to cache -> painted live
    live, _ = render(pose)
    assert diff(cached, live) < 1.2                       # only resampling softness apart


def test_skull_is_reused_while_he_only_breathes_and_tilts(qapp):
    R._HEAD_CACHE.clear()
    for i in range(30):
        render(R.Pose(t=i / 30, yaw=12, breath=(i % 10) / 10, head_tilt=i * 0.2,
                      twitch_l=i * 0.3, ear=0.1))
    assert len(R._HEAD_CACHE) == 1                        # bobbing, tilting, ears: same skull
    render(R.Pose(t=1, yaw=30))
    render(R.Pose(t=1, yaw=12, eye_open=0.0))
    assert len(R._HEAD_CACHE) == 3                        # turning or blinking repaints it


def test_cache_stays_small(qapp):
    R._HEAD_CACHE.clear()
    for i in range(40):
        render(R.Pose(t=1, yaw=-40 + i * 2))
    assert len(R._HEAD_CACHE) <= R.HEAD_CACHE_MAX


def test_he_is_shaded_not_flat(qapp):
    """Lit from the upper left: that side of his head is clearly brighter than
    the lower right, and his eyes are darker at the top than at the bottom."""
    img, geo = render(R.Pose(t=1.0, yaw=0), scale=2.0, size=(420, 330))
    c, r = geo["head"], geo["head_r"]

    def lum(dx, dy):
        col = img.pixelColor(int(c.x() + dx * r), int(c.y() + dy * r))
        return 0.3 * col.red() + 0.6 * col.green() + 0.1 * col.blue()
    assert lum(-0.45, -0.62) > lum(0.5, 0.75) + 12
    # iris: beside the pupil near the top, and under it in the pool of light
    ex, ey, _ = R.on_head(R.EYE_PHI, R.EYE_LAM, 0.0)
    k = R.HEAD_SCALE * 2.0
    top = img.pixelColor(int(c.x() + (ex + 12.3) * k), int(c.y() + (ey - 7) * k))
    low = img.pixelColor(int(c.x() + (ex - 2) * k), int(c.y() + (ey + 13) * k))
    assert low.green() > top.green() + 25                 # shade under the brow
    assert low.green() > 170 and low.green() > low.blue() + 60   # ...and still his lime


def test_drawing_him_never_paints_outside_his_own_pixels(qapp):
    """Shadows and highlights only land on him: the corners stay transparent."""
    img, _ = render(R.Pose(t=1.0, yaw=12))
    for x, y in ((2, 2), (202, 2), (2, 80), (202, 80)):
        assert img.pixelColor(x, y).alpha() == 0


def test_the_moving_line_is_gone():
    assert not hasattr(theme, "paint_comet")
