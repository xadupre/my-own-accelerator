Command Line
============

The ``my-own-accelerator`` package exposes a ``review-pr`` command that
fetches information about a GitHub pull request and prints a Markdown
summary to standard output.  Pass ``--copilot-review`` to include an
AI-generated review produced by GitHub Copilot.

Installation
------------

Install the package (preferably in a virtual environment) to make the
command available::

    pip install my-own-accelerator

After installation the ``review-pr`` command is available on the path.

Synopsis
--------

.. code-block:: text

    review-pr [--token TOKEN] [--api-url URL] [--copilot-review] [--model MODEL] owner repo pull_request

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
    When omitted the tool falls back to the ``GITHUB_TOKEN``
    environment variable (automatically set in GitHub Actions
    workflows).  If neither is provided, requests are unauthenticated
    and subject to lower rate limits.  For private repositories or to
    avoid rate limiting, a token with at least ``repo:read`` scope is
    required.

    See :doc:`github_token` for instructions on how to obtain a token.

    Example::

        review-pr --token ghp_xxxxxxxxxxxx owner repo 42
``--api-url URL``
    Base URL of the GitHub API.  When omitted the tool falls back to
    the ``GITHUB_API_URL`` environment variable (automatically set in
    GitHub Actions workflows).  If neither is provided the default is
    ``https://api.github.com``.  Override this when working against a
    GitHub Enterprise instance::

        review-pr --api-url https://github.example.com/api/v3 owner repo 42

``--copilot-review``
    After fetching the pull request data, send the summary to the
    GitHub Models API (powered by GitHub Copilot) to obtain an
    AI-generated code review.  The review is appended to the Markdown
    output as a ``## Copilot Review`` section.  A valid GitHub token
    is required (see ``--token``)::

        review-pr --copilot-review xadupre my-own-accelerator 1

``--model MODEL``
    AI model to use when ``--copilot-review`` is set.  Accepts any
    model identifier available on the
    `GitHub Models API <https://docs.github.com/en/github-models>`_.
    Defaults to ``openai/gpt-4o-mini``::

        review-pr --copilot-review --model openai/gpt-4o xadupre my-own-accelerator 1

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
        model="openai/gpt-4o-mini",  # optional, this is the default
    )
    print(markdown)

See the :mod:`moa.commands.review_pr` API reference for full details.

