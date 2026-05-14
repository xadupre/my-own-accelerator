# my-own-accelerator

[![CI](https://github.com/xadupre/my-own-accelerator/actions/workflows/ci.yml/badge.svg)](https://github.com/xadupre/my-own-accelerator/actions/workflows/ci.yml)

Minimal Python project scaffold.

## Install

```bash
pip install -e ".[dev]"
```

## Quick usage

```python
from my_own_accelerator import create_empty_project

project = create_empty_project()
print(project["name"])  # my-own-accelerator
```

## Development checks

```bash
black --check .
ruff check .
mypy my_own_accelerator
pyrefly check
pytest -q
```

## Documentation

The documentation uses reStructuredText (`docs/index.rst`) and a Sphinx configuration
aligned with `yet-another-onnx-builder` extensions.
