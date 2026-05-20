CMDs
====

The library implements command line tools for GitHub token setup, pull-request
reviewing, and pull-request activity reporting.

.. code-block:: bash

    python -m moa

.. runpython::
    :rst:

    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-m", "moa", "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    output = proc.stdout.strip() or proc.stderr.strip()
    print(".. code-block:: text")
    print()
    for line in output.splitlines():
        print(f"    {line}")

.. toctree::
    :maxdepth: 1
    :caption: Command Lines

    github_token
    pr_weekly_table
    pr_stats
    review_local
    review_pr
