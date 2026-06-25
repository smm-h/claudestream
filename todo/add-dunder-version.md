# Add `__version__` to `claudestream/__init__.py`

## Context

rlsbl now requires pypi targets to have a `__version__` variable in `__init__.py`. During releases, rlsbl bumps this value automatically alongside `pyproject.toml`.

## Problem

`claudestream/__init__.py` currently has no `__version__` line. This will cause rlsbl release to fail with a hard error when it tries to bump the version in the package source.

## Solution

Add the following line after the docstring in `claudestream/__init__.py`:

```python
__version__ = "0.12.2"
```

rlsbl will discover and bump this file automatically during future releases.
