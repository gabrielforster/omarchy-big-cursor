"""cursor-bridge.py as the service runs it: stdin commands, stdout lines, and
the requests it makes to Hyprland."""

import time
import unittest

from support import Bridge, FakeHyprland, Session

HIDE = "/eval hl.config({ cursor = { invisible = true } })"
SHOW = "/eval hl.config({ cursor = { invisible = false } })"


class BridgeTest(unittest.TestCase):
    def setUp(self):
        self.session = Session()
        self.addCleanup(self.session.cleanup)
        self.hyprland = FakeHyprland(self.session)
        self.addCleanup(self.hyprland.close)

    def start(self, **env):
        bridge = Bridge(self.session, **env)
        self.addCleanup(bridge.close)
        return bridge

    def wait_for_request(self, request, timeout=2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if request in self.hyprland.requests:
                return True
            time.sleep(0.02)
        return False


class PositionTest(BridgeTest):
    def test_reports_the_pointer_position(self):
        bridge = self.start()
        self.assertEqual(bridge.wait_for("100 "), ["100", "200"])

    def test_reports_a_position_only_when_it_changes(self):
        bridge = self.start()
        bridge.wait_for("100 ")
        self.assertIsNone(bridge.wait_for("100 ", timeout=0.3))
        self.hyprland.position = (101, 200)
        self.assertEqual(bridge.wait_for("101 "), ["101", "200"])


class CursorVisibilityTest(BridgeTest):
    def test_hides_and_shows_the_real_cursor(self):
        bridge = self.start()
        bridge.send("cursor hide")
        self.assertTrue(self.wait_for_request(HIDE))
        bridge.send("cursor show")
        self.assertTrue(self.wait_for_request(SHOW))

    def test_runs_commands_in_order(self):
        bridge = self.start()
        bridge.send("cursor hide", "cursor show")
        self.assertTrue(self.wait_for_request(SHOW))
        self.assertEqual(self.hyprland.requests, [HIDE, SHOW])

    def test_ignores_unknown_commands(self):
        bridge = self.start()
        bridge.send("cursor explode", "cursor hide")
        self.assertTrue(self.wait_for_request(HIDE))
        self.assertEqual(self.hyprland.requests, [HIDE])

    def test_shows_the_cursor_again_when_the_shell_goes_away(self):
        bridge = self.start()
        bridge.send("cursor hide")
        self.assertTrue(self.wait_for_request(HIDE))
        self.assertEqual(bridge.close(), 0)
        self.assertEqual(self.hyprland.requests[-1], SHOW)

    def test_leaves_a_visible_cursor_alone_on_exit(self):
        bridge = self.start()
        bridge.wait_for("100 ")
        self.assertEqual(bridge.close(), 0)
        self.assertEqual(self.hyprland.requests, [])


class ThemeRequestTest(BridgeTest):
    def test_stays_silent_without_a_theme(self):
        bridge = self.start(XCURSOR_THEME="Nope")
        bridge.send("theme")
        self.assertIsNone(bridge.wait_for("arrow", timeout=0.5))
        # Still serving after the empty reply.
        bridge.send("cursor hide")
        self.assertTrue(self.wait_for_request(HIDE))


class CursorSizeTest(BridgeTest):
    def test_sets_the_size_in_hyprland_with_the_current_theme(self):
        self.session.set_desktop_theme("Bibata")
        bridge = self.start()
        bridge.send("size 48")
        self.assertTrue(self.wait_for_request("/setcursor Bibata 48"))

    def test_default_goes_back_to_the_session_size(self):
        self.session.set_desktop_theme("Bibata")
        bridge = self.start(XCURSOR_SIZE="32")
        bridge.send("size 48", "size default")
        self.assertTrue(self.wait_for_request("/setcursor Bibata 32"))

    def test_default_without_a_session_size_is_24(self):
        self.session.set_desktop_theme("Bibata")
        bridge = self.start()
        bridge.send("size 48", "size default")
        self.assertTrue(self.wait_for_request("/setcursor Bibata 24"))

    # The shell recreates the service, and so the bridge, on every settings
    # change, so the bridge that hears "default" is rarely the one that set
    # the size.
    def test_default_after_a_bridge_restart(self):
        self.session.set_desktop_theme("Bibata")
        first = self.start()
        first.send("size 48")
        self.assertTrue(self.wait_for_request("/setcursor Bibata 48"))
        first.close()
        self.start().send("size default")
        self.assertTrue(self.wait_for_request("/setcursor Bibata 24"))

    # Every new service sends "default" when no size is set; a size someone
    # else gave Hyprland must survive that.
    def test_default_leaves_a_size_it_never_set_alone(self):
        bridge = self.start()
        bridge.send("size default", "cursor hide")
        self.assertTrue(self.wait_for_request(HIDE))
        self.assertEqual(self.hyprland.requests, [HIDE])

    def test_default_only_restores_once(self):
        bridge = self.start()
        bridge.send("size 48", "size default", "size default", "cursor hide")
        self.assertTrue(self.wait_for_request(HIDE))
        self.assertEqual(self.hyprland.requests, ["/setcursor default 48", "/setcursor default 24", HIDE])

    def test_ignores_sizes_out_of_range(self):
        bridge = self.start()
        bridge.send("size 2", "size 9000", "size big", "cursor hide")
        self.assertTrue(self.wait_for_request(HIDE))
        self.assertEqual(self.hyprland.requests, [HIDE])


if __name__ == "__main__":
    unittest.main()
