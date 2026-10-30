#!/usr/bin/env python3
"""I/O bridge between the Big Cursor service and Hyprland's request socket.

stdout: "<x> <y>" each time the pointer moves, and
        "arrow <path> <hotspotX> <hotspotY> <size>" after a "colors" request.
        "arrow" is printed once at startup too.
stdin:  "cursor hide" / "cursor show" lines, which hide or show the real
        compositor cursor while the service draws the enlarged one, and
        "colors", which exports the current cursor theme's arrow image.

Hyprland closes the request socket after every reply, which makes a QML
Socket log a warning per sample; one long-lived process keeps that quiet.
If the shell exits while the cursor is hidden, the bridge shows it again.
"""

import os
import select
import socket
import struct
import subprocess
import sys
import tempfile
import time
import zlib

FAST_INTERVAL = 0.008  # while the pointer is moving
IDLE_INTERVAL = 0.05  # after it has been still for IDLE_AFTER seconds
IDLE_AFTER = 1.0

COMMANDS = {
    "cursor hide": "/eval hl.config({ cursor = { invisible = true } })",
    "cursor show": "/eval hl.config({ cursor = { invisible = false } })",
}

THEME_COMMAND = "colors"
IMAGE_TYPE = 0xFFFD0002  # Xcursor image chunk
ARROW_NAMES = ("left_ptr", "default", "top_left_arrow", "arrow")
ARROW_DIR = os.path.join(
    os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir(), "big-cursor")


def socket_path():
    runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    signature = os.environ["HYPRLAND_INSTANCE_SIGNATURE"]
    return os.path.join(runtime, "hypr", signature, ".socket.sock")


def request(path, command):
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(1)
        connection.connect(path)
        connection.sendall(command.encode())
        chunks = []
        while chunk := connection.recv(4096):
            chunks.append(chunk)
    return b"".join(chunks).decode(errors="replace")


# The cursor theme name Hyprland uses comes from the desktop setting when one
# has been applied, which covers themes set with `hyprctl setcursor` (as
# Omarchy's cursor theming plugins do) and a setting changed after the shell
# started. The session environment only covers the theme a fresh session boots
# with, so it is the fallback. `dconf read` is preferred over `gsettings get`
# because gsettings reports the default theme even when nothing is set.
def desktop_theme():
    commands = (
        ["dconf", "read", "/org/gnome/desktop/interface/cursor-theme"],
        ["gsettings", "get", "org.gnome.desktop.interface", "cursor-theme"],
    )
    for command in commands:
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=2)
        except (OSError, subprocess.SubprocessError):
            continue
        return completed.stdout.strip().strip("'")
    return ""


def theme_name():
    name = desktop_theme()
    if name:
        return name
    for variable in ("HYPRCURSOR_THEME", "XCURSOR_THEME"):
        value = os.environ.get(variable)
        if value:
            return value
    return "default"


def icon_dirs():
    home = os.path.expanduser("~")
    data_home = os.environ.get("XDG_DATA_HOME") or os.path.join(home, ".local/share")
    data_dirs = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    dirs = [os.path.join(data_home, "icons"), os.path.join(home, ".icons")]
    return dirs + [os.path.join(base, "icons") for base in data_dirs.split(":") if base]


def inherited_themes(name):
    for directory in icon_dirs():
        try:
            with open(os.path.join(directory, name, "index.theme"),
                      encoding="utf-8", errors="replace") as file:
                for line in file:
                    key, _, value = line.partition("=")
                    if key.strip() == "Inherits":
                        return [theme.strip() for theme in value.split(",") if theme.strip()]
        except OSError:
            continue
    return []


def cursor_file(name, seen=()):
    for directory in icon_dirs():
        for arrow in ARROW_NAMES:
            path = os.path.join(directory, name, "cursors", arrow)
            if os.path.isfile(path):
                return os.path.realpath(path)
    if name not in seen:
        for inherited in inherited_themes(name):
            path = cursor_file(inherited, seen + (name,))
            if path:
                return path
    return None


def arrow_images(path):
    with open(path, "rb") as file:
        data = file.read()
    magic, header, _, count = struct.unpack_from("<4I", data, 0)
    if magic != 0x72756358:  # "Xcur"
        return []
    images = []
    for index in range(count):
        kind, nominal, position = struct.unpack_from("<3I", data, header + index * 12)
        if kind != IMAGE_TYPE:
            continue
        (header_size, _, _, _, width, height,
         hotspot_x, hotspot_y, _) = struct.unpack_from("<9I", data, position)
        pixels = struct.unpack_from("<%dI" % (width * height), data, position + header_size)
        images.append({
            "nominal": nominal,
            "width": width,
            "height": height,
            "hotspotX": hotspot_x,
            "hotspotY": hotspot_y,
            "pixels": pixels,
        })
    return images


# The arrow is the only cursor both Hyprland and this plugin can name. Hyprland
# picks one of the theme's images by size; the largest is exported so the
# service can scale it up without turning blurry.
def theme_arrow():
    path = cursor_file(theme_name())
    if not path:
        return None
    try:
        return arrow_images(path)
    except (OSError, struct.error):
        return None


def write_png(width, height, rgba):
    def chunk(tag, payload):
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    raw = b"".join(b"\x00" + bytes(rgba[row * width * 4:(row + 1) * width * 4])
                   for row in range(height))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6))
            + chunk(b"IEND", b""))


# Writes the theme's arrow next to the socket and reports where its hotspot is
# and its nominal size, so the service can scale it as a whole instead of
# guessing from the tip. The file name comes from a hash of the PNG, so an
# unchanged theme keeps the same URL (QML caches images by URL) and a real
# change gets a new one.
def arrow_sprite():
    images = theme_arrow()
    if not images:
        return None
    image = max(images, key=lambda image: image["width"] * image["height"])
    width, height = image["width"], image["height"]
    rgba = bytearray(width * height * 4)
    for index, pixel in enumerate(image["pixels"]):
        alpha = pixel >> 24
        if alpha == 0:
            continue
        offset = index * 4
        # Xcursor stores premultiplied ARGB and PNG alpha is straight, so the
        # color channels have to be divided by alpha again.
        rgba[offset] = min(255, (pixel >> 16 & 255) * 255 // alpha)
        rgba[offset + 1] = min(255, (pixel >> 8 & 255) * 255 // alpha)
        rgba[offset + 2] = min(255, (pixel & 255) * 255 // alpha)
        rgba[offset + 3] = alpha
    payload = write_png(width, height, rgba)
    try:
        target = os.path.join(ARROW_DIR, "arrow-%08x.png" % (zlib.crc32(payload) & 0xFFFFFFFF))
        if not os.path.exists(target):
            os.makedirs(ARROW_DIR, exist_ok=True)
            with open(target, "wb") as file:
                file.write(payload)
    except OSError:
        return None
    for name in os.listdir(ARROW_DIR):
        if name.startswith("arrow-") and name != os.path.basename(target):
            try:
                os.unlink(os.path.join(ARROW_DIR, name))
            except OSError:
                pass
    return target, image["hotspotX"], image["hotspotY"], image["nominal"]


def report_theme():
    arrow = arrow_sprite()
    if arrow:
        print("arrow", *arrow, flush=True)


# The export is only useful while the bridge runs, so it is left behind no
# longer than the overlay that reads it.
def remove_exports():
    try:
        for name in os.listdir(ARROW_DIR):
            if name.startswith("arrow-"):
                os.unlink(os.path.join(ARROW_DIR, name))
    except OSError:
        pass


def main():
    path = socket_path()
    stdin = sys.stdin.fileno()
    pending = b""
    hidden = False
    last_position = None
    last_move = 0.0
    next_poll = 0.0

    report_theme()

    try:
        while True:
            now = time.monotonic()
            interval = FAST_INTERVAL if now - last_move < IDLE_AFTER else IDLE_INTERVAL
            ready, _, _ = select.select([stdin], [], [], max(0.0, next_poll - now))

            if ready:
                data = os.read(stdin, 4096)
                if not data:
                    return  # The shell went away.
                pending += data
                *lines, pending = pending.split(b"\n")
                commands = [line.decode(errors="replace").strip() for line in lines]
                commands = [command for command in commands
                            if command in COMMANDS or command == THEME_COMMAND]
                for command in commands:
                    if command == THEME_COMMAND:
                        report_theme()
                    else:
                        try:
                            request(path, COMMANDS[command])
                            hidden = command == "cursor hide"
                        except OSError:
                            pass
                continue

            next_poll = now + interval
            try:
                x, y = (int(value) for value in request(path, "/cursorpos").split(","))
            except (OSError, ValueError):
                continue

            if (x, y) != last_position:
                last_position = (x, y)
                last_move = now
                print(x, y, flush=True)
    finally:
        if hidden:
            try:
                request(path, COMMANDS["cursor show"])
            except OSError:
                pass
        remove_exports()


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, BrokenPipeError):
        pass
