"""Package entrypoint."""

import sys

from .commands.review_pr import main

sys.exit(main())
