"""The theme's arrow exported as a PNG for the overlay to scale (#2).

Skipped until the bridge can export the arrow.
"""

import os
import unittest

from support import (BLACK, WHITE, Bridge, FakeHyprland, Session, image, load_bridge,
                     outlined, premultiplied, read_png, xcursor)

EXPORTS_ARROW = hasattr(load_bridge(), "arrow_sprite")


def flat(size, fill=BLACK, nominal=None, hotspot=(1, 1)):
    return image(size, outlined(size, lambda row, column: fill), nominal=nominal, hotspot=hotspot)


@unittest.skipUnless(EXPORTS_ARROW, "the bridge does not export the theme's arrow yet")
class ArrowExportTest(unittest.TestCase):
    def setUp(self):
        self.session = Session()
        self.addCleanup(self.session.cleanup)
        self.addCleanup(FakeHyprland(self.session).close)

    def start(self, **env):
        bridge = Bridge(self.session, **env)
        self.addCleanup(bridge.close)
        return bridge

    def start_with(self, *images):
        self.session.add_theme("Test", {"left_ptr": xcursor(list(images))})
        return self.start(XCURSOR_THEME="Test")

    def test_exports_the_arrow_at_startup(self):
        bridge = self.start_with(flat(24))
        _, path, *_ = bridge.wait_for("arrow")
        width, height, pixels = read_png(path)
        self.assertEqual((width, height), (24, 24))
        self.assertEqual(pixels[0], (255, 255, 255, 255))
        self.assertEqual(pixels[24 * 12 + 12], (0, 0, 0, 255))

    def test_exports_into_the_runtime_directory(self):
        bridge = self.start_with(flat(24))
        _, path, *_ = bridge.wait_for("arrow")
        self.assertEqual(os.path.dirname(path), self.session.arrow_dir)

    def test_exports_the_largest_image_with_its_hotspot(self):
        bridge = self.start_with(flat(24, hotspot=(3, 1)), flat(96, hotspot=(12, 4)), flat(48, hotspot=(6, 2)))
        _, path, hotspot_x, hotspot_y, size = bridge.wait_for("arrow")
        self.assertEqual((hotspot_x, hotspot_y, size), ("12", "4", "96"))
        self.assertEqual(read_png(path)[:2], (96, 96))

    def test_reexports_when_the_theme_changes(self):
        self.session.add_theme("Dark", {"left_ptr": xcursor([flat(24, BLACK)])})
        self.session.add_theme("Light", {"left_ptr": xcursor([flat(24, WHITE)])})
        self.session.set_desktop_theme("Dark")
        bridge = self.start()
        first = bridge.wait_for("arrow")[1]
        self.session.set_desktop_theme("Light")
        bridge.send("colors")
        second = bridge.wait_for("arrow")[1]
        self.assertNotEqual(first, second, "QML caches images by URL, so a new theme needs a new path")
        self.assertEqual(read_png(second)[2][24 * 12 + 12], (255, 255, 255, 255))
        self.assertEqual(os.listdir(self.session.arrow_dir), [os.path.basename(second)])

    def test_no_xcursor_theme_keeps_the_drawn_arrow(self):
        bridge = self.start(XCURSOR_THEME="HyprcursorOnly")
        self.assertIsNone(bridge.wait_for("arrow", timeout=0.5))

    # Known issue: Xcursor pixels are premultiplied ARGB and PNG alpha is
    # straight, so copying them across darkens every translucent pixel; the
    # anti-aliased edge of a white outline turns grey.
    def test_translucent_pixels_keep_their_color(self):
        pixels = [premultiplied(128, 255, 255, 255)] * 16
        bridge = self.start_with(image(4, pixels))
        _, path, *_ = bridge.wait_for("arrow")
        red, green, blue, alpha = read_png(path)[2][0]
        self.assertEqual(alpha, 128)
        self.assertGreaterEqual(min(red, green, blue), 250, "half-transparent white came out as grey")

    # Known issue: the size reported is the image width. An Xcursor image's
    # nominal size can differ from its pixel size, and the cursor size the
    # service scales to is a nominal size.
    def test_reports_the_nominal_size(self):
        bridge = self.start_with(flat(64, nominal=48))
        self.assertEqual(bridge.wait_for("arrow")[4], "48")

    # Known issue: every shake start writes a new file even when the theme has
    # not changed, so QML reloads the image for nothing.
    def test_unchanged_theme_reuses_the_export(self):
        bridge = self.start_with(flat(24))
        first = bridge.wait_for("arrow")[1]
        bridge.send("colors")
        self.assertEqual(bridge.wait_for("arrow")[1], first)

    # Known issue: the export outlives the bridge; nothing removes it when the
    # shell exits or the plugin is disabled.
    def test_removes_the_export_on_exit(self):
        bridge = self.start_with(flat(24))
        bridge.wait_for("arrow")
        self.assertEqual(bridge.close(), 0)
        leftovers = os.listdir(self.session.arrow_dir) if os.path.isdir(self.session.arrow_dir) else []
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
