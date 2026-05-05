"""Gatepath — captive portal handler for Linux desktop (Flatpak).

Package root. GTK/PyGObject/dasbus are never imported at this level;
they are loaded on demand inside functions in app.py, portal_monitor.py,
and portal_webview.py so that `python -m gatepath --help` works without
any GUI toolkit installed.
"""

__version__ = "0.1.0"
