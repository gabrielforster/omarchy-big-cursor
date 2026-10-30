"""How the bridge finds the cursor theme and reads its arrow images."""

import os
import unittest
from unittest import mock

from support import BLACK, WHITE, Session, image, load_bridge, outlined, xcursor

bridge = load_bridge()


def flat_arrow(size=24, fill=BLACK, outline=WHITE):
    return xcursor([image(size, outlined(size, lambda row, column: fill, outline))])


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

    # The environment is fixed when the shell starts, so a theme applied later
    # (Omarchy's cursor plugins run `hyprctl setcursor` and update the desktop
    # setting) has to win over it.
    def test_theme_changed_after_startup_wins_over_the_environment(self):
        os.environ["XCURSOR_THEME"] = "Bibata"
        self.session.set_desktop_theme("Yaru")
        self.assertEqual(bridge.theme_name(), "Yaru")

    def test_uses_gsettings_without_dconf(self):
        self.session.remove_stub("dconf")
        self.session.set_desktop_theme("Yaru")
        self.assertEqual(bridge.theme_name(), "Yaru")

    # Known issue: without dconf, gsettings has no storage and reports the
    # schema default ('Adwaita') whether or not a theme was set, which hides
    # the theme the session environment names.
    def test_without_dconf_the_environment_wins_over_gsettings(self):
        self.session.remove_stub("dconf")
        self.session.set_desktop_theme("Adwaita")
        os.environ["XCURSOR_THEME"] = "Bibata"
        self.assertEqual(bridge.theme_name(), "Bibata")


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


class ArrowImagesTest(SessionTest):
    def use_theme(self, content):
        self.session.add_theme("Test", {"left_ptr": content})
        os.environ["XCURSOR_THEME"] = "Test"

    def test_reads_every_image_with_its_size_and_hotspot(self):
        self.use_theme(xcursor([image(24, outlined(24, lambda row, column: BLACK), hotspot=(3, 1)),
                                image(64, outlined(64, lambda row, column: BLACK), nominal=48,
                                      hotspot=(8, 2))]))
        images = bridge.theme_arrow()
        self.assertEqual([(entry["nominal"], entry["width"], entry["height"],
                           entry["hotspotX"], entry["hotspotY"]) for entry in images],
                         [(24, 24, 24, 3, 1), (48, 64, 64, 8, 2)])
        self.assertEqual(images[1]["pixels"][64 * 32 + 32], BLACK)

    def test_no_theme(self):
        os.environ["XCURSOR_THEME"] = "Nope"
        self.assertIsNone(bridge.theme_arrow())

    def test_file_that_is_not_an_xcursor(self):
        self.use_theme(b"\x89PNG\r\n\x1a\n" + bytes(64))
        self.assertFalse(bridge.theme_arrow())

    def test_truncated_xcursor(self):
        self.use_theme(flat_arrow()[:100])
        self.assertIsNone(bridge.theme_arrow())


if __name__ == "__main__":
    unittest.main()
