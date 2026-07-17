workflow-jobs command
=====================

The command reports GitHub workflow jobs with the same authentication flow as
other commands (``--token``/``GITHUB_TOKEN``/token cache or ``--gh``):

.. code-block:: bash

    workflow-jobs xadupre my-own-accelerator --queued
    workflow-jobs xadupre my-own-accelerator --queued --dump csv
    workflow-jobs xadupre my-own-accelerator --running
    workflow-jobs xadupre my-own-accelerator --duration --since -60d
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
* ``--duration`` writes historical successful job durations to CSV and
  generates SVG/HTML graphs. With ``--dump xlsx``, it also writes an Excel file.
  The fetch stops at ``--since`` (60 days by default), and ``--verbose`` shows
  ``min(date)`` / ``max(date)`` for each fetched page.
* ``--fail-rate`` writes historical counts for failed/cancelled/skipped/success
  jobs to CSV and prints the same data as a fixed-width table.

Additional output options:

* ``--dump {csv,xlsx}`` writes the selected tabular report to ``--output-dir``.
  ``--queued`` and ``--running`` only write a file when ``--dump`` is used.
