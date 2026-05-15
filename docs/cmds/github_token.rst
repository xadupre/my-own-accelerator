Obtaining a GitHub Personal Access Token
========================================

Several features of the ``review-pr`` command — including authenticated API
requests and Copilot-powered reviews — require a GitHub
**Personal Access Token** (PAT).  This page explains how to create one.

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
