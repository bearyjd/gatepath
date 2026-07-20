"""Programmatic GTK4/libadwaita UI widgets for Gatepath.

GTK/PyGObject are never imported at this package level; each widget module
guards its own ``gi`` import so pure helpers stay importable headless.
"""
