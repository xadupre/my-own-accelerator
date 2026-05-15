pr-stats command
================

The PR activity report command is available either as a script:

.. code-block:: bash

    pr-stats xadupre my-own-accelerator --output-dir . --prefix pr_activity

or through the package entrypoint:

.. code-block:: bash

    python -m moa pr-stats xadupre my-own-accelerator

The command scans completed pull requests (open PRs are skipped) and produces:

* ``<prefix>.csv``
* ``<prefix>.xlsx``
* ``<prefix>_status.svg``
* ``<prefix>_comments.svg``

Each row includes pull request author, creation datetime, merge/close status,
manual comment count, Copilot command count, and total workflow job duration
for that PR (in seconds).

Use ``--since`` to only include pull requests created on/after a given date
(``YYYY-MM-DD`` or ISO datetime), and ``--cache-file`` to control where the
PR statistics cache is stored. By default, cache is written to
``<output-dir>/<prefix>_cache.json`` and cached PR rows are reused on
subsequent runs instead of requesting their comment statistics again.
