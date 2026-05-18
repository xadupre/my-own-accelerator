CMDs
====

The library implements a couple of command lines.

.. code-block:: bash

    python -m moa

.. runpython::
    :rst:

    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-m", "moa", "--help"],
        check=False,
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

    github_token
    pr_stats
    review_local
    review_pr
