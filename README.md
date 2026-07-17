# my-own-accelerator

[![CI](https://github.com/xadupre/my-own-accelerator/actions/workflows/ci.yml/badge.svg)](https://github.com/xadupre/my-own-accelerator/actions/workflows/ci.yml)
[![black](https://github.com/xadupre/my-own-accelerator/actions/workflows/black.yml/badge.svg)](https://github.com/xadupre/my-own-accelerator/actions/workflows/black.yml)
[![ruff](https://github.com/xadupre/my-own-accelerator/actions/workflows/ruff.yml/badge.svg)](https://github.com/xadupre/my-own-accelerator/actions/workflows/ruff.yml)
[![mypy](https://github.com/xadupre/my-own-accelerator/actions/workflows/mypy.yml/badge.svg)](https://github.com/xadupre/my-own-accelerator/actions/workflows/mypy.yml)
[![pyrefly](https://github.com/xadupre/my-own-accelerator/actions/workflows/pyrefly.yml/badge.svg)](https://github.com/xadupre/my-own-accelerator/actions/workflows/pyrefly.yml)
[![Repo size](https://img.shields.io/github/repo-size/xadupre/my-own-accelerator)](https://github.com/xadupre/my-own-accelerator)

Minimal Python project scaffold.

<img src="docs/_static/logo.svg" alt="my-own-accelerator logo" width="300">

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

Command line token caching (classic for all projects, or project-specific):

```bash
github-token --token "$GITHUB_TOKEN" --classic
github-token --token "$GITHUB_TOKEN" --owner xadupre --repo my-own-accelerator
```

Command line pull request statistics report:

```bash
pr-stats xadupre my-own-accelerator --prefix pr_activity
```

Command line weekly pull request summary table:

```bash
pr-weekly-table xadupre my-own-accelerator
```

This command writes the Markdown table to
`dump_pr_stats/pr_weekly_<repo>.md` and caches fetched rows in
`dump_pr_stats/pr_weekly_<repo>_cache.json` by default.

Command line workflow jobs reports:

```bash
workflow-jobs xadupre my-own-accelerator --queued
workflow-jobs xadupre my-own-accelerator --queued --dump csv
workflow-jobs xadupre my-own-accelerator --running
workflow-jobs xadupre my-own-accelerator --duration --since 60
workflow-jobs xadupre my-own-accelerator --waiting --since 60
workflow-jobs xadupre my-own-accelerator --duration --dump xlsx
workflow-jobs xadupre my-own-accelerator --fail-rate
```

Historical `workflow-jobs` fetches also cache raw runs/jobs JSON in the
`workflow_jobs_cache/` subfolder under `--output-dir`, with one cache file per
day so repeating the same report can reuse previously collected data. Duration
graphs exclude workflow durations above three times the per-workflow median and
write those outliers to separate `workflow_jobs_duration_outliers_*` files with
the workflow-run URL. Waiting-time reports use the same historical run cache and
write `workflow_jobs_waiting_*` CSV/XLSX/graph outputs based on the queue delay
between `created_at` and `run_started_at`.

Replace `xadupre` and `my-own-accelerator` with your GitHub owner and repository name.

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

See the project on GitHub: <https://github.com/xadupre/my-own-accelerator>.
