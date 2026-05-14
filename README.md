# my-own-accelerator

Minimal Python project scaffold inspired by
[yet-another-onnx-builder](https://github.com/xadupre/yet-another-onnx-builder).

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
