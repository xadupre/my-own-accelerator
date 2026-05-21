Obtaining a GitHub Personal Access Token
========================================

A GitHub **Personal Access Token** (PAT) is optional for basic usage of
``review-pr`` on public repositories, but is required in some cases and
strongly recommended in others.  This page explains when you need one and
how to create it.

``github-token`` command
------------------------

Synopsis:

.. runpython::

    from moa.commands.github_token import _build_parser
    parser = _build_parser()
    parser.prog = f"python -m moa {parser.prog}"
    parser.print_help()

Why a token?
------------

The GitHub REST API allows unauthenticated requests, but they are subject to
strict rate limits (60 requests per hour per IP address).  Authenticated
requests receive a much higher limit (5 000 per hour).  A token is also
required when:

* the target repository is **private**.
* you use the ``--copilot-review`` flag (GitHub Models API access is tied to
  your GitHub account).

Token types
-----------

GitHub offers two types of personal access tokens:

**Fine-grained tokens** *(recommended)*
    Scoped to specific repositories with explicit permission grants.
    Available for free and paid accounts.

**Classic tokens** *(legacy)*
    Broader access — suitable when fine-grained tokens are not yet
    supported by an integration.

Which token type should I use?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**For open-source projects you are contributing to**, use a **fine-grained
token** scoped to *read-only* ``Pull requests`` and ``Contents`` permissions
on the target repository.  This follows the principle of least privilege: if
the token is ever exposed, an attacker can only read public data they could
access anyway.

Use a **classic token** only if the upstream organisation has disabled
fine-grained tokens for third-party applications (rare), or if you are
accessing many repositories at once and the per-repository scoping of
fine-grained tokens becomes impractical.

Creating a fine-grained token
------------------------------

1. Sign in to `github.com <https://github.com>`_.
2. Click your avatar in the top-right corner and choose **Settings**.
3. In the left sidebar, click **Developer settings**.
4. Under *Personal access tokens*, choose **Fine-grained tokens**.
5. Click **Generate new token**.
6. Fill in:

   * **Token name** — a descriptive name, e.g. ``review-pr``.
   * **Expiration** — choose an expiry that suits your security policy.
   * **Resource owner** — your personal account or an organisation.
   * **Repository access** — select *Only select repositories* and choose
     the repositories the token needs to access, or choose *All repositories*.

7. Under *Permissions → Repository permissions*, grant:

   * **Contents** → *Read-only* (needed to read the repository).
   * **Pull requests** → *Read-only* (needed to fetch PR data).
   * **Metadata** → *Read-only* (required automatically).

8. Click **Generate token** and copy the token immediately — it will not be
   shown again.

Creating a classic token
-------------------------

1. Sign in to `github.com <https://github.com>`_.
2. Go to **Settings → Developer settings → Personal access tokens →
   Tokens (classic)**.
3. Click **Generate new token (classic)**.
4. Provide a **Note**, set an **Expiration**, and select the scopes:

   * ``repo`` — full repository access, including private repositories.
   * ``public_repo`` — read access to public repositories only (sufficient
     when all target repositories are public).

5. Click **Generate token** and copy it.

Storing the token
-----------------

Never hard-code the token in scripts.  The recommended approaches are:

``github-token`` command::

    # Cache a classic token used as fallback for every repository.
    github-token --token "ghp_xxxxxxxxxxxx" --classic

    # Cache a repository-specific token (owner/repo).
    github-token --token "github_pat_xxxxxxxxxxxx" --owner xadupre --repo my-own-accelerator

    # List cached tokens.
    github-token --list

    # List cached tokens with token type hints (classic vs fine-grained).
    github-token --list --verbose

    # Show token permissions/scopes without saving the token.
    github-token --token "ghp_xxxxxxxxxxxx" --show-permissions

    # Save a classic token and immediately verify its permissions.
    github-token --token "ghp_xxxxxxxxxxxx" --classic --show-permissions

    # List cached tokens and show the permissions of a specific token.
    github-token --list --token "ghp_xxxxxxxxxxxx" --show-permissions

    # List cached tokens and show permissions for each one.
    github-token --list --show-permissions

The command stores values in ``~/.config/moa/review_pr.json`` with owner-only
permissions (``0600``). ``review-pr`` first looks for a project token
(``owner/repo``) and then falls back to the classic token.

Environment variable (local development)::

    export GITHUB_TOKEN="ghp_xxxxxxxxxxxx"
    review-pr xadupre my-own-accelerator 1

The ``review-pr`` command picks up ``GITHUB_TOKEN`` automatically, so no
``--token`` flag is needed once the environment variable is set.

GitHub Actions secret
~~~~~~~~~~~~~~~~~~~~~

In a GitHub Actions workflow the ``GITHUB_TOKEN`` secret is injected
automatically.  Pass it to the step explicitly if needed::

    - name: Review PR
      run: review-pr "${{ github.repository_owner }}" "${{ github.event.repository.name }}" "${{ github.event.pull_request.number }}"
      env:
        GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}

The token provided by GitHub Actions already has ``contents: read`` and
``pull-requests: read`` permissions by default for workflows triggered on
pull request events.

See also
--------

* `GitHub documentation: Managing personal access tokens
  <https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens>`_
* `GitHub documentation: Automatic token authentication
  <https://docs.github.com/en/actions/security-guides/automatic-token-authentication>`_
