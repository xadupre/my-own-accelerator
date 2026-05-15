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
manual comment count, and Copilot command count.
