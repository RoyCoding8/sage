"""
Closeable Mixin — Shared lifecycle for resources with a close() method.

Eliminates the duplicated __del__ / __enter__ / __exit__ boilerplate
found across classes that manage external resources (HTTP clients,
database connections, subprocesses).

Usage:
    class MyClient(CloseableMixin):
        def close(self):
            ...  # resource-specific teardown

The mixin provides:
    __del__   — safe close-on-garbage-collection (swallows exceptions)
    __enter__ — context manager entry (returns self)
    __exit__  — context manager exit (calls close())
"""


class CloseableMixin:
    """Mixin that adds safe __del__ and context-manager protocol to any class with a close() method."""

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
