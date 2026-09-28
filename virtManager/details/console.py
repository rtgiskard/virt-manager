# Copyright (C) 2006-2008, 2015 Red Hat, Inc.
# Copyright (C) 2006 Daniel P. Berrange <berrange@redhat.com>
# Copyright (C) 2010 Marc-Andre Lureau <marcandre.lureau@redhat.com>
#
# This work is licensed under the GNU GPLv2 or later.
# See the COPYING file in the top-level directory.

from gi.repository import Gtk

from virtinst import log

from .serialcon import vmmSerialConsole
from .sshtunnels import ConnectionInfo
from .viewers import SpiceViewer, SPICE_GTK_IMPORT_ERROR
from ..baseclass import vmmGObject, vmmGObjectUI
from ..lib.keyring import vmmKeyring


# console-pages IDs
(
    _CONSOLE_PAGE_UNAVAILABLE,
    _CONSOLE_PAGE_SERIAL,
    _CONSOLE_PAGE_GRAPHICS,
    _CONSOLE_PAGE_CONNECT,
) = range(4)

# console-gfx-pages IDs
(_GFX_PAGE_VIEWER, _GFX_PAGE_AUTH, _GFX_PAGE_UNAVAILABLE) = range(3)


class _TimedRevealer(vmmGObject):
    """
    Revealer for the fullscreen toolbar, with a bit of extra logic to
    hide/show based on mouse over
    """

    def __init__(self, toolbar):
        vmmGObject.__init__(self)

        self._in_fullscreen = False
        self._timeout_id = None

        self._revealer = Gtk.Revealer()
        self._revealer.set_child(toolbar)

        # Keep a pointer target visible even while the toolbar is concealed.
        self._ebox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._ebox.set_size_request(1, 1)
        self._ebox.append(self._revealer)
        self._ebox.set_halign(Gtk.Align.CENTER)
        self._ebox.set_valign(Gtk.Align.START)

        motion = Gtk.EventControllerMotion()
        motion.connect("enter", self._enter_notify)
        motion.connect("leave", self._leave_notify)
        self._ebox.add_controller(motion)

    def _cleanup(self):
        self._ebox.unparent()
        self._ebox = None
        self._revealer = None
        self._timeout_id = None

    def _enter_notify(self, *_args):
        if not self._in_fullscreen:
            return
        self._unregister_timeout()
        self._revealer.set_reveal_child(True)

    def _leave_notify(self, *_args):
        if self._in_fullscreen and self._revealer.get_reveal_child():
            self._schedule_unreveal_timeout(1000)

    def _schedule_unreveal_timeout(self, timeout):
        if self._timeout_id:
            return  # pragma: no cover

        def cb():
            self._revealer.set_reveal_child(False)
            self._timeout_id = None

        self._timeout_id = self.timeout_add(timeout, cb)

    def _unregister_timeout(self):
        if self._timeout_id:  # pragma: no cover
            self.remove_gobject_timeout(self._timeout_id)
            self._timeout_id = None

    def force_reveal(self, val):
        self._unregister_timeout()
        self._in_fullscreen = val
        self._revealer.set_reveal_child(val)
        self._schedule_unreveal_timeout(2000)

    def get_overlay_widget(self):
        return self._ebox


def build_keycombo_menu(on_send_key_fn):
    menu = Gtk.Popover()
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    menu.set_child(box)

    def make_item(accel, combo):
        _valid, keyval, modifiers = Gtk.accelerator_parse(accel)
        name = Gtk.accelerator_get_label(keyval, modifiers)
        item = Gtk.Button(label=name)
        item.set_has_frame(False)

        def send_key(button):
            menu.popdown()
            on_send_key_fn(button, combo)

        item.connect("clicked", send_key)
        box.append(item)

    make_item("<Control><Alt>BackSpace", ["Control_L", "Alt_L", "BackSpace"])
    make_item("<Control><Alt>Delete", ["Control_L", "Alt_L", "Delete"])
    make_item("<Control><Alt><Shift>Escape", ["Control_L", "Alt_L", "Shift_L", "Escape"])
    box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

    for i in range(1, 13):
        make_item("<Control><Alt>F%d" % i, ["Control_L", "Alt_L", "F%d" % i])
    box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
    make_item("Print", ["Print"])

    return menu


class vmmOverlayToolbar:
    def __init__(self, on_leave_fn, on_send_key_fn):
        self._keycombo_menu = None
        self._toolbar = None
        self.timed_revealer = None
        self._init_ui(on_leave_fn, on_send_key_fn)

    def _init_ui(self, on_leave_fn, on_send_key_fn):
        self._keycombo_menu = build_keycombo_menu(on_send_key_fn)

        self._toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self._toolbar.add_css_class("toolbar")
        self._toolbar.update_property([Gtk.AccessibleProperty.LABEL], ["Fullscreen Toolbar"])

        button = Gtk.Button(label=_("Leave Fullscreen"), icon_name="view-restore")
        button.set_tooltip_text(_("Leave fullscreen"))
        button.update_property([Gtk.AccessibleProperty.LABEL], ["Fullscreen Exit"])
        self._toolbar.append(button)
        button.connect("clicked", on_leave_fn)

        self._send_key_button = Gtk.MenuButton(icon_name="preferences-desktop-keyboard-shortcuts")
        self._send_key_button.set_tooltip_text(_("Send key combination"))
        self._send_key_button.set_popover(self._keycombo_menu)
        self._send_key_button.update_property(
            [Gtk.AccessibleProperty.LABEL], ["Fullscreen Send Key"]
        )
        self._toolbar.append(self._send_key_button)

        self.timed_revealer = _TimedRevealer(self._toolbar)

    def cleanup(self):
        self._send_key_button.set_popover(None)
        self._keycombo_menu = None
        self.timed_revealer.cleanup()
        self.timed_revealer = None
        self._toolbar = None


def _cant_embed_graphics(ginfo):
    if ginfo.gtype == "spice":
        return
    if ginfo.gtype == "vnc":
        return _("VNC graphical consoles are not supported by the GTK 4 viewer.")

    msg = _("Cannot display graphical console type '%s'") % ginfo.gtype
    return msg


class _ConsoleMenu(vmmGObject):
    """
    Helper class for building the text/graphical console menu list
    """

    def __init__(self, show_cb, toggled_cb):
        vmmGObject.__init__(self)
        self._menu = Gtk.Popover()
        self._box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._menu.set_child(self._box)
        self._menu.connect("show", show_cb)
        self._toggled_cb = toggled_cb
        self._items = []

    def _cleanup(self):
        self._menu.unparent()
        self._menu = None
        self._toggled_cb = None

    ################
    # Internal API #
    ################

    def _build_serial_menu_items(self, vm):
        ret = []
        for dev in vmmSerialConsole.get_serialcon_devices(vm):
            if dev.DEVICE_TYPE == "console":
                label = _("Text Console %d") % (dev.get_xml_idx() + 1)
            else:
                label = _("Serial %d") % (dev.get_xml_idx() + 1)

            tooltip = vmmSerialConsole.can_connect(vm, dev)
            ret.append([label, dev, tooltip])

        if not ret:
            ret = [[_("No text console available"), None, None]]
        return ret

    def _build_graphical_menu_items(self, vm):

        from ..device.gfxdetails import vmmGraphicsDetails

        ret = []
        found_default = False
        for gdev in vm.xmlobj.devices.graphics:
            idx = gdev.get_xml_idx()
            ginfo = ConnectionInfo(vm.conn, gdev)

            label = (
                _("Graphical Console")
                + " "
                + vmmGraphicsDetails.graphics_pretty_type_simple(gdev.type)
            )
            if idx > 0:
                label += " %s" % (idx + 1)

            tooltip = _cant_embed_graphics(ginfo)
            if not tooltip:
                if not found_default:
                    found_default = True
                else:
                    tooltip = _("virt-manager does not support more than one graphical console")

            ret.append([label, ginfo, tooltip])

        if not ret:
            ret = [[_("Graphical console not configured for guest"), None, None]]
        return ret

    def _get_selected_menu_item(self):
        for child in self._items:
            if child.get_active():
                return child

    ##############
    # Public API #
    ##############

    def rebuild_menu(self, vm):
        olditem = self._get_selected_menu_item()
        oldlabel = olditem.get_label() if olditem else None

        while child := self._box.get_first_child():
            self._box.remove(child)
        self._items = []

        graphics = self._build_graphical_menu_items(vm)
        serials = self._build_serial_menu_items(vm)
        items = graphics + [[None, None, None]] + serials

        last_item = None
        for label, dev, tooltip in items:
            if label is None:
                self._box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
                continue

            sensitive = bool(dev and not tooltip)
            if not sensitive and not tooltip:
                tooltip = label

            if oldlabel is None and sensitive:
                oldlabel = label

            item = Gtk.CheckButton(label=label)
            if last_item is not None:
                item.set_group(last_item)
            last_item = item

            item.set_active(label == oldlabel and sensitive)
            item.set_sensitive(sensitive)
            item.set_tooltip_text(tooltip or None)
            item.vmm_data = dev
            if sensitive:
                item.connect("toggled", self._on_toggled)
            self._box.append(item)
            self._items.append(item)

    def _on_toggled(self, item):
        if item.get_active():
            self._menu.popdown()
            self._toggled_cb(item)

    def activate_default(self):
        for child in self._items:
            if child.get_sensitive():
                if child.get_active():
                    self._toggled_cb(child)
                else:
                    child.set_active(True)
                return True
        return False

    def get_selected(self):
        row = self._get_selected_menu_item()
        if not row:
            row = self._items[0]
        return row.get_label(), row.vmm_data, row.get_tooltip_text()

    def get_menu(self):
        return self._menu


class vmmConsolePages(vmmGObjectUI):
    """Handles graphical SPICE and text console pages."""

    __gsignals__ = {
        "page-changed": (vmmGObjectUI.RUN_FIRST, None, []),
        "leave-fullscreen": (vmmGObjectUI.RUN_FIRST, None, []),
        "change-title": (vmmGObjectUI.RUN_FIRST, None, []),
    }

    def __init__(self, vm, builder, topwin):
        vmmGObjectUI.__init__(self, "console.ui", None, builder=builder, topwin=topwin)

        self.vm = vm
        self.top_box = self.widget("console-pages")
        self._pointer_is_grabbed = False


        # Initialize display widget
        self._viewer = None
        self._viewer_connect_clicked = False
        self._in_fullscreen = False

        # Fullscreen toolbar
        self._keycombo_menu = build_keycombo_menu(self._do_send_key)

        self._overlay_toolbar_fullscreen = vmmOverlayToolbar(
            on_leave_fn=self._leave_fullscreen, on_send_key_fn=self._do_send_key
        )
        self.widget("console-overlay").add_overlay(
            self._overlay_toolbar_fullscreen.timed_revealer.get_overlay_widget()
        )
        # The viewport background shows around the viewer in scaling mode.
        self.widget("console-gfx-viewport").add_css_class("vmm-console-background")

        self.widget("console-pages").set_show_tabs(False)
        self.widget("serial-pages").set_show_tabs(False)
        self.widget("console-gfx-pages").set_show_tabs(False)

        self._consolemenu = _ConsoleMenu(
            self._on_console_menu_show_cb, self._on_console_menu_toggled_cb
        )
        self._serial_consoles = []

        # Signals are added by vmmVMWindow. Don't use connect_signals here
        # or it changes will be overwritten

        self.connect_signals({
            "on_console_pages_switch_page": self._page_changed_cb,
            "on_console_auth_password_activate": self._auth_login_cb,
            "on_console_auth_login_clicked": self._auth_login_cb,
            "on_console_connect_button_clicked": self._connect_button_clicked_cb,
        })

        self.widget("console-gfx-pages").connect("switch-page", self._page_changed_cb)

    def _cleanup(self):
        self.vm = None

        if self._viewer:
            self._viewer.cleanup()  # pragma: no cover
        self._viewer = None

        self._overlay_toolbar_fullscreen.cleanup()

        for serial in self._serial_consoles:
            serial.cleanup()
        self._serial_consoles = []

        self._consolemenu.cleanup()
        self._consolemenu = None

    #################
    # Internal APIs #
    #################

    def _do_send_key(self, src, keys):
        ignore = src

        if keys is not None:
            self._viewer.console_send_keys(keys)


    ###########################
    # Resize and scaling APIs #
    ###########################

    def _viewer_get_resizeguest_tooltip(self):
        tooltip = ""
        if self._viewer:
            tooltip = self._viewer.console_get_resizeguest_warning()
        return tooltip or ""

    def _sync_resizeguest_with_display(self):
        if not self._viewer:
            return

        val = bool(self.vm.get_console_resizeguest())
        self._viewer.console_set_resizeguest(val)

    def _set_size_to_vm(self):
        if not self._viewer_is_visible():
            return  # pragma: no cover

        w, h = self._viewer.console_get_preferred_size()
        if w <= 0 or h <= 0:  # pragma: no cover
            log.debug("_set_size_to_vm but no valid sizing found")
            return

        top_w, top_h = self.topwin.get_width(), self.topwin.get_height()
        viewer_alloc = self.widget("console-gfx-scroll").get_allocation()

        valw = w + (top_w - viewer_alloc.width)
        valh = h + (top_h - viewer_alloc.height)

        log.debug("_set_size_to_vm vm=(%s, %s) window=(%s, %s)", w, h, valw, valh)
        self.topwin.unmaximize()
        self.topwin.set_default_size(valw, valh)

    ################
    # Scaling APIs #
    ################

    def _sync_scaling_with_display(self):
        if not self._viewer:
            return

        scale_type = self.vm.get_console_scaling()

        if scale_type == self.config.CONSOLE_SCALE_NEVER:
            self._viewer.console_set_scaling(False)
        elif scale_type == self.config.CONSOLE_SCALE_ALWAYS:
            self._viewer.console_set_scaling(True)
        elif scale_type == self.config.CONSOLE_SCALE_FULLSCREEN:
            self._viewer.console_set_scaling(self._in_fullscreen)

    ###################
    # Fullscreen APIs #
    ###################

    def _leave_fullscreen(self, ignore=None):
        self.emit("leave-fullscreen")

    def _change_fullscreen(self, do_fullscreen):
        if do_fullscreen:
            self._in_fullscreen = True
            self.topwin.fullscreen()
            self._overlay_toolbar_fullscreen.timed_revealer.force_reveal(True)
        else:
            self._in_fullscreen = False
            self._overlay_toolbar_fullscreen.timed_revealer.force_reveal(False)
            self.topwin.unfullscreen()

        self._sync_scaling_with_display()

    ##########################
    # State tracking methods #
    ##########################

    def _show_vm_status_unavailable(self):
        if self.vm.is_crashed():  # pragma: no cover
            self._activate_vm_unavailable_page(_("Guest has crashed."))
        else:
            self._activate_vm_unavailable_page(_("Guest is not running."))

    def _close_viewer(self):
        self._leave_fullscreen()
        self._viewer_connect_clicked = False

        for serial in self._serial_consoles:
            serial.close()

        if self._viewer is None:
            return
        self._viewer.console_remove_display_from_widget(self.widget("console-gfx-viewport"))
        self._viewer.cleanup()
        self._viewer = None
        log.debug("Viewer object cleaned up")

    def _refresh_vm_state(self):
        self._activate_default_console_page()

    ###########################
    # console page navigation #
    ###########################

    def _activate_gfx_unavailable_page(self, msg):
        self._close_viewer()
        self.widget("console-gfx-pages").set_current_page(_GFX_PAGE_UNAVAILABLE)
        if msg:
            self.widget("console-gfx-unavailable").set_label("<b>" + msg + "</b>")

    def _activate_vm_unavailable_page(self, msg):
        """
        This is the top level error page. We should only set it for very
        specific error cases, because when it is set and the VM is running
        we take that to mean we should attempt to connect to the default
        console.
        """
        self._close_viewer()
        self.widget("console-pages").set_current_page(_CONSOLE_PAGE_UNAVAILABLE)
        if msg:
            self.widget("console-unavailable").set_label("<b>" + msg + "</b>")
        self._activate_gfx_unavailable_page(msg)

    def _activate_auth_page(self, withPassword, withUsername):
        (pw, username) = vmmKeyring.get_instance().get_console_password(self.vm)

        self.widget("console-auth-password").set_visible(withPassword)
        self.widget("label-auth-password").set_visible(withPassword)

        self.widget("console-auth-username").set_visible(withUsername)
        self.widget("label-auth-username").set_visible(withUsername)

        self.widget("console-auth-username").set_text(username)
        self.widget("console-auth-password").set_text(pw)

        has_keyring = vmmKeyring.get_instance().is_available()
        remember = bool(withPassword and pw) or (withUsername and username)
        remember = has_keyring and remember
        self.widget("console-auth-remember").set_sensitive(has_keyring)
        self.widget("console-auth-remember").set_active(remember)

        self.widget("console-gfx-pages").set_current_page(_GFX_PAGE_AUTH)

        if withUsername:
            self.widget("console-auth-username").grab_focus()
        else:
            self.widget("console-auth-password").grab_focus()

    def _activate_gfx_viewer_page(self):
        self.widget("console-pages").set_current_page(_CONSOLE_PAGE_GRAPHICS)
        self.widget("console-gfx-pages").set_current_page(_GFX_PAGE_VIEWER)
        if self._viewer:
            self._viewer.console_grab_focus()

    def _activate_console_connect_page(self):
        self.widget("console-pages").set_current_page(_CONSOLE_PAGE_CONNECT)

    def _viewer_is_visible(self):
        is_visible = self.widget("console-pages").is_visible()
        cpage = self.widget("console-pages").get_current_page()
        gpage = self.widget("console-gfx-pages").get_current_page()

        return bool(
            is_visible
            and cpage == _CONSOLE_PAGE_GRAPHICS
            and gpage == _GFX_PAGE_VIEWER
            and self._viewer
            and self._viewer.console_is_open()
        )

    def _viewer_can_usb_redirect(self):
        return self._viewer_is_visible() and self._viewer.console_has_usb_redirection()

    #########################
    # Viewer login attempts #
    #########################

    def _init_viewer(self, ginfo, errmsg):
        if self._viewer or not self.is_visible():
            return

        if errmsg:
            log.debug("No acceptable graphics to connect to")
            self._activate_gfx_unavailable_page(errmsg)
            return

        if not self.vm.get_console_autoconnect() and not self._viewer_connect_clicked:
            self._activate_console_connect_page()
            return

        self._activate_gfx_unavailable_page(_("Connecting to graphical console for guest"))

        log.debug("Starting connect process for %s", ginfo.logstring())
        try:
            if SPICE_GTK_IMPORT_ERROR:
                raise RuntimeError("Error opening SPICE console: %s" % SPICE_GTK_IMPORT_ERROR)
            self._viewer = SpiceViewer(self.vm, ginfo)

            self._connect_viewer_signals()

            self._viewer.console_open()
        except Exception as e:
            log.exception("Error connecting to graphical console")
            self._activate_gfx_unavailable_page(_("Error connecting to graphical console:\n%s") % e)

    def _set_credentials(self, src_ignore=None):
        passwd = self.widget("console-auth-password")
        username = self.widget("console-auth-username")

        if passwd.get_visible():
            self._viewer.console_set_password(passwd.get_text())
        if username.get_visible():
            self._viewer.console_set_username(username.get_text())

        if self.widget("console-auth-remember").get_active():
            vmmKeyring.get_instance().set_console_password(
                self.vm, passwd.get_text(), username.get_text()
            )
        else:
            vmmKeyring.get_instance().del_console_password(self.vm)

    ##########################
    # Viewer signal handling #
    ##########################

    def _viewer_add_display_cb(self, _src, display):
        self.widget("console-gfx-viewport").set_child(display)

        # Sync initial settings
        self._sync_scaling_with_display()
        self._sync_resizeguest_with_display()

    def _pointer_grabbed_cb(self, _src):
        self._pointer_is_grabbed = True
        self.emit("change-title")

    def _pointer_ungrabbed_cb(self, _src):
        self._pointer_is_grabbed = False
        self.emit("change-title")

    def _viewer_auth_error_cb(self, _src, errmsg, viewer_will_disconnect):
        errmsg = _("Viewer authentication error: %s") % errmsg
        self.err.val_err(errmsg)

        if viewer_will_disconnect:
            self._activate_vm_unavailable_page(errmsg)

        self._refresh_vm_state()


    def _viewer_need_auth_cb(self, _src, withPassword, withUsername):
        self._activate_auth_page(withPassword, withUsername)

    def _viewer_agent_connected_cb(self, _src):
        # Tell the vmwindow to trigger a state refresh, since
        # resizeguest setting depends on the agent value
        if self.widget("console-pages").is_visible():  # pragma: no cover
            self.emit("page-changed")

    def _viewer_usb_redirect_error_cb(self, _src, errstr):
        self.err.show_err(
            _("USB redirection error"), text2=str(errstr), modal=True
        )  # pragma: no cover

    def _viewer_disconnected_set_page(self, errdetails, ssherr):
        if self.vm.is_runable():  # pragma: no cover
            # Exit was probably for legitimate reasons
            self._show_vm_status_unavailable()
            return

        msg = _("Viewer was disconnected.")
        errmsg = ""
        if errdetails:
            errmsg += "\n" + errdetails
        if ssherr:
            log.debug("SSH tunnel error output: %s", ssherr)
            errmsg += "\n\n"
            errmsg += _("SSH tunnel error output: %s") % ssherr

        if errmsg:
            self._activate_gfx_unavailable_page(msg + errmsg)
            return

        # If no error message was reported, this isn't a clear graphics
        # error that should block reconnecting. So use the top level
        # 'VM unavailable' page which makes it easier for the user to
        # reconnect.
        self._activate_vm_unavailable_page(msg)

    def _viewer_disconnected_cb(self, _src, errdetails, ssherr):
        self._activate_gfx_unavailable_page(_("Viewer is disconnecting."))
        log.debug("Viewer disconnected cb")

        self._viewer_disconnected_set_page(errdetails, ssherr)

    def _viewer_connected_cb(self, _src):
        log.debug("Viewer connected cb")
        self._activate_gfx_viewer_page()

    def _connect_viewer_signals(self):
        self._viewer.connect("add-display-widget", self._viewer_add_display_cb)
        self._viewer.connect("pointer-grab", self._pointer_grabbed_cb)
        self._viewer.connect("pointer-ungrab", self._pointer_ungrabbed_cb)
        self._viewer.connect("connected", self._viewer_connected_cb)
        self._viewer.connect("disconnected", self._viewer_disconnected_cb)
        self._viewer.connect("auth-error", self._viewer_auth_error_cb)
        self._viewer.connect("need-auth", self._viewer_need_auth_cb)
        self._viewer.connect("agent-connected", self._viewer_agent_connected_cb)
        self._viewer.connect("usb-redirect-error", self._viewer_usb_redirect_error_cb)

    ##############################
    # Console list menu handling #
    ##############################

    def _console_menu_view_selected(self):
        name, dev, errmsg = self._consolemenu.get_selected()
        is_graphics = hasattr(dev, "gtype")

        if self.vm.is_runable():
            self._show_vm_status_unavailable()
            return

        if errmsg or not dev or is_graphics:
            self.widget("console-pages").set_current_page(_CONSOLE_PAGE_GRAPHICS)
            self.idle_add(self._init_viewer, dev, errmsg)
            return

        target_port = dev.get_xml_idx()
        serial = None
        for s in self._serial_consoles:
            if s.name == name:
                serial = s
                break

        if not serial:
            serial = vmmSerialConsole(self.vm, target_port, name)

            title = Gtk.Label(label=name)
            self.widget("serial-pages").append_page(serial.get_box(), title)
            self._serial_consoles.append(serial)

        if not self.vm.get_console_autoconnect() and not self._viewer_connect_clicked:
            self._activate_console_connect_page()
            return

        serial.open_console()
        page_idx = self._serial_consoles.index(serial)
        self.widget("console-pages").set_current_page(_CONSOLE_PAGE_SERIAL)
        self.widget("serial-pages").set_current_page(page_idx)

    def _populate_console_menu(self):
        self._consolemenu.rebuild_menu(self.vm)

    def _toggle_first_console_menu_item(self):
        # We iterate through the 'console' menu and activate the first
        # valid entry... hacky but it works
        self._populate_console_menu()
        found = self._consolemenu.activate_default()
        if not found:
            # Calling this with dev=None will trigger _init_viewer
            # which shows some meaningful errors
            self._console_menu_view_selected()

    def _activate_default_console_page(self):
        if self.vm.is_runable():
            self._show_vm_status_unavailable()
            return

        viewer_initialized = self._viewer and self._viewer.console_is_open()
        if viewer_initialized:
            return

        cpage = self.widget("console-pages").get_current_page()
        if cpage != _CONSOLE_PAGE_UNAVAILABLE:
            return

        # If we are in this condition it should mean the VM was
        # just started, so connect to the default page
        self._toggle_first_console_menu_item()

    def _on_console_menu_toggled_cb(self, src):
        self._console_menu_view_selected()

    def _on_console_menu_show_cb(self, src):
        self._populate_console_menu()

    ################
    # UI listeners #
    ################

    def _auth_login_cb(self, src):
        self._set_credentials()

    def _connect_button_clicked_cb(self, src):
        self._viewer_connect_clicked = True
        self._console_menu_view_selected()

    def _page_changed_cb(self, src, origpage, newpage):
        # Hide the contents of all other pages, so they don't screw
        # up window sizing
        for i in range(src.get_n_pages()):
            src.get_nth_page(i).set_visible(i == newpage)

        # Dispatch the next bit in idle_add, so the UI size can change
        self.idle_emit("page-changed")

    ###########################
    # API used by vmmVMWindow #
    ###########################

    def vmwindow_viewer_can_usb_redirect(self):
        return self._viewer_can_usb_redirect()

    def vmwindow_viewer_get_usb_widget(self):
        return self._viewer.console_get_usb_widget()

    def vmwindow_viewer_get_pixbuf(self):
        return self._viewer.console_get_pixbuf()

    def vmwindow_close(self):
        return self._activate_vm_unavailable_page(_("Viewer window closed."))

    def vmwindow_get_title_message(self):
        if self._pointer_is_grabbed and self._viewer:
            keystr = self._viewer.console_get_grab_keys()
            return _("Press %s to release pointer.") % keystr

    def vmwindow_activate_default_console_page(self):
        return self._activate_default_console_page()

    def vmwindow_refresh_vm_state(self):
        return self._refresh_vm_state()

    def vmwindow_set_size_to_vm(self):
        return self._set_size_to_vm()

    def vmwindow_set_fullscreen(self, do_fullscreen):
        self._change_fullscreen(do_fullscreen)

    def vmwindow_get_keycombo_menu(self):
        return self._keycombo_menu

    def vmwindow_get_console_list_menu(self):
        return self._consolemenu.get_menu()

    def vmwindow_get_viewer_is_visible(self):
        return self._viewer_is_visible()


    def vmwindow_has_keyboard_grab(self):
        return bool(self._viewer and self._viewer.console_has_keyboard_grab())

    def vmwindow_get_resizeguest_tooltip(self):
        return self._viewer_get_resizeguest_tooltip()

    def vmwindow_sync_scaling_with_display(self):
        return self._sync_scaling_with_display()

    def vmwindow_sync_resizeguest_with_display(self):
        return self._sync_resizeguest_with_display()
