"""Plugins: input adapters and output exporters that live outside the core.

A package adds one by declaring an entry point in its own `pyproject.toml`:

    [project.entry-points."spec2test.adapters"]
    rtf = "my_package.rtf:RtfAdapter"

    [project.entry-points."spec2test.exporters"]
    acme-sheet = "my_package.acme:AcmeExporter"

The entry point names a class (built with no arguments) or an instance. Plugins load the
first time a registry is asked for one. A plugin that fails to load, or takes a name a
built-in already has, is skipped with a warning, so it cannot break the built-ins; asking
for it by name gives the reason.
"""

from __future__ import annotations

import warnings
from collections.abc import Callable
from importlib.metadata import entry_points

ADAPTER_GROUP = "spec2test.adapters"
EXPORTER_GROUP = "spec2test.exporters"


class PluginError(ValueError):
    """An unknown plugin name, or a plugin that does not fit its interface."""


class Registry[T]:
    """Plugins of one kind, by name: the built-ins, then those found by entry point."""

    def __init__(self, group: str, what: str, check: Callable[[object], T]) -> None:
        self.group = group
        self.what = what
        self._check = check
        self._plugins: dict[str, T] = {}
        self._builtin: set[str] = set()
        self._broken: dict[str, str] = {}
        self._loaded = False

    def register(self, plugin: object, *, builtin: bool = False) -> T:
        """Add `plugin`; a name already taken is an error."""
        checked = self._check(plugin)
        name = checked.name  # type: ignore[attr-defined]
        if name in self._plugins:
            raise PluginError(f"{self.what} {name!r} is already registered")
        self._plugins[name] = checked
        if builtin:
            self._builtin.add(name)
        return checked

    def unregister(self, name: str) -> None:
        """Remove a plugin that is not a built-in (for tests and notebooks)."""
        if name in self._builtin:
            raise PluginError(f"{self.what} {name!r} is built in")
        self._plugins.pop(name, None)

    def get(self, name: str) -> T:
        self._load()
        if name in self._plugins:
            return self._plugins[name]
        if name in self._broken:
            raise PluginError(f"{self.what} {name!r} did not load: {self._broken[name]}")
        known = ", ".join(sorted(self._plugins)) or "none"
        raise PluginError(f"unknown {self.what} {name!r}; known: {known}")

    def all(self) -> dict[str, T]:
        """Every plugin, built-ins first, in the order they were registered."""
        self._load()
        return dict(self._plugins)

    def is_builtin(self, name: str) -> bool:
        return name in self._builtin

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        for ep in entry_points(group=self.group):
            try:
                obj = ep.load()
                plugin = obj() if isinstance(obj, type) else obj
                if self._check(plugin).name != ep.name:  # type: ignore[attr-defined]
                    raise PluginError(
                        f"entry point {ep.name!r} names a {self.what} called "
                        f"{plugin.name!r}; the two must match"  # type: ignore[attr-defined]
                    )
                self.register(plugin)
            except Exception as exc:
                self._broken.setdefault(ep.name, f"{type(exc).__name__}: {exc}")
                warnings.warn(
                    f"{self.what} plugin {ep.name!r} ({ep.value}) skipped: {exc}",
                    stacklevel=3,
                )

    def reload(self) -> None:
        """Forget the plugins found by entry point and look again (for tests)."""
        self._plugins = {n: p for n, p in self._plugins.items() if n in self._builtin}
        self._broken = {}
        self._loaded = False


def check_name(plugin: object, what: str) -> str:
    name = getattr(plugin, "name", None)
    if not isinstance(name, str) or not name or name != name.strip() or " " in name:
        raise PluginError(f"a {what} needs a `name` with no spaces, got {name!r}")
    return name
