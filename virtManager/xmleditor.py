# This work is licensed under the GNU GPLv2 or later.
# See the COPYING file in the top-level directory.

# pylint: disable=wrong-import-order,ungrouped-imports
import os

import gi

from virtinst import log

from gi.repository import Gtk

# GtkSource 5 is the native GTK4 source view. A plain TextView remains usable
# when syntax highlighting support is not installed.
have_gtksourceview = True
try:
    gi.require_version("GtkSource", "5")
except ValueError:
    log.debug("Not using GtkSource 5")
    have_gtksourceview = False

if "VIRTINST_TEST_SUITE_FAKE_NO_SOURCEVIEW" in os.environ:
    log.debug("Faking missing GtkSource for test suite")
    have_gtksourceview = False

if have_gtksourceview:
    from gi.repository import GtkSource

from .lib import uiutil
from .baseclass import vmmGObjectUI

_PAGE_DETAILS = 0
_PAGE_XML = 1


class vmmXMLEditor(vmmGObjectUI):
    __gsignals__ = {
        "changed": (vmmGObjectUI.RUN_FIRST, None, []),
        "xml-requested": (vmmGObjectUI.RUN_FIRST, None, []),
        "xml-reset": (vmmGObjectUI.RUN_FIRST, None, []),
    }

    def __init__(self, builder, topwin, parent_container, details_widget):
        super().__init__("xmleditor.ui", None, builder=builder, topwin=topwin)

        parent_container.remove(details_widget)
        parent_container.append(self.widget("xml-notebook"))
        self.widget("xml-details-box").append(details_widget)

        self._curpage = _PAGE_DETAILS
        self._srcxml = ""
        self._srcview = None
        self._srcbuff = None
        self._theme_settings = None
        self._theme_handles = []
        self._init_ui()

        self.details_changed = False

        self.add_gsettings_handle(
            self.config.on_xmleditor_enabled_changed(self._xmleditor_enabled_changed_cb)
        )

    def _cleanup(self):
        for handle in self._theme_handles:
            self._theme_settings.disconnect(handle)
        self._theme_handles.clear()
        self._theme_settings = None
        self._srcview.unparent()
        self._srcbuff = None

    ###########
    # UI init #
    ###########

    def _set_xmleditor_enabled_from_config(self):
        enabled = self.config.get_xmleditor_enabled()
        self._srcview.set_editable(enabled)
        uiutil.set_grid_row_visible(self.widget("xml-warning-box"), not enabled)

    def _init_ui(self):
        if have_gtksourceview:
            self._srcview = GtkSource.View()
            self._srcbuff = self._srcview.get_buffer()
            self._srcview.set_auto_indent(True)
            lang = GtkSource.LanguageManager.get_default().get_language("xml")
            self._srcbuff.set_language(lang)
            self._srcbuff.set_highlight_syntax(True)
            self._theme_settings = self._srcview.get_settings()
            for prop in (
                "gtk-theme-name",
                "gtk-application-prefer-dark-theme",
                "gtk-interface-color-scheme",
            ):
                if self._theme_settings.find_property(prop):
                    self._theme_handles.append(
                        self._theme_settings.connect("notify::" + prop, self._update_style_scheme)
                    )
            self._update_style_scheme()
        else:
            self._srcview = Gtk.TextView()
            self._srcbuff = self._srcview.get_buffer()

        self._srcview.set_monospace(True)
        self._srcview.update_property([Gtk.AccessibleProperty.LABEL], ["XML editor"])

        self._srcbuff.connect("changed", self._buffer_changed_cb)

        self.widget("xml-notebook").connect("switch-page", self._before_page_changed_cb)
        self.widget("xml-notebook").connect("notify::page", self._after_page_changed_cb)

        self.widget("xml-scroll").set_child(self._srcview)
        self._set_xmleditor_enabled_from_config()

    ####################
    # Internal helpers #
    ####################


    def _update_style_scheme(self, *_args):
        settings = self._theme_settings
        theme = os.environ.get("GTK_THEME") or settings.get_property("gtk-theme-name")
        dark = settings.get_property("gtk-application-prefer-dark-theme") or theme.lower().endswith(
            ("-dark", ":dark")
        )
        if settings.find_property("gtk-interface-color-scheme"):
            dark = dark or (
                settings.get_property("gtk-interface-color-scheme") == Gtk.InterfaceColorScheme.DARK
            )
        manager = GtkSource.StyleSchemeManager.get_default()
        self._srcbuff.set_style_scheme(manager.get_scheme("Adwaita-dark" if dark else "Adwaita"))


    def _reselect_page(self, pagenum):
        # Setting _curpage first will shortcircuit our page changed callback
        self._curpage = pagenum
        self.widget("xml-notebook").set_current_page(pagenum)

    def _reset_xml(self):
        self.set_xml("")
        self.emit("xml-reset")

    def _reset_cursor(self):
        # Put cursor at the start of the second line. Starting on the
        # first means XML open/close tags are highlighted which is weird
        # starting visual
        startiter = self._srcbuff.get_start_iter()
        startiter.forward_line()
        self._srcbuff.place_cursor(startiter)

    def _detials_unapplied_changes(self):
        if not self.details_changed:
            return False

        ret = self.err.yes_no(
            _("There are unapplied changes."),
            _("Your changes will be lost if you leave this tab. Really leave this tab?"),
        )
        if ret:
            self.details_changed = False

        return not ret

    def _xml_unapplied_changes(self):
        if self._srcxml == self.get_xml():
            return False

        ret = self.err.yes_no(
            _("There are unapplied changes."),
            _("Your XML changes will be lost if you leave this tab. Really leave this tab?"),
        )

        return not ret

    ##############
    # Public API #
    ##############

    def reset_state(self):
        """
        Clear XML and select the details page. Used when callers do
        their own reset_state
        """
        self._reset_xml()
        return self.widget("xml-notebook").set_current_page(_PAGE_DETAILS)

    def get_xml(self):
        """
        Return the XML from the editor UI
        """
        return self._srcbuff.get_property("text")

    def set_xml(self, xml):
        """
        Set the editor UI XML to the passed string
        """
        try:
            self._srcbuff.disconnect_by_func(self._buffer_changed_cb)
            self._srcxml = xml or ""
            self._srcbuff.set_text(self._srcxml)
            self._reset_cursor()
        finally:
            self._srcbuff.connect("changed", self._buffer_changed_cb)

    def set_xml_from_libvirtobject(self, libvirtobject):
        """
        Set the editor UI XML to the inactive XML from the passed
        vmmLibvirtObject. If the XML UI isn't visible, we don't set
        anything, which lets callers use this on every page refresh
        """
        if not self.is_xml_selected():
            return
        xml = ""
        if libvirtobject:
            xml = libvirtobject.get_xml_to_define()
        self.set_xml(xml)

    def is_xml_selected(self):
        """
        Return True if the XML page is selected
        """
        return self._curpage == _PAGE_XML

    #############
    # Listeners #
    #############

    def _buffer_changed_cb(self, buf):
        self.emit("changed")

    def _before_page_changed_cb(self, notebook, widget, pagenum):
        if self._curpage == pagenum:
            return
        prevpage = self._curpage
        self._curpage = pagenum

        if pagenum == _PAGE_XML:
            if not self._detials_unapplied_changes():
                # If the XML page is clicked, emit xml-requested signal which
                # expects the user to call set_xml/set_libvirtobject. This saves
                # having to fetch inactive XML up front, and gives users like
                # a hook to actually serialize the final XML to return
                self.emit("xml-requested")
                return
        else:
            if not self._xml_unapplied_changes():
                self._reset_xml()
                return

        # I can't find anyway to make the notebook stay on the current page
        # So set an idle callback to switch back to the XML page. It causes
        # a visual UI blip unfortunately
        self.idle_add(self._reselect_page, prevpage)

    def _after_page_changed_cb(self, notebook, gparam):
        self._curpage = notebook.get_current_page()

    def _xmleditor_enabled_changed_cb(self):
        self._set_xmleditor_enabled_from_config()
