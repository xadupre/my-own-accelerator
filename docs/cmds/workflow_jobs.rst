workflow-jobs command
=====================

The command reports GitHub workflow jobs with the same authentication flow as
other commands (``--token``/``GITHUB_TOKEN``/token cache or ``--gh``):

.. code-block:: bash

    workflow-jobs xadupre my-own-accelerator --queued
    workflow-jobs xadupre my-own-accelerator --queued --dump csv
    workflow-jobs xadupre my-own-accelerator --running
    workflow-jobs xadupre my-own-accelerator --duration --since 60
    workflow-jobs xadupre my-own-accelerator --duration --since -60d --dump xlsx
    workflow-jobs xadupre my-own-accelerator --fail-rate --since 2026-01-01

Synopsis:

.. runpython::

    from moa.commands.workflow_jobs import _build_parser
    parser = _build_parser()
    parser.prog = f"python -m moa {parser.prog}"
    parser.print_help()

Options
-------

Exactly one option must be chosen:

* ``--queued`` prints a fixed-width table of queued workflow jobs sorted by job name.
* ``--running`` prints a fixed-width table of running workflow jobs with their
  current duration in seconds.
* ``--duration`` writes historical successful workflow-run durations to CSV and
  generates SVG/HTML graphs from the run list returned by ``/actions/runs``.
  The tabular export contains ``run_id``, ``created_at``, ``name``, ``pr``, and
  ``duration`` (in seconds). With ``--dump xlsx``, it also writes an Excel file.
  Graph generation excludes duration outliers above three times the per-workflow
  median and writes those excluded rows to separate ``workflow_jobs_duration_outliers_*``
  files in ``--output-dir`` with a ``url`` column pointing to the workflow run.
  The fetch stops at ``--since`` (60 days by default). ``--since`` accepts an ISO
  date/datetime, a relative value such as ``-60d``, or an integer day count such
  as ``60``. ``--verbose`` shows ``min(date)`` / ``max(date)`` for each fetched
  page. Historical run fetches are also cached as JSON files in the
  ``workflow_jobs_cache/`` subfolder under ``--output-dir`` and reused on
  repeated calls, with one cache file per day. These cache files are written
  incrementally while pages are being fetched.
* ``--fail-rate`` writes historical counts for failed/cancelled/skipped/success
  jobs to CSV and prints the same data as a fixed-width table.

Additional output options:

* ``--dump {csv,xlsx}`` writes the selected tabular report to ``--output-dir``.
  ``--queued`` and ``--running`` only write a file when ``--dump`` is used.
