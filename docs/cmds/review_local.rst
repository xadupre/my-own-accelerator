review-local command
====================

The local review command is available either as a script:

.. code-block:: bash

    review-local README.md

or through the package entrypoint:

.. code-block:: bash

    python -m moa review-local README.md

Synopsis:

.. runpython::

    from moa.commands.review_local import _build_parser
    parser = _build_parser()
    parser.prog = f"python -m moa {parser.prog}"
    parser.print_help()

Examples::

    review-local README.md
    review-local --copilot-review --token "$GITHUB_TOKEN" moa/commands/review_pr.py
    python -m moa review-local README.md

The command prints a ``# Local Files Review`` markdown report containing
the provided files and, if requested, a ``## Copilot Review`` section.
It reuses the same token cache file as ``review-pr``
(``~/.config/moa/review_pr.json``) and supports ``--save`` to persist
the resolved token. Use ``-v`` or ``--verbose`` to print progress
information to standard error while the command runs, including which
token source/type is used.

``review-local`` supports the same ``--copilot-review``, ``--model``, and
``--prompt`` flags as ``review-pr``.  Use ``--prompt`` one or more times
to ask follow-up questions as part of the same Copilot conversation
session::

    review-local --copilot-review --token "$GITHUB_TOKEN" \
        --prompt "Highlight any security concerns." \
        moa/commands/review_pr.py
