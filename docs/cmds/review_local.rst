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
