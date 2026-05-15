# my-own-accelerator

[![CI](https://github.com/xadupre/my-own-accelerator/actions/workflows/ci.yml/badge.svg)](https://github.com/xadupre/my-own-accelerator/actions/workflows/ci.yml)
[![black](https://github.com/xadupre/my-own-accelerator/actions/workflows/black.yml/badge.svg)](https://github.com/xadupre/my-own-accelerator/actions/workflows/black.yml)
[![ruff](https://github.com/xadupre/my-own-accelerator/actions/workflows/ruff.yml/badge.svg)](https://github.com/xadupre/my-own-accelerator/actions/workflows/ruff.yml)
[![mypy](https://github.com/xadupre/my-own-accelerator/actions/workflows/mypy.yml/badge.svg)](https://github.com/xadupre/my-own-accelerator/actions/workflows/mypy.yml)
[![pyrefly](https://github.com/xadupre/my-own-accelerator/actions/workflows/pyrefly.yml/badge.svg)](https://github.com/xadupre/my-own-accelerator/actions/workflows/pyrefly.yml)

Minimal Python project scaffold.

![my-own-accelerator logo](docs/_static/logo.svg)

## Install

```bash
pip install -e ".[dev]"
```

## Quick usage

Command line pull request review:

```bash
review-pr xadupre my-own-accelerator 1
```

Command line local files review:

```bash
review-local README.md
```

## Development checks

```bash
black --check .
ruff check .
mypy moa
pyrefly check
pytest -q
```

## Documentation

The documentation uses reStructuredText (`docs/index.rst`) and a Sphinx configuration
aligned with `yet-another-onnx-builder` extensions.
