---
title: README.md
---
# claudestream

:-: var key="project.description"

## Install

```
uv pip install claudestream
```

## Quick start

```python
from claudestream import SyncSession

with SyncSession() as session:
    response = session.send("Hello, Claude!")
    print(response.text)
```

## Commands

:-: table-commands path="claudestream/"

## Modules

:-: list-modules path="claudestream/"

## License

MIT
