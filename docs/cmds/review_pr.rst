review-pr command
=================

The ``my-own-accelerator`` package exposes:

* ``review-pr`` to fetch information about a GitHub pull request and
  print a Markdown summary.
* ``review-local`` to review one or multiple local files and print a
  Markdown summary.

Both commands support ``--copilot-review`` to append AI-generated
feedback produced by GitHub Copilot.

Installation
------------

Install the package (preferably in a virtual environment) to make the
command available::

    pip install my-own-accelerator

After installation the ``review-pr`` and ``review-local`` commands are
available on the path.

``review-local``
----------------

See :doc:`review_local` for the full documentation of the ``review-local``
command.

Synopsis
--------

.. runpython::

    from moa.commands.review_pr import _build_parser
    parser = _build_parser()
    parser.prog = f"python -m moa {parser.prog}"
    parser.print_help()

Positional Arguments
--------------------

``owner``
    GitHub user or organisation that owns the repository
    (e.g. ``xadupre``).

``repo``
    Name of the GitHub repository (e.g. ``my-own-accelerator``).

``pull_request``
    Integer number of the pull request to review (e.g. ``42``).

Optional Arguments
------------------

``--token TOKEN``
    GitHub personal access token used to authenticate API requests.
    Resolution order: explicit flag value → ``GITHUB_TOKEN`` environment
    variable (automatically set in GitHub Actions workflows) → project-specific
    value cached with ``--save`` (``owner/repo`` key) → classic cached value
    with ``--save`` → unauthenticated.  For private repositories or to avoid
    rate limiting, a token with at least ``repo:read`` scope is required.

    See :doc:`github_token` for instructions on how to obtain a token.

    Example::

        review-pr --token ghp_xxxxxxxxxxxx owner repo 42

``--api-url URL``
    Base URL of the GitHub API.  Resolution order: explicit flag value →
    ``GITHUB_API_URL`` environment variable (automatically set in GitHub
    Actions workflows) → value cached with ``--save`` →
    ``https://api.github.com``.  Override this when working against a
    GitHub Enterprise instance::

        review-pr --api-url https://github.example.com/api/v3 owner repo 42

``--user USERNAME``
    Your GitHub username.
    Resolution order: explicit flag value → ``GITHUB_USER`` environment
    variable → value cached with ``--save``.  When set and ``owner`` is
    omitted from the command line, the username is used as the repository
    owner automatically.

    Example – save your username once, then omit ``owner`` in future calls::

        review-pr --user myname --token "$GITHUB_TOKEN" --save myname my-repo 1

        review-pr my-repo 2

``--save``
    Persist the resolved ``--token``, ``--api-url``, and ``--user`` values to
    ``~/.config/moa/review_pr.json`` so they are used automatically in
    future invocations (without needing to pass the flags again).  The file
    is created with owner-read-only permissions (``0600``).

    .. warning::

        This stores your token in plain text.  Only use ``--save`` on a
        personal workstation that you control.  In CI environments, rely on
        ``GITHUB_TOKEN`` instead.

    Example – authenticate once and save::

        review-pr --token "$GITHUB_TOKEN" --save xadupre my-own-accelerator 1

    Subsequent invocations on the same machine no longer need ``--token``::

        review-pr xadupre my-own-accelerator 2

``--copilot-review``
    After fetching the pull request data, send the summary to the
    GitHub Models API (powered by GitHub Copilot) to obtain an
    AI-generated code review.  The review is appended to the Markdown
    output as a ``## Copilot Review`` section.  A valid GitHub token
    is required (see ``--token``)::

        review-pr --copilot-review xadupre my-own-accelerator 1

``--prompt PROMPT``
    Add a follow-up prompt to the Copilot review session.  The prompt
    is sent as a continuation of the same conversation so the model has
    full context from the initial review.  The flag can be repeated to
    ask several follow-up questions in sequence.  Only meaningful when
    ``--copilot-review`` is also set::

        review-pr --copilot-review \
            --prompt "What are the security implications?" \
            --prompt "Are there any performance concerns?" \
            xadupre my-own-accelerator 1

    Each follow-up response is appended to the ``## Copilot Review``
    section, preceded by a separator and the prompt text.

``--model MODEL``
    AI model to use when ``--copilot-review`` is set.  Accepts any
    model identifier available on the
    `GitHub Models API <https://docs.github.com/en/github-models>`_.
    When omitted, Copilot chooses the model automatically and falls
    back to ``openai/gpt-4.1`` if no default model is available::

        review-pr --copilot-review --model openai/gpt-4o xadupre my-own-accelerator 1

``-v``, ``--verbose``
    Print progress information to standard error while the command is
    running. Verbose output also shows which token source is used
    (``--token``, ``GITHUB_TOKEN``, project-specific cache, classic cache,
    or unauthenticated) and the token type (``explicit``, ``fine-grained``,
    ``classic``, or ``none``). For tokens supplied via ``--token`` or
    ``GITHUB_TOKEN``, the type is inferred from the token prefix
    (``ghp_*`` → ``classic``, ``github_pat_*`` → ``fine-grained``).

``-h``, ``--help``
    Print a short help message and exit.

Examples
--------

Review a public pull request without authentication::

    review-pr xadupre my-own-accelerator 1

Review a pull request and save the output to a file::

    review-pr xadupre my-own-accelerator 1 > review.md

Authenticate with a personal access token (recommended for private
repositories or to increase the API rate limit)::

    review-pr --token "$GITHUB_TOKEN" xadupre my-own-accelerator 1

Include an AI-powered Copilot review::

    review-pr --copilot-review --token "$GITHUB_TOKEN" xadupre my-own-accelerator 1

Review a pull request on a GitHub Enterprise server::

    review-pr \
        --api-url https://github.example.com/api/v3 \
        --token "$GHE_TOKEN" \
        myorg myrepo 7

Output Format
-------------

The command prints a Markdown document to standard output.  The document
contains three sections (plus an optional Copilot Review section when
``--copilot-review`` is used):

* **Summary** – title, state, author, URL, number of changed files,
  and total additions/deletions.
* **Description** – the body text of the pull request.
* **Changed Files** – list of every file touched by the pull request
  together with the number of added and deleted lines.
* **Copilot Review** *(optional)* – AI-generated review produced by the
  GitHub Models API when ``--copilot-review`` is passed.

Example output (values are illustrative)::

    # Pull Request Review

    ## Summary
    - **Title:** Fix typo in README
    - **State:** open
    - **Author:** octocat
    - **URL:** https://github.com/owner/repo/pull/42
    - **Files changed:** 2
    - **Additions/Deletions:** +10 / -3

    ## Description
    Fixes a typo in the README introduction paragraph.

    ## Changed Files
    - `README.md` (+10/-3)
    - `docs/index.rst` (+1/-0)

    ## Copilot Review

    The change looks straightforward. Consider adding a test to ensure
    the README renders correctly in CI.

Exit Codes
----------

``0``
    The pull request was retrieved and the Markdown summary was printed
    successfully.

``1``
    An error occurred (network failure, invalid PR number, or
    authentication problem).  A human-readable message is printed to
    standard error.

Python API
----------

The command can also be invoked programmatically:

.. code-block:: python

    from moa.commands.review_pr import review_pull_request

    # Basic summary
    markdown = review_pull_request(
        owner="xadupre",
        repo="my-own-accelerator",
        pull_request=1,
        token="ghp_xxxxxxxxxxxx",   # optional
        api_url="https://api.github.com",  # optional
    )
    print(markdown)

    # With Copilot review
    markdown = review_pull_request(
        owner="xadupre",
        repo="my-own-accelerator",
        pull_request=1,
        token="ghp_xxxxxxxxxxxx",
        copilot_review=True,
        model="openai/gpt-4o",  # optional override
    )
    print(markdown)

    # With Copilot review and follow-up prompts (session)
    markdown = review_pull_request(
        owner="xadupre",
        repo="my-own-accelerator",
        pull_request=1,
        token="ghp_xxxxxxxxxxxx",
        copilot_review=True,
        extra_prompts=[
            "What are the security implications?",
            "Are there any performance concerns?",
        ],
    )
    print(markdown)

See the :mod:`moa.commands.review_pr` API reference for full details.
