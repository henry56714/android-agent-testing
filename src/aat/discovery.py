"""Public discovery API; model, scan and synchronization have separate owners."""

from .discovery_model import gradle_argv as gradle_argv
from .discovery_model import native_inventory as native_inventory
from .discovery_scan import scan as _scan
from .discovery_sync import preview as _preview
from .discovery_sync import reconcile as _reconcile


def scan(project, inventory=None):
    return _scan(project, native_inventory(project) if inventory is None else inventory)


def preview(project, inventory=None):
    return _preview(project, native_inventory(project) if inventory is None else inventory)


def reconcile(project, inventory=None):
    return _reconcile(project, native_inventory(project) if inventory is None else inventory)
