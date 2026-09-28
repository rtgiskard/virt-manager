# This work is licensed under the GNU GPLv2 or later.
# See the COPYING file in the top-level directory.

"""GTK4 regressions, isolated from desktop settings and the application bus."""

import os
import subprocess
import textwrap

import pytest

from virtinst import buildconfig

pytestmark = pytest.mark.skipif(
    not os.environ.get("VIRT_MANAGER_TEST_DISPLAY"),
    reason="Set VIRT_MANAGER_TEST_DISPLAY to a private Wayland compositor",
)


def _run(code, x11=False):
    env = os.environ.copy()
    env.update(
        GSETTINGS_BACKEND="memory",
        GSETTINGS_SCHEMA_DIR=buildconfig.BuildConfig.gsettings_dir,
        GDK_BACKEND="wayland",
        WAYLAND_DISPLAY=env["VIRT_MANAGER_TEST_DISPLAY"],
        GTK_A11Y="none",
    )
    if x11:
        env.update(GDK_BACKEND="x11", DISPLAY=env["VIRT_MANAGER_TEST_XDISPLAY"])
    setup = """
import builtins
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk
from virtinst import buildconfig
from virtManager import config
from virtManager.lib.testmock import CLITestOptionsClass

builtins._ = lambda value: value
Gtk.init()
config.vmmConfig.get_instance(buildconfig.BuildConfig, CLITestOptionsClass(["disable-libguestfs"]))
"""
    result = subprocess.run(
        ["dbus-run-session", "--", "python3", "-c", setup + textwrap.dedent(code)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_manager_without_selection():
    _run("""
        from virtManager.manager import vmmManager
        manager = vmmManager()
        assert manager.current_vm() is None
        for name in ("vm-run", "vm-pause", "vm-shutdown", "vm-open", "menu_edit_delete"):
            assert not manager.widget(name).get_sensitive(), name
        manager.cleanup()
    """)


@pytest.mark.skipif(
    not os.environ.get("VIRT_MANAGER_TEST_XDISPLAY"),
    reason="Set VIRT_MANAGER_TEST_XDISPLAY to the private compositor's XWayland display",
)
def test_real_close_shortcut_without_focused_child():
    _run("""
        import ctypes
        import time
        gi.require_version("GdkX11", "4.0")
        from gi.repository import GdkX11
        from virtManager.baseclass import vmmGObjectUI

        xlib = ctypes.CDLL("libX11.so.6")
        xtest = ctypes.CDLL("libXtst.so.6")
        xlib.XOpenDisplay.argtypes = [ctypes.c_char_p]
        xlib.XOpenDisplay.restype = ctypes.c_void_p
        display = xlib.XOpenDisplay(None)
        assert display
        xlib.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        xlib.XKeysymToKeycode.restype = ctypes.c_uint
        xlib.XSetInputFocus.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
                                       ctypes.c_int, ctypes.c_ulong]
        xlib.XFlush.argtypes = [ctypes.c_void_p]
        xlib.XCloseDisplay.argtypes = [ctypes.c_void_p]
        xtest.XTestFakeKeyEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                           ctypes.c_int, ctypes.c_ulong]
        def drain():
            until = time.monotonic() + .25
            while time.monotonic() < until:
                GLib.MainContext.default().iteration(False)
                time.sleep(.001)
        class Window:
            topwin = Gtk.Window(default_width=300, default_height=150)
            blocked = True
            def close(self):
                self.topwin.set_visible(False)
        owner = Window()
        vmmGObjectUI.bind_close_shortcut(owner, "<Control>w", lambda: not owner.blocked)
        owner.topwin.present()
        drain()
        assert owner.topwin.get_focus() is None
        xid = GdkX11.X11Surface.get_xid(owner.topwin.get_surface())
        xlib.XSetInputFocus(display, xid, 1, 0)
        xlib.XFlush(display)
        drain()
        codes = [xlib.XKeysymToKeycode(display, key) for key in (0xffe3, ord("w"))]
        for blocked in (True, False):
            owner.blocked = blocked
            for key in codes:
                xtest.XTestFakeKeyEvent(display, key, 1, 0)
            for key in reversed(codes):
                xtest.XTestFakeKeyEvent(display, key, 0, 0)
            xlib.XFlush(display)
            drain()
            assert owner.topwin.get_visible() == blocked
        owner.topwin.destroy()
        xlib.XCloseDisplay(display)
    """, x11=True)
