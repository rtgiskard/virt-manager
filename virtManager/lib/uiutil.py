# Copyright (C) 2009, 2013, 2014 Red Hat, Inc.
# Copyright (C) 2009 Cole Robinson <crobinso@redhat.com>
#
# This work is licensed under the GNU GPLv2 or later.
# See the COPYING file in the top-level directory.

from gi.repository import Gdk, Graphene, Gtk

from virtinst import xmlutil


#####################
# UI getter helpers #
#####################


def spin_get_helper(widget):
    """
    Safely get spin button contents, converting to int if possible
    """
    adj = widget.get_adjustment()
    txt = widget.get_text()

    try:
        return int(txt)
    except Exception:
        return adj.get_value()


def get_list_selected_row(widget, check_visible=False):
    """
    Helper to simplify getting the selected row in a list/tree/combo
    """
    if check_visible and not widget.get_visible():
        return None

    if hasattr(widget, "get_selection"):
        selection = widget.get_selection()
        model, treeiter = selection.get_selected()
        if treeiter is None:
            return None

        row = model[treeiter]
    else:
        idx = widget.get_active()
        if idx == -1:
            return None

        row = widget.get_model()[idx]

    return row


def get_list_selection(widget, column=0, check_visible=False, check_entry=True):
    """
    Helper to simplify getting the selected row and value in a list/tree/combo.
    If nothing is selected, and the widget is a combo box with a text entry,
    return the value of that.

    :param check_entry: If True, attempt to check the widget's text entry
        using the logic described above.
    """
    row = get_list_selected_row(widget, check_visible=check_visible)
    if row is not None:
        return row[column]

    if check_entry and hasattr(widget, "get_has_entry"):
        if widget.get_has_entry():
            return widget.get_child().get_text().strip()

    return None


#####################
# UI setter helpers #
#####################


def set_list_selection_by_number(widget, rownum):
    """
    Helper to set list selection from the passed row number
    """
    path = str(rownum)
    selection = widget.get_selection()

    selection.unselect_all()
    widget.set_cursor(path)
    selection.select_path(path)


def set_list_selection(widget, value, column=0):
    """
    Set a list or tree selection given the passed key, expected to
    be stored at the specified column.

    If the passed value is not found, and the widget is a combo box with
    a text entry, set the text entry to the passed value.
    """
    model = widget.get_model()
    _iter = None
    for row in model:
        if row[column] == value:
            _iter = row.iter
            break

    if not _iter:
        if hasattr(widget, "get_has_entry") and widget.get_has_entry():
            widget.get_child().set_text(value or "")
        else:
            _iter = model.get_iter_first()

    if hasattr(widget, "get_selection"):
        selection = widget.get_selection()
        cb = selection.select_iter
    else:
        selection = widget
        cb = selection.set_active_iter
    if _iter:
        cb(_iter)
    selection.emit("changed")


##################
# Misc functions #
##################

def _dismiss_menu_action(button):
    popover = button.get_ancestor(Gtk.Popover)
    while popover is not None:
        parent = popover.get_parent()
        ancestor = parent.get_ancestor(Gtk.Popover) if parent is not None else None
        popover.popdown()
        popover = ancestor


class vmmMenuActionButton(Gtk.Button):
    """Gtk.Button whose first clicked handler dismisses its menu hierarchy."""

    __gtype_name__ = "VmmMenuActionButton"

    def __init__(self):
        Gtk.Button.__init__(self)
        # Install before Gtk.Builder or callers connect action handlers.
        self.connect("clicked", _dismiss_menu_action)


def new_menu_action_button(label):
    button = vmmMenuActionButton()
    button.set_label(label)
    button.set_use_underline(True)
    return button


def _align_menu_row(row, *_args):
    if isinstance(row, Gtk.MenuButton):
        rtl = Gtk.Widget.get_direction(row) == Gtk.TextDirection.RTL
        row.set_direction(Gtk.ArrowType.LEFT if rtl else Gtk.ArrowType.RIGHT)
        text = row.get_label()
        if text is not None:
            # MenuButton's native label is not exposed by get_child(). Own the
            # label instead, leaving the arrow and its spacing to the theme.
            label = Gtk.Label(label=text)
            label.set_mnemonic_widget(row)
            row.set_child(label)
            row.set_always_show_arrow(True)
        label = row.get_child()
        if isinstance(label, Gtk.Label):
            label.set_use_underline(row.get_use_underline())
            row.update_property([Gtk.AccessibleProperty.LABEL], [label.get_text()])
    else:
        label = row.get_child()

    if isinstance(label, Gtk.Label):
        # Logical alignment follows RTL without reaching into internal boxes.
        label.set_halign(Gtk.Align.START)
        label.set_hexpand(True)


def _style_menu_rows(menu):
    row = menu.get_child().get_first_child()
    while row:
        if isinstance(row, (Gtk.Button, Gtk.MenuButton, Gtk.CheckButton)):
            if not row.has_css_class("vmm-menu-row"):
                row.add_css_class("vmm-menu-row")
                if isinstance(row, (Gtk.Button, Gtk.MenuButton)):
                    row.set_has_frame(False)
                    row.connect("notify::label", _align_menu_row)
                    row.connect("notify::use-underline", _align_menu_row)
                    row.connect("direction-changed", _align_menu_row)
            # CheckButton keeps its native indicator and text layout. Action
            # rows do not reserve a guessed-width checkbox column.
            if not isinstance(row, Gtk.CheckButton):
                _align_menu_row(row)
        row = row.get_next_sibling()


def init_menu(menu):
    """Style static and rebuilt menu rows after their show handlers populate them."""
    if getattr(menu, "_vmm_menu_initialized", False):
        return
    menu._vmm_menu_initialized = True
    menu.add_css_class("vmm-menu")
    menu.add_css_class("menu")
    menu.set_has_arrow(False)
    # Align dropdowns to their button's start and side submenus to its top,
    # instead of centering a speech bubble around the triggering row.
    menu.set_halign(Gtk.Align.START)
    menu.set_valign(Gtk.Align.START)
    menu.connect_after("show", _style_menu_rows)


def popup_menu_at_widget(menu, widget, x=0, y=0):
    # TreeView and VTE do not lay out arbitrary popover children. The window's
    # content container does; translate the click from the source widget.
    parent = widget.get_root().get_child()
    if not menu.get_parent():
        menu.set_parent(parent)
    _ok, point = widget.compute_point(parent, Graphene.Point().init(x, y))
    rect = Gdk.Rectangle()
    rect.x, rect.y, rect.width, rect.height = int(point.x), int(point.y), 1, 1
    menu.set_pointing_to(rect)
    menu.popup()


def set_grid_row_visible(child, visible):
    """
    For the passed widget, find its parent GtkGrid, and hide/show all
    elements that are in the same row as it. Simplifies having to name
    every element in a row when we want to dynamically hide things
    based on UI interaction
    """
    parent = child.get_parent()
    if not isinstance(parent, Gtk.Grid):
        raise xmlutil.DevError("parent must be grid, not %s" % type(parent))

    row = parent.query_child(child)[1]
    c = parent.get_first_child()
    while c:
        if parent.query_child(c)[1] == row:
            c.set_visible(visible)
        c = c.get_next_sibling()


def init_combo_text_column(combo, col):
    """
    Set the text column of the passed combo to 'col'. Does the
    right thing whether it's a plain combo or a comboboxentry. Saves
    some typing.

    :returns: If we added a cell renderer, returns it. Otherwise return None
    """
    if combo.get_has_entry():
        combo.set_entry_text_column(col)
    else:
        text = Gtk.CellRendererText()
        combo.pack_start(text, True)
        combo.add_attribute(text, "text", col)
        return text
    return None


def pretty_mem(val):
    val = int(val)
    if val > (10 * 1024 * 1024):
        return "%2.2f GiB" % (val / (1024.0 * 1024.0))
    else:
        return "%2.0f MiB" % (val / 1024.0)


def build_simple_combo(combo, values, default_value=None, sort=True):
    """
    Helper to build a combo with model schema [xml value, label]
    """
    model = Gtk.ListStore(object, str)
    combo.set_model(model)
    init_combo_text_column(combo, 1)
    if sort:
        model.set_sort_column_id(1, Gtk.SortType.ASCENDING)

    for xmlval, label in values:
        model.append([xmlval, label])
    if default_value:
        set_list_selection(combo, default_value)
    elif len(model):
        combo.set_active(0)
