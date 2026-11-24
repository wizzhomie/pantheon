"""``python3 -m harness`` runs the same command as ``bin/pantheon``."""

import sys

from .cli import main

sys.exit(main())
