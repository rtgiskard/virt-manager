# Basic Install

For starters, if you just want to run `virt-manager/virt-install` to test out
changes, it can be done from the source directory:
```sh
./virt-manager
```

For more details on that, see [CONTRIBUTING.md](CONTRIBUTING.md)


To install the software into `/usr/local` (usually), you can do:
```sh
meson setup build
meson install -C build
```


## Pre-requisite software

A detailed dependency list can be found in [virt-manager.spec.in](virt-manager.spec.in) file.

Minimum version requirements of major components:

   - gettext >= 0.19.6
   - python >= 3.9
   - gtk4 >= 4.14
   - libvirt-python >= 0.6.0
   - pygobject3 >= 3.31.3
   - libosinfo >= 0.2.10
   - spice-gtk with SpiceClientGtk-4.0 introspection data (GTK4-only fork)
   - VTE with Vte-3.91 introspection data (GTK4)
   - gtksourceview >= 5 (optional syntax highlighting)

On Debian or Ubuntu based distributions, you need to install the
`gobject-introspection` bindings for some dependencies like `libvirt-glib`
and `libosinfo`. Look for package names that start with `'gir'`, for example
`gir1.2-libosinfo-1.0`.

The graphical application loads only GTK4 widgets. Install the GTK4
`SpiceClientGtk-4.0` and `Vte-3.91` typelibs alongside their shared libraries;
GTK3 `spice-gtk3`, `gtk-vnc2`, `Vte-2.91`, and `GtkSource-4` cannot be loaded
in the same process. The integrated graphical console supports SPICE, not VNC;
the `virt-install` command can still configure VNC guests. A desktop system
tray requires a StatusNotifierItem watcher on the session bus.


## Optional software

`virt-manager` can optionally use [libguestfs](http://libguestfs.org/)
for inspecting the guests.  For this, `python-libguestfs` >= 1.22 is needed.
