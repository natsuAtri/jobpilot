"""Sandbox compatibility shim.

`tempfile.mkdtemp` creates its directory with mode 0700. Under the DSH Windows
sandbox that restrictive DACL breaks ACL inheritance, so the very next write
into the fresh directory is denied and pip (which unpacks into mkdtemp dirs)
fails with PermissionError.

This module is imported automatically by CPython at startup when its parent
directory is on PYTHONPATH. It reimplements mkdtemp using a plain makedirs so
the new directory inherits the parent's permissions instead.

Not part of the application; needed only to install dependencies inside a
sandboxed session.
"""
import errno
import itertools
import os
import tempfile

_counter = itertools.count()


def _mkdtemp(suffix=None, prefix=None, dir=None):
    prefix = tempfile.gettempprefix() if prefix is None else prefix
    suffix = '' if suffix is None else suffix
    if dir is None:
        dir = tempfile.gettempdir()
    for _ in range(10000):
        candidate = os.path.join(dir, f'{prefix}{next(_counter)}{suffix}')
        try:
            os.makedirs(candidate, exist_ok=False)
        except FileExistsError:
            continue
        return os.path.abspath(candidate)
    raise FileExistsError(errno.EEXIST, 'No usable temporary directory name found', dir, suffix)


tempfile.mkdtemp = _mkdtemp
