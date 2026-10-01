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


def test_tray_stale_layout_events():
    _run("""
        import os
        import time
        from virtManager.systray import _StatusNotifier, _MENU_PATH, _MENU_INTERFACE

        invoked = []
        class Menu:
            entries = []
            def items(self):
                return self.entries
        menu = Menu()
        def entry(key, enabled=True):
            return (key, lambda: invoked.append(key), enabled, (), key)
        menu.entries = [entry("a"), entry("b")]
        tray = _StatusNotifier(menu)
        client = Gio.DBusConnection.new_for_address_sync(
            os.environ["DBUS_SESSION_BUS_ADDRESS"],
            (Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
             | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION),
            None, None,
        )
        def call(method, args):
            completed = []
            def finished(bus, result):
                completed.append(bus.call_finish(result))
            client.call(tray._bus.get_unique_name(), _MENU_PATH, _MENU_INTERFACE.name,
                        method, args, None, Gio.DBusCallFlags.NONE, 5000, None, finished)
            deadline = time.monotonic() + 6
            while not completed:
                assert time.monotonic() < deadline
                GLib.MainContext.default().iteration(False)
                time.sleep(.001)
            return completed[0].unpack()
        def layout_ids():
            _revision, layout = call("GetLayout", GLib.Variant("(iias)", (0, -1, [])))
            return {child[1]["label"]: child[0] for child in layout[2]}
        def click(item_id):
            call("Event", GLib.Variant("(isvu)", (item_id, "clicked", GLib.Variant("s", ""), 0)))
        original = layout_ids()
        menu.entries = [entry("c"), entry("b"), entry("a")]
        tray.refresh()
        reordered = layout_ids()
        assert reordered["a"] == original["a"]
        assert reordered["b"] == original["b"]
        click(original["b"])
        assert invoked == ["b"]
        menu.entries = [entry("c"), entry("a", False)]
        tray.refresh()
        click(original["b"])
        click(original["a"])
        assert invoked == ["b"]
        menu.entries.append(entry("b"))
        tray.refresh()
        assert layout_ids()["b"] != original["b"]
        click(original["b"])
        assert invoked == ["b"]
        client.close_sync(None)
        tray.close()
    """)


def test_manager_without_selection():
    _run("""
        from virtManager.manager import vmmManager
        manager = vmmManager()
        assert manager.current_vm() is None
        for name in ("vm-run", "vm-pause", "vm-shutdown", "vm-open", "menu_edit_delete"):
            assert not manager.widget(name).get_sensitive(), name
        manager.cleanup()
    """)


def test_vm_menu_actions_dismiss_before_callback():
    _run("""
        import time
        from virtManager.baseclass import _BuilderScope
        from virtManager.lib import uiutil
        builder = Gtk.Builder()
        scope = _BuilderScope()
        builder.set_scope(scope)
        builder.add_from_file(buildconfig.BuildConfig.ui_dir + "/vmwindow.ui")
        window = builder.get_object("vmm-vmwindow")
        menu = builder.get_object("virtual_machine1_menu")
        uiutil.init_menu(menu)
        window.present()
        def drain():
            deadline = time.monotonic() + .15
            while time.monotonic() < deadline:
                GLib.MainContext.default().iteration(False)
                time.sleep(.001)
        drain()
        for button, handler in (
            ("details-menu-vm-screenshot", "on_details_menu_screenshot_activate"),
            ("details-menu-usb-redirection", "on_details_menu_usb_redirection"),
        ):
            observed = []
            scope.handlers[handler] = lambda _button: observed.append(menu.get_visible())
            builder.get_object("details-vm-menu").popup()
            drain()
            assert menu.get_visible()
            builder.get_object(button).emit("clicked")
            assert observed == [False]
        window.destroy()
    """)


def test_tray_cleanup_releases_signal_owners():
    _run("""
        import gc
        import weakref
        from virtManager.connmanager import vmmConnectionManager
        from virtManager.systray import _TrayMenu

        manager = vmmConnectionManager.get_instance()
        manager.add_conn("test:///default")
        menu = _TrayMenu(lambda: None)
        reference = weakref.ref(menu)
        menu.cleanup()
        del menu
        gc.collect()
        assert reference() is None
        # Teardown also works after the connection manager's singleton has
        # been cleared by normal application exit.
        menu = _TrayMenu(lambda: None)
        manager.cleanup()
        menu.cleanup()
        reference = weakref.ref(menu)
        del menu
        gc.collect()
        assert reference() is None
    """)


def test_native_menu_dynamic_labels_and_nested_dismissal():
    _run("""
        import time
        from virtManager.lib import uiutil

        def drain():
            until = time.monotonic() + .15
            while time.monotonic() < until:
                GLib.MainContext.default().iteration(False)
                time.sleep(.001)

        action = uiutil.new_menu_action_button("_Run")
        inner_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        inner_box.append(action)
        inner = Gtk.Popover(child=inner_box)
        submenu = Gtk.MenuButton(label="_Actions", use_underline=True, popover=inner)
        outer_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer_box.append(submenu)
        outer = Gtk.Popover(child=outer_box)
        anchor = Gtk.MenuButton(label="Menu", popover=outer)
        window = Gtk.Window(child=anchor, default_width=300, default_height=150)
        for menu in (inner, outer):
            uiutil.init_menu(menu)
        window.present()
        drain()
        anchor.popup()
        drain()
        submenu.set_label("_Renamed")
        assert submenu.get_child().get_text() == "Renamed"
        assert submenu.get_child().get_mnemonic_widget() == submenu
        Gtk.Widget.set_direction(submenu, Gtk.TextDirection.RTL)
        assert submenu.get_direction() == Gtk.ArrowType.LEFT
        submenu.popup()
        drain()
        action.set_label("_Resume")
        assert action.get_child().get_text() == "Resume"
        observed = []
        action.connect("clicked", lambda _button:
                       observed.append((inner.get_visible(), outer.get_visible())))
        assert inner.get_visible() and outer.get_visible()
        action.emit("clicked")
        assert observed == [(False, False)]
        window.destroy()
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


def test_storage_browser_embeds_storage_controls():
    _run("""
        from virtManager.connection import vmmConnection
        from virtManager.storagebrowse import vmmStorageBrowser
        conn = vmmConnection("test:///default")
        browser = vmmStorageBrowser(conn)
        browser.set_browse_reason(browser.REASON_ISO_MEDIA)
        browser.show(None)
        assert browser.storagelist.top_box.get_parent() == browser.widget("storage-align")
        assert browser.storagelist.widget("browse-local").get_sensitive()
        browser.cleanup()
        conn.cleanup()
    """)

def test_tray_uses_current_text_direction():
    _run("""
        from virtManager.systray import _StatusNotifier
        class Menu:
            def items(self):
                return []
        tray = _StatusNotifier(Menu())
        for direction, expected in ((Gtk.TextDirection.RTL, "rtl"),
                                    (Gtk.TextDirection.LTR, "ltr")):
            Gtk.Widget.set_default_direction(direction)
            value = tray._menu_property(None, None, None, None, "TextDirection")
            assert value.unpack() == expected
        tray.close()
    """)

def test_context_menu_targets_clicked_row_with_headers():
    _run("""
        import time
        from types import SimpleNamespace
        from virtManager.manager import vmmManager
        from virtManager.hoststorage import vmmHostStorage

        model = Gtk.ListStore(str)
        for name in ("alpha", "beta", "gamma"):
            model.append([name])
        tree = Gtk.TreeView(model=model)
        column = Gtk.TreeViewColumn("Name", Gtk.CellRendererText(), text=0)
        tree.append_column(column)
        gesture = Gtk.GestureClick(button=3)
        tree.add_controller(gesture)
        window = Gtk.Window(child=tree, default_width=400, default_height=300)
        window.present()
        until = time.monotonic() + .25
        while time.monotonic() < until:
            GLib.MainContext.default().iteration(False)
            time.sleep(.001)
        targets = []
        owner = SimpleNamespace(model=model,
            popup_vm_menu=lambda m, it, x, y: targets.append(m[it][0]))
        popover = Gtk.Popover(child=Gtk.Label(label="Copy Volume Path"))
        storage = SimpleNamespace(_volmenu=popover)
        for index, expected in ((0, "alpha"), (2, "gamma")):
            rect = tree.get_background_area(Gtk.TreePath.new_from_indices([index]), column)
            x, y = tree.convert_bin_window_to_widget_coords(30, rect.y + rect.height - 2)
            vmmManager.popup_vm_menu_button(owner, gesture, 1, x, y)
            assert targets[-1] == expected
            vmmHostStorage._vol_popup_menu_cb(storage, gesture, 1, x, y)
            selected_model, selected = tree.get_selection().get_selected()
            assert selected_model[selected][0] == expected
            popover.popdown()
        popover.unparent()
        window.destroy()
    """)
