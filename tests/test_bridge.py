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
        bridge.send("colors")
        self.assertIsNone(bridge.wait_for("arrow", timeout=0.5))
        # Still serving after the empty reply.
        bridge.send("cursor hide")
        self.assertTrue(self.wait_for_request(HIDE))


if __name__ == "__main__":
    unittest.main()
