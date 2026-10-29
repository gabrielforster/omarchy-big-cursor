"""Shared fixtures for the cursor-bridge tests.

Nothing here touches the real session: themes are synthetic Xcursor files in a
temporary icon directory, Hyprland is a fake request socket, and `gsettings`
(plus `dconf`) are stub scripts that read the desktop cursor theme from a file
the test controls.
"""

import importlib.util
import os
import queue
import shutil
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRIDGE = os.path.join(ROOT, "cursor-bridge.py")

XCURSOR_MAGIC = 0x72756358
IMAGE_TYPE = 0xFFFD0002


def load_bridge():
    spec = importlib.util.spec_from_file_location("cursor_bridge", BRIDGE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- Xcursor images ---------------------------------------------------------

def argb(alpha, red, green, blue):
    """A pixel as Xcursor stores it: ARGB with premultiplied color."""
    return alpha << 24 | red << 16 | green << 8 | blue


def premultiplied(alpha, red, green, blue):
    return argb(alpha, red * alpha // 255, green * alpha // 255, blue * alpha // 255)


TRANSPARENT = 0
BLACK = argb(255, 0, 0, 0)
WHITE = argb(255, 255, 255, 255)


def image(size, pixels, nominal=None, hotspot=(1, 1)):
    return {"nominal": size if nominal is None else nominal, "width": size,
            "height": size, "hotspot": hotspot, "pixels": pixels}


def outlined(size, fill, outline=WHITE):
    """A square with a 1px outline. `fill(row, column)` colors the inside."""
    pixels = []
    for row in range(size):
        for column in range(size):
            edge = row in (0, size - 1) or column in (0, size - 1)
            pixels.append(outline if edge else fill(row, column))
    return pixels


def xcursor(images):
    header_size = 16
    positions, chunks = [], []
    position = header_size + 12 * len(images)
    for entry in images:
        width, height = entry["width"], entry["height"]
        chunk = struct.pack("<9I", 36, IMAGE_TYPE, entry["nominal"], 1, width, height,
                            entry["hotspot"][0], entry["hotspot"][1], 50)
        chunk += struct.pack("<%dI" % (width * height), *entry["pixels"])
        positions.append(position)
        chunks.append(chunk)
        position += len(chunk)
    toc = b"".join(struct.pack("<3I", IMAGE_TYPE, entry["nominal"], at)
                   for entry, at in zip(images, positions))
    return struct.pack("<4I", XCURSOR_MAGIC, header_size, 0x10000, len(images)) + toc + b"".join(chunks)


# --- PNG --------------------------------------------------------------------

def read_png(path):
    """Decodes an 8-bit RGBA PNG into (width, height, [(r, g, b, a), ...])."""
    with open(path, "rb") as file:
        data = file.read()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    position, idat = 8, b""
    while position < len(data):
        length, = struct.unpack_from(">I", data, position)
        tag = data[position + 4:position + 8]
        payload = data[position + 8:position + 8 + length]
        if tag == b"IHDR":
            width, height, depth, color_type = struct.unpack_from(">IIBB", payload)
            assert (depth, color_type) == (8, 6), "expected 8-bit RGBA"
        elif tag == b"IDAT":
            idat += payload
        position += 12 + length

    raw = zlib.decompress(idat)
    stride = width * 4
    rows, previous = [], bytearray(stride)
    for row in range(height):
        start = row * (stride + 1)
        kind, line = raw[start], bytearray(raw[start + 1:start + 1 + stride])
        for index in range(stride):
            left = line[index - 4] if index >= 4 else 0
            up = previous[index]
            corner = previous[index - 4] if index >= 4 else 0
            if kind == 1:
                line[index] = (line[index] + left) & 255
            elif kind == 2:
                line[index] = (line[index] + up) & 255
            elif kind == 3:
                line[index] = (line[index] + (left + up) // 2) & 255
            elif kind == 4:
                estimate = left + up - corner
                nearest = min((abs(estimate - left), 0, left), (abs(estimate - up), 1, up),
                              (abs(estimate - corner), 2, corner))[2]
                line[index] = (line[index] + nearest) & 255
        rows.append(line)
        previous = line
    pixels = [tuple(row[column * 4:column * 4 + 4]) for row in rows for column in range(width)]
    return width, height, pixels


# --- Session sandbox --------------------------------------------------------

STUB = """#!/bin/sh
# Stands in for `gsettings get ... cursor-theme` and `dconf read ...`.
[ -s "$BIG_CURSOR_TEST_DESKTOP_THEME" ] || exit 0
printf "'%s'\\n" "$(cat "$BIG_CURSOR_TEST_DESKTOP_THEME")"
"""


class Session:
    """A throwaway home, icon directories, runtime dir and PATH stubs."""

    def __init__(self):
        # Unix socket paths are limited to ~100 bytes, so stay near the root.
        self.root = tempfile.mkdtemp(prefix="bc-", dir="/tmp" if os.path.isdir("/tmp") else None)
        self.home = os.path.join(self.root, "home")
        self.data_home = os.path.join(self.root, "data")
        self.system_data = os.path.join(self.root, "sys")
        self.runtime = os.path.join(self.root, "run")
        self.bin = os.path.join(self.root, "bin")
        self.desktop_theme_file = os.path.join(self.root, "desktop-theme")
        for directory in (self.home, self.data_home, self.system_data, self.runtime, self.bin):
            os.makedirs(directory)
        for name in ("gsettings", "dconf"):
            path = os.path.join(self.bin, name)
            with open(path, "w") as file:
                file.write(STUB)
            os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def environment(self, **extra):
        env = {
            "HOME": self.home,
            "XDG_DATA_HOME": self.data_home,
            "XDG_DATA_DIRS": self.system_data,
            "XDG_RUNTIME_DIR": self.runtime,
            "HYPRLAND_INSTANCE_SIGNATURE": "test",
            "PATH": self.bin + os.pathsep + os.environ.get("PATH", "/usr/bin"),
            "BIG_CURSOR_TEST_DESKTOP_THEME": self.desktop_theme_file,
        }
        env.update(extra)
        return env

    def set_desktop_theme(self, name):
        with open(self.desktop_theme_file, "w") as file:
            file.write(name)

    def add_theme(self, name, cursors=None, inherits=None, base=None):
        directory = os.path.join(base or os.path.join(self.system_data, "icons"), name)
        os.makedirs(os.path.join(directory, "cursors"), exist_ok=True)
        if inherits is not None:
            with open(os.path.join(directory, "index.theme"), "w") as file:
                file.write("[Icon Theme]\nName=%s\nInherits=%s\n" % (name, inherits))
        for cursor, content in (cursors or {}).items():
            path = os.path.join(directory, "cursors", cursor)
            if isinstance(content, str):  # a symlink to another cursor, as themes ship them
                os.symlink(content, path)
            else:
                with open(path, "wb") as file:
                    file.write(content)
        return directory

    @property
    def arrow_dir(self):
        return os.path.join(self.runtime, "big-cursor")


class FakeHyprland:
    """Answers the request socket the way Hyprland does: one reply, then close."""

    def __init__(self, session, position=(100, 200)):
        self.position = position
        self.requests = []
        directory = os.path.join(session.runtime, "hypr", "test")
        os.makedirs(directory)
        self.server = socket.socket(socket.AF_UNIX)
        self.server.bind(os.path.join(directory, ".socket.sock"))
        self.server.listen(16)
        self.server.settimeout(0.2)
        self.running = True
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        while self.running:
            try:
                connection, _ = self.server.accept()
            except (socket.timeout, OSError):
                continue
            with connection:
                request = connection.recv(4096).decode()
                if request == "/cursorpos":
                    connection.sendall(("%d, %d" % self.position).encode())
                else:
                    self.requests.append(request)
                    connection.sendall(b"ok")

    def close(self):
        self.running = False
        self.thread.join()
        self.server.close()


class Bridge:
    """cursor-bridge.py running as the shell runs it, with its output queued."""

    def __init__(self, session, **env):
        self.process = subprocess.Popen(
            [sys.executable, "-u", BRIDGE], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=session.environment(**env), text=True)
        self.lines = queue.Queue()
        self.seen = []
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        for line in self.process.stdout:
            self.lines.put(line.rstrip("\n"))

    def send(self, *commands):
        self.process.stdin.write("".join(command + "\n" for command in commands))
        self.process.stdin.flush()

    def wait_for(self, prefix, timeout=3.0):
        """The next line starting with `prefix`, split into words, or None."""
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                line = self.lines.get(timeout=remaining)
            except queue.Empty:
                return None
            self.seen.append(line)
            if line.startswith(prefix):
                return line.split()

    def close(self, timeout=3.0):
        """Closes stdin as a quitting shell does and returns the exit code."""
        try:
            self.process.stdin.close()
        except BrokenPipeError:
            pass
        try:
            return self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
            return None
