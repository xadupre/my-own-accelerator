review-local command
====================

The local review command is available either as a script:

.. code-block:: bash

    review-local README.md

or through the package entrypoint:

.. code-block:: bash

    python -m moa review-local README.md

``review-local`` reuses the same cache file as ``review-pr``
(``~/.config/moa/review_pr.json``) for ``--token``.
Use ``--save`` to persist the token.

The command supports the same ``--copilot-review``, ``--model``, and
``--prompt`` flags as ``review-pr``.  Use ``--prompt`` one or more times
to ask follow-up questions as part of the same Copilot conversation
session::

    review-local --copilot-review --token "$GITHUB_TOKEN" \
        --prompt "Highlight any security concerns." \
        moa/commands/review_pr.py
