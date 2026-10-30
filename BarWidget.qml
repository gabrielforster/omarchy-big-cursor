import QtQuick
import Quickshell
import qs.Commons
import qs.Ui

BarWidget {
  id: root
  moduleName: "io.github.gabrielforster.big-cursor"

  readonly property var cursorService: bar?.shell?.serviceFor(root.moduleName)
  readonly property int cursorSize: cursorService
    ? cursorService.baseSize : Number(setting("baseSize", 24))
  readonly property bool cursorSizeSet: cursorService
    ? cursorService.sizeSet : setting("baseSize", undefined) !== undefined
  readonly property int maxSize: cursorService
    ? cursorService.maxSize : Number(setting("maxSize", 96))
  readonly property real sensitivity: cursorService
    ? cursorService.sensitivity : Number(setting("sensitivity", 2.5))
  property bool popupOpen: false

  // The shake threshold has three named levels; any other value is custom.
  readonly property var sensitivityLevels: [
    { name: "Light", value: 2.0 },
    { name: "Medium", value: 2.5 },
    { name: "Hard", value: 3.5 }
  ]
  readonly property int sensitivityLevel: root.levelFor(root.sensitivity)
  property bool customSensitivity: root.sensitivityLevel < 0
  onSensitivityLevelChanged: if (root.sensitivityLevel < 0) root.customSensitivity = true

  function levelFor(value) {
    for (var i = 0; i < root.sensitivityLevels.length; i++)
      if (Math.abs(root.sensitivityLevels[i].value - value) < 0.001) return i
    return -1
  }

  function nearestLevel(value) {
    var nearest = 0
    for (var i = 1; i < root.sensitivityLevels.length; i++) {
      if (Math.abs(root.sensitivityLevels[i].value - value)
          < Math.abs(root.sensitivityLevels[nearest].value - value)) nearest = i
    }
    return nearest
  }

  function setSensitivityLevel(index) {
    root.customSensitivity = false
    root.persist("sensitivity", root.sensitivityLevels[index].value)
  }

  // Write one setting to this widget's shell.json entry. The service picks it
  // up from the file, but is updated first so the change is live on release.
  function persist(key, value) {
    var changes = {}
    changes[key] = value
    if (cursorService) cursorService[key] = value
    root.writeEntry(changes, [])
  }

  // The everyday size reaches Hyprland on release only: every change reloads
  // the whole cursor theme. The maximum is raised with it so it never sits
  // below the everyday size.
  function setCursorSize(size) {
    var changes = { baseSize: size }
    if (root.maxSize < size) changes.maxSize = size
    if (cursorService) {
      cursorService.baseSize = size
      cursorService.sizeSet = true
      cursorService.maxSize = Math.max(cursorService.maxSize, size)
      cursorService.applyCursorSize()
    }
    root.writeEntry(changes, [])
  }

  // Dropping the setting hands the size back to the session.
  function resetCursorSize() {
    if (cursorService) {
      cursorService.sizeSet = false
      cursorService.baseSize = cursorService.environmentSize()
      cursorService.applyCursorSize()
    }
    root.writeEntry({}, ["baseSize"])
  }

  function writeEntry(changes, removed) {
    var entry = { id: root.moduleName }
    for (var existing in root.settings)
      if (existing !== "id" && removed.indexOf(existing) < 0) entry[existing] = root.settings[existing]
    for (var key in changes) entry[key] = changes[key]

    root.settings = entry
    if (root.bar && root.bar.shell && typeof root.bar.shell.updateEntryInline === "function")
      root.bar.shell.updateEntryInline(root.moduleName, entry)
  }

  function preview() {
    if (cursorService) cursorService.preview()
  }

  function close() {
    root.popupOpen = false
  }

  function schedulePopupClose() {
    if (root.popupOpen) popupCloseTimer.restart()
  }

  Timer {
    id: popupCloseTimer
    interval: 200
    repeat: false
    onTriggered: {
      if (root.popupOpen && !button.tooltipHovered && !popup.containsMouse && !sizeSlider.dragging
          && !maxSlider.dragging
          && !sensitivitySlider.dragging && !customSlider.dragging)
        root.popupOpen = false
    }
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  onPopupOpenChanged: if (!root.popupOpen) popupCloseTimer.stop()

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: ""
    labelVisible: false
    hasVisualContent: true
    fixedWidth: Style.bar.iconSlot
    fixedHeight: Style.bar.iconSlot
    useActiveColor: false
    tooltipText: "Big cursor · up to " + root.maxSize + " px"

    onPressed: function(buttonId) {
      if (buttonId === Qt.RightButton) root.preview()
      else if (buttonId === Qt.LeftButton) root.popupOpen = !root.popupOpen
    }

    onTooltipHoveredChanged: {
      if (tooltipHovered) popupCloseTimer.stop()
      else root.schedulePopupClose()
    }

    CursorArrow {
      anchors.centerIn: parent
      anchors.horizontalCenterOffset: Style.space(1)
      size: Style.space(20)
      fillColor: button.foreground
      strokeColor: "transparent"
    }
  }

  PopupCard {
    id: popup
    anchorItem: button
    bar: root.bar
    owner: root
    open: root.popupOpen
    contentWidth: popup.fittedContentWidth(Style.space(260))
    contentHeight: popup.fittedContentHeight(panelColumn.implicitHeight)

    // Keep the popup open while moving between the bar button and the card.
    onContainsMouseChanged: {
      if (containsMouse) popupCloseTimer.stop()
      else root.schedulePopupClose()
    }

    Column {
      id: panelColumn
      anchors.fill: parent
      spacing: Style.space(10)

      Text {
        text: "BIG CURSOR"
        color: root.bar.foreground
        font.family: root.bar.fontFamily
        font.pixelSize: Style.font.caption
        font.bold: true
      }

      Text {
        width: parent.width
        text: "Shake the mouse to find the cursor."
        color: root.bar.foreground
        opacity: 0.7
        wrapMode: Text.WordWrap
        font.family: root.bar.fontFamily
        font.pixelSize: Style.font.caption
      }

      PanelSeparator { foreground: root.bar.foreground }

      SettingRow {
        label: "CURSOR SIZE"
        valueText: (sizeSlider.dragging ? Math.round(sizeSlider.liveValue) : root.cursorSize) + " px"
          + (root.cursorSizeSet ? "" : " · default")

        PanelSlider {
          id: sizeSlider
          width: parent.width
          bar: root.bar
          minimum: 16
          maximum: 128
          step: 4
          integer: true
          value: root.cursorSize
          onReleased: root.setCursorSize(Math.round(value))
        }
      }

      Button {
        width: parent.width
        text: "Reset to default"
        foreground: root.bar.foreground
        bordered: true
        enabled: root.cursorSizeSet
        opacity: root.cursorSizeSet ? 1 : 0.4
        onClicked: root.resetCursorSize()
      }

      PanelSeparator { foreground: root.bar.foreground }

      SettingRow {
        label: "MAX SIZE"
        valueText: root.maxSize + " px"

        PanelSlider {
          id: maxSlider
          width: parent.width
          bar: root.bar
          minimum: 32
          maximum: 256
          step: 8
          integer: true
          value: root.maxSize
          onMoved: if (root.cursorService) root.cursorService.maxSize = Math.round(value)
          onReleased: {
            root.persist("maxSize", Math.round(value))
            root.preview()
          }
        }
      }

      SettingRow {
        label: "SHAKE NEEDED"
        valueText: root.customSensitivity
          ? "Custom · " + root.sensitivity.toFixed(1)
          : root.sensitivityLevels[Math.max(0, root.sensitivityLevel)].name

        PanelSlider {
          id: sensitivitySlider
          width: parent.width
          bar: root.bar
          minimum: 0
          maximum: root.sensitivityLevels.length - 1
          step: 1
          integer: true
          tickCount: root.sensitivityLevels.length
          enabled: !root.customSensitivity
          opacity: root.customSensitivity ? 0.4 : 1
          value: root.nearestLevel(root.sensitivity)
          onReleased: root.setSensitivityLevel(Math.round(value))
        }
      }

      Item {
        width: parent.width
        height: customSwitch.implicitHeight

        Text {
          anchors.verticalCenter: parent.verticalCenter
          text: "Custom value"
          color: root.bar.foreground
          opacity: 0.7
          font.family: root.bar.fontFamily
          font.pixelSize: Style.font.caption
        }

        ToggleSwitch {
          id: customSwitch
          anchors.right: parent.right
          anchors.verticalCenter: parent.verticalCenter
          checked: root.customSensitivity
          foreground: root.bar.foreground
          // Turning custom off snaps back to the closest named level.
          onToggled: {
            if (root.customSensitivity) root.setSensitivityLevel(root.nearestLevel(root.sensitivity))
            else root.customSensitivity = true
          }
        }
      }

      // Lower values trigger on a lighter shake. Past about 4 a shake has to
      // be faster than most hands can manage.
      PanelSlider {
        id: customSlider
        visible: root.customSensitivity
        width: parent.width
        bar: root.bar
        minimum: 1.5
        maximum: 5
        step: 0.1
        value: root.sensitivity
        onMoved: if (root.cursorService) root.cursorService.sensitivity = Math.round(value * 10) / 10
        onReleased: root.persist("sensitivity", Math.round(value * 10) / 10)
      }

      Button {
        width: parent.width
        text: "Preview"
        foreground: root.bar.foreground
        bordered: true
        onClicked: root.preview()
      }
    }
  }

  component SettingRow: Column {
    id: row
    property string label: ""
    property string valueText: ""
    default property alias content: slot.data

    width: parent.width
    spacing: Style.space(4)

    Item {
      width: parent.width
      height: labelText.implicitHeight

      Text {
        id: labelText
        text: row.label
        color: root.bar.foreground
        opacity: 0.7
        font.family: root.bar.fontFamily
        font.pixelSize: Style.font.caption
        font.bold: true
      }

      Text {
        anchors.right: parent.right
        text: row.valueText
        color: root.bar.foreground
        opacity: 0.75
        font.family: root.bar.fontFamily
        font.pixelSize: Style.font.caption
      }
    }

    Item {
      id: slot
      width: parent.width
      height: childrenRect.height
    }
  }
}
