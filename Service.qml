import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland

// Shake-to-find cursor. cursor-bridge.py streams pointer positions from
// Hyprland; this service detects a shake from how much path the pointer covers
// inside a small area. While the shake lasts the cursor grows, then shrinks
// back.
//
// Hyprland cannot say which cursor shape it is showing, so the only shape this
// can reproduce exactly is the arrow: the bridge exports it from the cursor
// theme Hyprland loaded, and the overlay scales that bitmap instead of drawing
// its own arrow. Themes with no Xcursor file (hyprcursor-only) keep the drawn
// arrow as a fallback.
Item {
  id: root

  readonly property string pluginId: "io.github.gabrielforster.big-cursor"
  readonly property string shellConfigPath: Quickshell.env("HOME") + "/.config/omarchy/shell.json"
  readonly property string bridgePath: decodeURIComponent(
    String(Qt.resolvedUrl("cursor-bridge.py")).replace(/^file:\/\//, ""))

  // Settings. Defaults below; overridden by this plugin's entry in
  // ~/.config/omarchy/shell.json (hot-reloaded), which the bar widget edits.
  property int maxSize: 96
  property real sensitivity: 2.5
  property int growMs: 300
  property int holdMs: 500
  property int shrinkMs: 350
  property int baseSize: root.environmentSize()

  // Shake detection works on the last few hundred milliseconds of movement.
  readonly property int shakeWindowMs: 500
  readonly property int minimumShakeSpan: 80
  readonly property int minimumReversals: 2

  property var samples: []
  property real cursorX: 0
  property real cursorY: 0
  property bool cursorReady: false
  property real level: 0
  property bool animating: false
  property double lastShakeMs: 0
  property double previewUntilMs: 0
  property double lastTickMs: 0
  property bool cursorHidden: false

  // The arrow image from the cursor theme Hyprland is using, exported by the
  // bridge, plus its hotspot and nominal size. Empty when the theme has no
  // Xcursor file (hyprcursor-only), which leaves the drawn arrow.
  property string arrowSource: ""
  property int arrowHotspotX: 0
  property int arrowHotspotY: 0
  property real arrowSize: 0

  readonly property bool enlarged: root.level > 0 && root.cursorReady
  readonly property real currentSize: root.baseSize
    + (root.maxSize - root.baseSize) * (1 - Math.pow(1 - root.level, 3))

  function environmentSize() {
    var size = Number(Quickshell.env("HYPRCURSOR_SIZE") || Quickshell.env("XCURSOR_SIZE"))
    return isFinite(size) && size > 0 ? Math.round(size) : 24
  }

  function numberSetting(entry, key, fallback, minimum, maximum) {
    var value = Number(entry[key])
    if (entry[key] === undefined || !isFinite(value)) value = fallback
    return Math.max(minimum, Math.min(maximum, value))
  }

  // The entry lives in the bar layout when the bar widget is placed, and in
  // `plugins` otherwise.
  function findEntry(config) {
    var lists = [Array.isArray(config.plugins) ? config.plugins : []]
    var layout = config.bar && config.bar.layout ? config.bar.layout : {}
    for (var section in layout) {
      if (Array.isArray(layout[section])) lists.push(layout[section])
    }

    for (var i = 0; i < lists.length; i++) {
      for (var j = 0; j < lists[i].length; j++) {
        if (lists[i][j] && lists[i][j].id === root.pluginId) return lists[i][j]
      }
    }
    return {}
  }

  function applySettings(raw) {
    var entry
    try {
      entry = root.findEntry(JSON.parse(String(raw || "{}")))
    } catch (error) {
      // Keep the current settings while shell.json is mid-edit.
      return
    }

    root.baseSize = Math.round(root.numberSetting(entry, "baseSize", root.environmentSize(), 8, 256))
    root.maxSize = Math.round(root.numberSetting(entry, "maxSize", 96, root.baseSize, 256))
    root.sensitivity = root.numberSetting(entry, "sensitivity", 2.5, 1.5, 10)
    root.growMs = Math.round(root.numberSetting(entry, "growMs", 300, 16, 5000))
    root.holdMs = Math.round(root.numberSetting(entry, "holdMs", 500, 0, 10000))
    root.shrinkMs = Math.round(root.numberSetting(entry, "shrinkMs", 350, 16, 5000))
  }

  // Grow to the current maximum and back, so the size can be judged while a
  // slider is moved.
  function preview() {
    root.previewUntilMs = Date.now() + root.growMs
    root.animating = true
  }

  function addSample(line) {
    var parts = String(line || "").trim().split(/\s+/)
    var x = Number(parts[0])
    var y = Number(parts[1])
    if (parts.length !== 2 || !isFinite(x) || !isFinite(y)) return

    root.cursorX = x
    root.cursorY = y
    root.cursorReady = true

    var next = root.samples
    next.push({ x: x, y: y, t: Date.now() })
    root.samples = next
    root.animating = true
  }

  function pruneSamples(now) {
    var next = root.samples
    while (next.length > 0 && now - next[0].t > root.shakeWindowMs) next.shift()
    root.samples = next
  }

  // A shake covers far more distance than the box it stays inside, and keeps
  // turning back. One fast flick is long but straight; a single there-and-back
  // has only one reversal.
  function isShaking() {
    var points = root.samples
    if (points.length < 4) return false

    var distance = 0
    var left = points[0].x, right = points[0].x
    var top = points[0].y, bottom = points[0].y
    var reversalsX = 0, reversalsY = 0
    var lastDx = 0, lastDy = 0

    for (var i = 1; i < points.length; i++) {
      var dx = points[i].x - points[i - 1].x
      var dy = points[i].y - points[i - 1].y
      distance += Math.sqrt(dx * dx + dy * dy)
      left = Math.min(left, points[i].x)
      right = Math.max(right, points[i].x)
      top = Math.min(top, points[i].y)
      bottom = Math.max(bottom, points[i].y)

      // Ignore jitter when counting changes of direction.
      if (Math.abs(dx) >= 4) {
        if (lastDx !== 0 && (dx > 0) !== (lastDx > 0)) reversalsX++
        lastDx = dx
      }
      if (Math.abs(dy) >= 4) {
        if (lastDy !== 0 && (dy > 0) !== (lastDy > 0)) reversalsY++
        lastDy = dy
      }
    }

    var span = Math.sqrt((right - left) * (right - left) + (bottom - top) * (bottom - top))
    if (span < root.minimumShakeSpan) return false
    if (Math.max(reversalsX, reversalsY) < root.minimumReversals) return false
    return distance / span >= root.sensitivity
  }

  function tick() {
    var now = Date.now()
    var dt = root.lastTickMs > 0 ? Math.min(100, now - root.lastTickMs) : 16
    root.lastTickMs = now

    root.pruneSamples(now)
    if (root.isShaking() || now < root.previewUntilMs) root.lastShakeMs = now

    // Grow for as long as the shake continues, so a longer shake gets a
    // bigger cursor, then hold briefly before shrinking back.
    if (now - root.lastShakeMs < 60) root.level = Math.min(1, root.level + dt / root.growMs)
    else if (now - root.lastShakeMs > root.holdMs) root.level = Math.max(0, root.level - dt / root.shrinkMs)

    if (root.level === 0 && root.samples.length === 0 && now >= root.previewUntilMs) {
      root.animating = false
      root.lastTickMs = 0
    }
  }

  function setCursorHidden(hidden) {
    if (hidden === root.cursorHidden) return
    root.cursorHidden = hidden
    if (bridge.running) bridge.write(hidden ? "cursor hide\n" : "cursor show\n")
  }

  // The theme can change at runtime, so ask again when a shake starts; the
  // startup request only fills in the arrow for the first shake.
  function refreshCursorTheme() {
    if (bridge.running) bridge.write("colors\n")
  }

  function handleLine(line) {
    var parts = String(line || "").trim().split(/\s+/)
    if (parts.length === 5 && parts[0] === "arrow") {
      root.arrowSource = "file://" + parts[1]
      root.arrowHotspotX = Number(parts[2])
      root.arrowHotspotY = Number(parts[3])
      root.arrowSize = Number(parts[4])
      return
    }
    root.addSample(line)
  }

  onEnlargedChanged: {
    root.setCursorHidden(root.enlarged)
    if (root.enlarged) root.refreshCursorTheme()
  }

  Timer {
    interval: 8
    repeat: true
    running: root.animating
    onTriggered: root.tick()
  }

  // Mapped only while enlarged, so an idle overlay never sits above
  // fullscreen apps.
  Variants {
    model: Quickshell.screens

    PanelWindow {
      id: overlay
      required property var modelData
      screen: modelData
      visible: root.enlarged && cursorOnScreen
      color: "transparent"
      anchors {
        top: true
        bottom: true
        left: true
        right: true
      }

      WlrLayershell.namespace: root.pluginId
      WlrLayershell.layer: WlrLayer.Overlay
      WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
      exclusionMode: ExclusionMode.Ignore
      mask: Region {}

      readonly property bool cursorOnScreen: root.cursorX >= modelData.x
        && root.cursorX < modelData.x + modelData.width
        && root.cursorY >= modelData.y
        && root.cursorY < modelData.y + modelData.height

      // The theme's arrow at its own pixel size, scaled from its hotspot so the
      // tip stays on the pointer. Smooth filtering hides the resampling when the
      // theme's largest image is bigger than the cursor currently is.
      Image {
        visible: root.arrowSource !== ""
        source: root.arrowSource
        cache: false
        smooth: true
        transformOrigin: Item.TopLeft
        readonly property real arrowScale: root.arrowSize > 0
          ? root.currentSize / root.arrowSize : 1
        scale: arrowScale
        x: root.cursorX - overlay.modelData.x - root.arrowHotspotX * arrowScale
        y: root.cursorY - overlay.modelData.y - root.arrowHotspotY * arrowScale
      }

      CursorArrow {
        visible: root.arrowSource === ""
        x: root.cursorX - overlay.modelData.x
        y: root.cursorY - overlay.modelData.y
        size: root.currentSize
      }
    }
  }

  Process {
    id: bridge
    command: ["python3", "-u", root.bridgePath]
    running: true
    stdinEnabled: true

    stdout: SplitParser {
      onRead: line => root.handleLine(line)
    }

    // The bridge shows the cursor again when it exits, so a restarted bridge
    // starts from a visible cursor.
    onExited: {
      root.cursorHidden = false
      bridgeRestart.restart()
    }
    onRunningChanged: if (running) {
      root.refreshCursorTheme()
      root.setCursorHidden(root.enlarged)
    }
  }

  Timer {
    id: bridgeRestart
    interval: 2000
    onTriggered: bridge.running = true
  }

  FileView {
    path: root.shellConfigPath
    watchChanges: true
    printErrors: false
    onLoaded: root.applySettings(text())
    // text() is stale inside the change signal, so reload and parse on load.
    onFileChanged: reload()
  }

  Component.onDestruction: {
    if (root.cursorHidden)
      Quickshell.execDetached(["hyprctl", "eval", "hl.config({ cursor = { invisible = false } })"])
  }
}
