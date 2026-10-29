"""How the bridge finds the cursor theme and samples its arrow colors."""

import os
import unittest
from unittest import mock

from support import (BLACK, TRANSPARENT, WHITE, Session, argb, image, load_bridge, outlined,
                     xcursor)

bridge = load_bridge()


def flat_arrow(size=24, fill=BLACK, outline=WHITE):
    return xcursor([image(size, outlined(size, lambda row, column: fill, outline))])


def luminance(color):
    red, green, blue = (int(color[index:index + 2], 16) for index in (1, 3, 5))
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


class SessionTest(unittest.TestCase):
    def setUp(self):
        self.session = Session()
        self.addCleanup(self.session.cleanup)
        patcher = mock.patch.dict(os.environ, self.session.environment(), clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)


class ThemeNameTest(SessionTest):
    def test_uses_the_xcursor_theme_from_the_environment(self):
        os.environ["XCURSOR_THEME"] = "Bibata"
        self.assertEqual(bridge.theme_name(), "Bibata")

    def test_hyprcursor_theme_wins_over_xcursor_theme(self):
        os.environ["XCURSOR_THEME"] = "Bibata"
        os.environ["HYPRCURSOR_THEME"] = "Bibata-Hypr"
        self.assertEqual(bridge.theme_name(), "Bibata-Hypr")

    def test_uses_the_desktop_setting_without_an_environment_theme(self):
        self.session.set_desktop_theme("Yaru")
        self.assertEqual(bridge.theme_name(), "Yaru")

    def test_falls_back_to_default(self):
        self.assertEqual(bridge.theme_name(), "default")

    def test_falls_back_to_default_without_gsettings(self):
        os.environ["PATH"] = self.session.root  # no stubs, no real gsettings
        self.assertEqual(bridge.theme_name(), "default")

    # Known issue: the environment is fixed when the shell starts, so a theme
    # applied later (Omarchy's cursor plugins run `hyprctl setcursor` and
    # update the desktop setting) is never seen while the variable is set.
    def test_theme_changed_after_startup_wins_over_the_environment(self):
        os.environ["XCURSOR_THEME"] = "Bibata"
        self.session.set_desktop_theme("Yaru")
        self.assertEqual(bridge.theme_name(), "Yaru")


class CursorFileTest(SessionTest):
    def test_finds_left_ptr(self):
        directory = self.session.add_theme("Plain", {"left_ptr": flat_arrow()})
        self.assertEqual(bridge.cursor_file("Plain"), os.path.join(directory, "cursors", "left_ptr"))

    def test_prefers_left_ptr_over_other_arrow_names(self):
        directory = self.session.add_theme("Plain", {"arrow": flat_arrow(), "left_ptr": flat_arrow()})
        self.assertEqual(bridge.cursor_file("Plain"), os.path.join(directory, "cursors", "left_ptr"))

    def test_falls_back_to_other_arrow_names(self):
        directory = self.session.add_theme("Yaru-like", {"arrow": flat_arrow()})
        self.assertEqual(bridge.cursor_file("Yaru-like"), os.path.join(directory, "cursors", "arrow"))

    def test_resolves_symlinked_cursors(self):
        directory = self.session.add_theme("Linked", {"default": flat_arrow(), "left_ptr": "default"})
        self.assertEqual(bridge.cursor_file("Linked"), os.path.join(directory, "cursors", "default"))

    def test_follows_inherits(self):
        directory = self.session.add_theme("Base", {"left_ptr": flat_arrow()})
        self.session.add_theme("default", inherits="Missing, Base")
        self.assertEqual(bridge.cursor_file("default"), os.path.join(directory, "cursors", "left_ptr"))

    def test_stops_on_an_inheritance_cycle(self):
        self.session.add_theme("A", inherits="B")
        self.session.add_theme("B", inherits="A")
        self.assertIsNone(bridge.cursor_file("A"))

    def test_user_icons_win_over_system_icons(self):
        self.session.add_theme("Plain", {"left_ptr": flat_arrow()})
        user = self.session.add_theme("Plain", {"left_ptr": flat_arrow()},
                                      base=os.path.join(self.session.data_home, "icons"))
        self.assertEqual(bridge.cursor_file("Plain"), os.path.join(user, "cursors", "left_ptr"))

    def test_reads_legacy_home_icons(self):
        directory = self.session.add_theme("Plain", {"left_ptr": flat_arrow()},
                                           base=os.path.join(self.session.home, ".icons"))
        self.assertEqual(bridge.cursor_file("Plain"), os.path.join(directory, "cursors", "left_ptr"))

    def test_unknown_theme(self):
        self.assertIsNone(bridge.cursor_file("Nope"))


class CursorColorsTest(SessionTest):
    def use_theme(self, content):
        self.session.add_theme("Test", {"left_ptr": content})
        os.environ["XCURSOR_THEME"] = "Test"

    def test_black_arrow_with_white_outline(self):
        self.use_theme(flat_arrow())
        self.assertEqual(bridge.cursor_colors(), ("#000000", "#ffffff"))

    def test_recolored_arrow(self):
        self.use_theme(flat_arrow(fill=argb(255, 0x0c, 0x0b, 0x0c), outline=argb(255, 0xb5, 0x97, 0x90)))
        self.assertEqual(bridge.cursor_colors(), ("#0c0b0c", "#b59790"))

    def test_ignores_the_translucent_shadow(self):
        shadow = argb(80, 0, 0, 0)
        pixels = outlined(24, lambda row, column: BLACK) + [shadow] * 24 * 30
        self.use_theme(xcursor([{"nominal": 24, "width": 24, "height": 54, "hotspot": (1, 1),
                                 "pixels": pixels}]))
        self.assertEqual(bridge.cursor_colors(), ("#000000", "#ffffff"))

    def test_samples_the_largest_image(self):
        small = image(12, outlined(12, lambda row, column: WHITE, BLACK))
        large = image(48, outlined(48, lambda row, column: BLACK, WHITE))
        self.use_theme(xcursor([small, large]))
        self.assertEqual(bridge.cursor_colors(), ("#000000", "#ffffff"))

    def test_no_theme_keeps_the_defaults(self):
        os.environ["XCURSOR_THEME"] = "Nope"
        self.assertIsNone(bridge.cursor_colors())

    def test_fully_transparent_image_keeps_the_defaults(self):
        self.use_theme(xcursor([image(8, [TRANSPARENT] * 64)]))
        self.assertIsNone(bridge.cursor_colors())

    def test_file_that_is_not_an_xcursor(self):
        self.use_theme(b"\x89PNG\r\n\x1a\n" + bytes(64))
        self.assertIsNone(bridge.cursor_colors())

    def test_truncated_xcursor(self):
        self.use_theme(flat_arrow()[:100])
        self.assertIsNone(bridge.cursor_colors())

    # Known issue: the fill is taken as the single most common color. A
    # gradient fill (Yaru) spreads over many shades, so its flat white outline
    # wins and the enlarged arrow comes out inverted.
    def test_gradient_fill_stays_the_fill(self):
        def gradient(row, column):
            shade = 0x20 + row * 3
            return argb(255, shade, shade, shade)
        self.use_theme(xcursor([image(20, outlined(20, gradient))]))
        fill, stroke = bridge.cursor_colors()
        self.assertLess(luminance(fill), 128, "the dark gradient fill was reported as " + fill)
        self.assertGreater(luminance(stroke), 128, "the white outline was reported as " + stroke)

    # Known issue: with a single opaque color the outline gets the fill's
    # color and disappears against it.
    def test_single_color_arrow_keeps_a_visible_outline(self):
        self.use_theme(xcursor([image(16, [BLACK] * 256)]))
        fill, stroke = bridge.cursor_colors()
        self.assertNotEqual(fill, stroke)


if __name__ == "__main__":
    unittest.main()
