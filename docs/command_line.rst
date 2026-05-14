Command Line
============

The ``my-own-accelerator`` package exposes a ``review-pr`` command that
fetches information about a GitHub pull request and prints a Markdown
summary to standard output.

Installation
------------

Install the package (preferably in a virtual environment) to make the
command available::

    pip install my-own-accelerator

After installation the ``review-pr`` command is available on the path.

Synopsis
--------

.. code-block:: text

    review-pr [--token TOKEN] [--api-url URL] owner repo pull_request

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
    When omitted the tool issues unauthenticated requests, which are
    subject to lower rate limits.  For private repositories or to
    avoid rate limiting, pass a token with at least ``repo:read``
    scope::

        review-pr --token ghp_xxxxxxxxxxxx owner repo 42

``--api-url URL``
    Base URL of the GitHub API.  Defaults to
    ``https://api.github.com``.  Override this when working against a
    GitHub Enterprise instance::

        review-pr --api-url https://github.example.com/api/v3 owner repo 42

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

Review a pull request on a GitHub Enterprise server::

    review-pr \
        --api-url https://github.example.com/api/v3 \
        --token "$GHE_TOKEN" \
        myorg myrepo 7

Output Format
-------------

The command prints a Markdown document to standard output.  The document
contains three sections:

* **Summary** – title, state, author, URL, number of changed files,
  and total additions/deletions.
* **Description** – the body text of the pull request.
* **Changed Files** – list of every file touched by the pull request
  together with the number of added and deleted lines.

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

    markdown = review_pull_request(
        owner="xadupre",
        repo="my-own-accelerator",
        pull_request=1,
        token="ghp_xxxxxxxxxxxx",   # optional
        api_url="https://api.github.com",  # optional
    )
    print(markdown)

See the :mod:`moa.commands.review_pr` API reference for full details.
