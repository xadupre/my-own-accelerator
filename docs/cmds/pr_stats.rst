pr-stats command
================

The PR activity report command is available either as a script:

.. code-block:: bash

    pr-stats xadupre my-own-accelerator --prefix pr_activity

or through the package entrypoint:

.. code-block:: bash

    python -m moa pr-stats xadupre my-own-accelerator

Synopsis:

.. runpython::

    from moa.commands.pr_stats import _build_parser
    parser = _build_parser()
    parser.prog = f"python -m moa {parser.prog}"
    parser.print_help()

The command scans completed pull requests (open PRs are skipped) and produces:

* ``<prefix>.csv``
* ``<prefix>.xlsx``
* ``graphs_<repo>/<prefix>_status.svg``
* ``graphs_<repo>/<prefix>_comments.svg``
* ``graphs_<repo>/<prefix>_prs_per_week.svg``
* ``graphs_<repo>/<prefix>_comments_per_pr.svg``  — distribution of PRs by number of comments
* ``graphs_<repo>/<prefix>_comments_per_week.svg``
* ``graphs_<repo>/<prefix>_graphs.html`` — final HTML report embedding all generated graphs
* ``graphs_<repo>/<prefix>_job_duration_<job>.svg`` (one per unique job name, successful runs only)

Each row includes pull request author, creation datetime, merge/close status,
manual comment count, Copilot command count, and total workflow job duration
for that PR (in seconds). The Excel workbook also includes per-week PR counts,
per-PR comment totals, per-week comment totals, a comment-count distribution,
average PR duration per author and week, and a **Job durations** sheet listing
the duration of every successful workflow job across all scanned PRs. A
line-graph SVG is produced for each unique job name, showing duration over time
with a 10-run moving average.

Use ``--since`` to only include pull requests created on/after a given date
(``YYYY-MM-DD`` or ISO datetime), and ``--cache-file`` to control where the
PR statistics cache is stored. By default, cache is written to
``<output-dir>/<prefix>_cache.json`` and cached PR rows are reused on
subsequent runs instead of requesting their comment statistics again.
Generated files are written to the ``dump_pr_stats`` directory by default, with
SVG graphs stored in a repo-specific ``graphs_<repo>`` subdirectory.
By default, generated file names use the ``pr_activity_<repo>`` prefix.
Use ``-v`` or ``--verbose`` to print progress information to standard
error while the command is running, including which token source/type is
used.
