# Copyright (C) 2009, 2013, 2014 Red Hat, Inc.
#
# This work is licensed under the GNU GPLv2 or later.
# See the COPYING file in the top-level directory.

"""GTK-independent StatusNotifierItem tray for the GTK4 application."""

from gi.repository import Gio
from gi.repository import GLib

from virtinst import log

from . import vmmenu
from .baseclass import vmmGObject
from .connmanager import vmmConnectionManager

_ITEM_PATH = "/org/virt_manager/StatusNotifierItem"
_MENU_PATH = "/org/virt_manager/StatusNotifierMenu"
_WATCHER = "org.kde.StatusNotifierWatcher"

_ITEM_INTERFACE = Gio.DBusNodeInfo.new_for_xml("""
<node><interface name="org.kde.StatusNotifierItem">
  <method name="Activate">
    <arg type="i" direction="in"/><arg type="i" direction="in"/>
  </method>
  <method name="SecondaryActivate">
    <arg type="i" direction="in"/><arg type="i" direction="in"/>
  </method>
  <method name="ContextMenu">
    <arg type="i" direction="in"/><arg type="i" direction="in"/>
  </method>
  <method name="Scroll"><arg type="i" direction="in"/><arg type="s" direction="in"/></method>
  <signal name="NewStatus"><arg type="s"/></signal>
  <property name="Category" type="s" access="read"/>
  <property name="Id" type="s" access="read"/>
  <property name="Title" type="s" access="read"/>
  <property name="Status" type="s" access="read"/>
  <property name="IconName" type="s" access="read"/>
  <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
  <property name="Menu" type="o" access="read"/>
  <property name="ItemIsMenu" type="b" access="read"/>
</interface></node>
""").interfaces[0]

_MENU_INTERFACE = Gio.DBusNodeInfo.new_for_xml("""
<node><interface name="com.canonical.dbusmenu">
  <method name="GetLayout">
    <arg type="i" direction="in"/><arg type="i" direction="in"/>
    <arg type="as" direction="in"/><arg type="u" direction="out"/>
    <arg type="(ia{sv}av)" direction="out"/>
  </method>
  <method name="GetGroupProperties">
    <arg type="ai" direction="in"/><arg type="as" direction="in"/>
    <arg type="a(ia{sv})" direction="out"/>
  </method>
  <method name="GetProperty">
    <arg type="i" direction="in"/><arg type="s" direction="in"/>
    <arg type="v" direction="out"/>
  </method>
  <method name="Event">
    <arg type="i" direction="in"/><arg type="s" direction="in"/>
    <arg type="v" direction="in"/><arg type="u" direction="in"/>
  </method>
  <method name="EventGroup"><arg type="a(isvu)" direction="in"/>
    <arg type="ai" direction="out"/></method>
  <method name="AboutToShow"><arg type="i" direction="in"/>
    <arg type="b" direction="out"/></method>
  <method name="AboutToShowGroup"><arg type="ai" direction="in"/>
    <arg type="ai" direction="out"/><arg type="ai" direction="out"/></method>
  <signal name="LayoutUpdated"><arg type="u"/><arg type="i"/></signal>
  <property name="Version" type="u" access="read"/>
  <property name="TextDirection" type="s" access="read"/>
  <property name="Status" type="s" access="read"/>
  <property name="IconThemePath" type="as" access="read"/>
</interface></node>
""").interfaces[0]


def _toggle_manager():
    from .manager import vmmManager

    manager = vmmManager.get_instance(None)
    if manager.is_visible():
        manager.close()
    else:
        manager.show()


def _connect(uri):
    conn = vmmConnectionManager.get_instance().conns.get(uri)
    if conn and conn.is_disconnected():
        conn.open()


def _disconnect(uri):
    conn = vmmConnectionManager.get_instance().conns.get(uri)
    if conn and not conn.is_disconnected():
        conn.close()


class _TrayMenu(vmmGObject):
    """Keep the connection/VM actions current without creating GTK3 menus."""

    def __init__(self, changed):
        super().__init__()
        self.topwin = None  # Parent for VMActionUI error dialogs
        self._changed = changed
        manager = vmmConnectionManager.get_instance()
        self._manager = manager
        manager.connect("conn-added", self._conn_added)
        manager.connect("conn-removed", self._conn_removed)
        for conn in manager.conns.values():
            self._conn_added(manager, conn)

    def _cleanup(self):
        manager = self._manager
        manager.disconnect_by_obj(self)
        for conn in manager.conns.values():
            conn.disconnect_by_obj(self)
            for vm in conn.list_vms():
                vm.disconnect_by_obj(self)
        self._manager = None
        self._changed = None

    def _conn_added(self, _manager, conn):
        conn.connect("vm-added", self._vm_added)
        conn.connect("vm-removed", self._vm_removed)
        conn.connect("state-changed", self._state_changed)
        for vm in conn.list_vms():
            self._vm_added(conn, vm)
        self._state_changed(conn)

    def _conn_removed(self, _manager, _conn):
        self._changed()

    def _vm_added(self, _conn, vm):
        vm.connect("state-changed", self._state_changed)
        self._changed()

    def _vm_removed(self, _conn, _vm):
        self._changed()

    def _state_changed(self, _obj):
        self._changed()

    def _vm_actions(self, vm):
        states = vmmenu.vm_action_states(vm)

        def action(label, method):
            name = method.__name__
            return (
                label.replace("_", "", 1),
                lambda method=method, vm=vm: method(self, vm),
                states[name],
                (),
                name,
            )

        shutdown = (
            _("_Shut Down").replace("_", "", 1),
            None,
            states["shutdown"],
            (
                action(_("_Reboot"), vmmenu.VMActionUI.reboot),
                action(_("_Shut Down"), vmmenu.VMActionUI.shutdown),
                action(_("F_orce Reset"), vmmenu.VMActionUI.reset),
                action(_("_Force Off"), vmmenu.VMActionUI.destroy),
                action(_("Sa_ve"), vmmenu.VMActionUI.save),
            ),
            "shutdown",
        )
        actions = [
            action(_("_Run"), vmmenu.VMActionUI.run),
            action(_("_Pause"), vmmenu.VMActionUI.suspend),
            action(_("R_esume"), vmmenu.VMActionUI.resume),
            shutdown,
            action(_("Clone..."), vmmenu.VMActionUI.clone),
            action(_("Migrate..."), vmmenu.VMActionUI.migrate),
            action(_("_Delete"), vmmenu.VMActionUI.delete),
            action(_("_Open"), vmmenu.VMActionUI.show),
        ]
        if not states["resume"]:
            actions.pop(2)
        else:
            actions.pop(1)
        return tuple(actions)

    def items(self):
        manager = vmmConnectionManager.get_instance()
        connections = sorted(
            manager.conns.values(),
            key=lambda conn: (not conn.is_active(), conn.get_pretty_desc().casefold()),
        )
        result = []
        for conn in connections:
            uri = conn.get_uri()
            children = [
                (vm.get_name_or_title(), None, True, self._vm_actions(vm), vm.get_uuid())
                for vm in sorted(conn.list_vms(), key=lambda vm: vm.get_name_or_title().casefold())
            ]
            if conn.is_active():
                children.append(
                    (
                        _("_Disconnect").replace("_", "", 1),
                        lambda uri=uri: _disconnect(uri),
                        True,
                        (),
                        "disconnect",
                    )
                )
            else:
                children.append(
                    (
                        _("_Connect").replace("_", "", 1),
                        lambda uri=uri: _connect(uri),
                        True,
                        (),
                        "connect",
                    )
                )
            result.append((conn.get_pretty_desc(), None, True, tuple(children), uri))
        result.append(
            (
                _("_Show Virtual Machine Manager").replace("_", "", 1),
                _toggle_manager,
                True,
                (),
                "manager",
            )
        )
        result.append((_("_Quit").replace("_", "", 1), self._exit_app, True, (), "quit"))
        return result

    def _exit_app(self):
        from .engine import vmmEngine

        vmmEngine.get_instance().exit_app()


class _StatusNotifier:
    """Expose the StatusNotifierItem and DBusMenu protocols over the session bus."""

    def __init__(self, menu):
        self._menu = menu
        self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self._visible = False
        self._watcher = False
        self._is_registered = False
        self._registration = None
        self._revision = 0
        self._nodes = {}
        self._children = {}
        self._node_ids = {}
        self._next_id = 0
        self._item_id = self._bus.register_object(
            _ITEM_PATH, _ITEM_INTERFACE, self._item_method, self._item_property, None
        )
        self._menu_id = self._bus.register_object(
            _MENU_PATH, _MENU_INTERFACE, self._menu_method, self._menu_property, None
        )
        self.refresh()
        self._watch_id = Gio.bus_watch_name(
            Gio.BusType.SESSION,
            _WATCHER,
            Gio.BusNameWatcherFlags.NONE,
            self._watcher_appeared,
            self._watcher_vanished,
        )

    def close(self):
        self._watcher = False
        self._cancel_registration()
        Gio.bus_unwatch_name(self._watch_id)
        self._bus.unregister_object(self._item_id)
        self._bus.unregister_object(self._menu_id)

    def is_embedded(self):
        return self._visible and self._watcher and self._is_registered

    def show(self):
        self._visible = True
        self._status_changed()
        if self._watcher:
            self._register()

    def hide(self):
        self._visible = False
        self._status_changed()

    def _status_changed(self):
        self._bus.emit_signal(
            None,
            _ITEM_PATH,
            _ITEM_INTERFACE.name,
            "NewStatus",
            GLib.Variant("(s)", ("Active" if self._visible else "Passive",)),
        )

    def _watcher_appeared(self, _bus, _name, owner):
        self._cancel_registration()
        self._watcher = owner
        if self._visible:
            self._register()

    def _watcher_vanished(self, _bus, _name):
        self._watcher = False
        self._cancel_registration()

    def _cancel_registration(self):
        if self._registration is not None:
            self._registration.cancel()
            self._registration = None
        self._is_registered = False

    def _register(self):
        if self._registration is not None or self._is_registered:
            return
        self._registration = Gio.Cancellable()
        try:
            self._bus.call(
                self._watcher,
                "/StatusNotifierWatcher",
                _WATCHER,
                "RegisterStatusNotifierItem",
                GLib.Variant("(s)", (_ITEM_PATH,)),
                None,
                Gio.DBusCallFlags.NONE,
                -1,
                self._registration,
                self._registered,
                self._registration,
            )
        except GLib.Error:
            self._registration = None
            log.exception("Could not register system tray icon")

    def _registered(self, bus, result, registration):
        current = registration is self._registration
        if current:
            self._registration = None
        try:
            bus.call_finish(result)
        except GLib.Error as error:
            if current and not error.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED):
                log.exception("Could not register system tray icon")
        else:
            if current:
                self._is_registered = True

    def refresh(self):
        nodes = {0: ("", None, True, (), "root")}
        children = {}
        node_ids = {}

        def add(parent, path, entries):
            ids = []
            for entry in entries:
                key = path + (entry[4],)
                item_id = self._node_ids.get(key)
                if item_id is None:
                    self._next_id += 1
                    item_id = self._next_id
                node_ids[key] = item_id
                nodes[item_id] = entry
                ids.append(item_id)
                if entry[3]:
                    add(item_id, key, entry[3])
            children[parent] = ids

        add(0, (), self._menu.items())
        # Only live identities are retained. Deleted IDs are never reused, so
        # events from a host's old layout cannot target a different action.
        self._nodes = nodes
        self._children = children
        self._node_ids = node_ids
        self._revision += 1
        self._bus.emit_signal(
            None,
            _MENU_PATH,
            _MENU_INTERFACE.name,
            "LayoutUpdated",
            GLib.Variant("(ui)", (self._revision, 0)),
        )

    def _item_property(self, _bus, _sender, _path, _interface, name):
        values = {
            "Category": ("s", "ApplicationStatus"),
            "Id": ("s", "virt-manager"),
            "Title": ("s", _("Virtual Machine Manager")),
            "Status": ("s", "Active" if self._visible else "Passive"),
            "IconName": ("s", "virt-manager"),
            "ToolTip": ("(sa(iiay)ss)", ("virt-manager", [], _("Virtual Machine Manager"), "")),
            "Menu": ("o", _MENU_PATH),
            "ItemIsMenu": ("b", False),
        }
        signature, value = values[name]
        return GLib.Variant(signature, value)

    def _item_method(self, _bus, _sender, _path, _interface, name, _args, reply):
        if name in ("Activate", "SecondaryActivate"):
            _toggle_manager()
        reply.return_value(None)

    def _properties(self, item_id):
        label, _callback, enabled, children, _key = self._nodes[item_id]
        if item_id == 0:
            return {"children-display": GLib.Variant("s", "submenu")}
        props = {"label": GLib.Variant("s", label)}
        if not enabled:
            props["enabled"] = GLib.Variant("b", False)
        if children:
            props["children-display"] = GLib.Variant("s", "submenu")
        return props

    def _layout(self, item_id, depth, names):
        props = self._properties(item_id)
        if names:
            props = {key: value for key, value in props.items() if key in names}
        children = []
        if depth != 0:
            for child_id in self._children.get(item_id, []):
                children.append(
                    GLib.Variant(
                        "(ia{sv}av)", self._layout(child_id, depth - 1 if depth > 0 else -1, names)
                    )
                )
        return (item_id, props, children)

    def _menu_property(self, _bus, _sender, _path, _interface, name):
        values = {
            "Version": ("u", 3),
            "TextDirection": ("s", "ltr"),
            "Status": ("s", "normal"),
            "IconThemePath": ("as", []),
        }
        signature, value = values[name]
        return GLib.Variant(signature, value)

    def _event(self, item_id, event):
        node = self._nodes.get(item_id)
        if node and event == "clicked" and node[2] and node[1]:
            node[1]()

    def _menu_method(self, _bus, _sender, _path, _interface, name, args, reply):
        params = args.unpack()
        if name == "GetLayout":
            item_id, depth, names = params
            if item_id not in self._nodes:
                reply.return_dbus_error("com.canonical.dbusmenu.Error.UnknownItem", str(item_id))
                return
            reply.return_value(
                GLib.Variant("(u(ia{sv}av))", (self._revision, self._layout(item_id, depth, names)))
            )
        elif name == "GetGroupProperties":
            ids, names = params
            result = []
            for item_id in ids:
                if item_id in self._nodes:
                    props = self._properties(item_id)
                    if names:
                        props = {k: v for k, v in props.items() if k in names}
                    result.append((item_id, props))
            reply.return_value(GLib.Variant("(a(ia{sv}))", (result,)))
        elif name == "GetProperty":
            item_id, prop = params
            if item_id not in self._nodes or prop not in self._properties(item_id):
                reply.return_dbus_error("com.canonical.dbusmenu.Error.UnknownProperty", prop)
                return
            reply.return_value(GLib.Variant("(v)", (self._properties(item_id)[prop],)))
        elif name == "Event":
            self._event(params[0], params[1])
            reply.return_value(None)
        elif name == "EventGroup":
            for item_id, event, _data, _timestamp in params[0]:
                self._event(item_id, event)
            reply.return_value(GLib.Variant("(ai)", ([],)))
        elif name == "AboutToShow":
            reply.return_value(GLib.Variant("(b)", (False,)))
        elif name == "AboutToShowGroup":
            reply.return_value(GLib.Variant("(aiai)", ([], [])))


class vmmSystray(vmmGObject):
    """Keep the application resident and expose VM actions via SNI/DBusMenu."""

    @classmethod
    def get_instance(cls):
        if not cls._instance:
            cls._instance = cls()
        return cls._instance

    @staticmethod
    def systray_disabled_message():
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            result = bus.call_sync(
                "org.freedesktop.DBus",
                "/org/freedesktop/DBus",
                "org.freedesktop.DBus",
                "NameHasOwner",
                GLib.Variant("(s)", (_WATCHER,)),
                GLib.VariantType.new("(b)"),
                Gio.DBusCallFlags.NONE,
                -1,
                None,
            )
            if result.unpack()[0]:
                return None
        except GLib.Error:
            log.debug("Could not query system tray watcher", exc_info=True)
        return _("No StatusNotifierItem system tray watcher is available")

    def __init__(self):
        super().__init__()
        self._cleanup_on_app_close()
        self._tray = None
        self._menu = _TrayMenu(self._menu_changed)
        self.add_gsettings_handle(
            self.config.on_view_system_tray_changed(self._show_systray_changed_cb)
        )
        self._show_systray_changed_cb()

    def _cleanup(self):
        if self._tray:
            self._tray.close()
        self._menu.cleanup()
        self._tray = None
        self._menu = None

    def _menu_changed(self):
        if self._tray:
            self._tray.refresh()

    def is_embedded(self):
        return bool(self._tray and self._tray.is_embedded())

    def _ensure_tray(self):
        if not self._tray:
            try:
                self._tray = _StatusNotifier(self._menu)
            except GLib.Error:
                log.exception("Could not connect to the system tray session bus")
                return False
        return True

    def show_from_cli(self):
        if self._ensure_tray():
            self._tray.show()

    def _show_systray_changed_cb(self):
        if self.config.get_view_system_tray():
            self.show_from_cli()
        elif self._tray:
            self._tray.hide()
