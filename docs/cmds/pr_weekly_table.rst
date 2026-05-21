pr-weekly-table command
=======================

The weekly pull request summary command is available either as a script:

.. code-block:: bash

    pr-weekly-table xadupre my-own-accelerator

or through the package entrypoint:

.. code-block:: bash

    python -m moa pr-weekly-table xadupre my-own-accelerator

Synopsis:

.. runpython::

    from moa.commands.pr_weekly_table import _build_parser
    parser = _build_parser()
    parser.prog = f"python -m moa {parser.prog}"
    parser.print_help()

By default the command writes a Markdown table to
``dump_pr_stats/pr_weekly_<repo>.md`` for pull requests created over the last
seven days with:

* title
* author
* creation date
* last update
* link
* whether CI needs approval to start
* CI status (green/pending or first failing required check)
* reviewers

Use ``--copilot`` to append ``Copilot summary`` and ``Help needed`` columns.
Fetched PR rows are cached in ``dump_pr_stats/pr_weekly_<repo>_cache.json`` by
default so unchanged PR entries are not fetched again. Use ``--verbose`` to
print the resolved cache and output file locations.
