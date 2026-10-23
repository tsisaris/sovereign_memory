"""Run the Owner runtime with python -m sovereign_memory <memory database>.

Settings live in the memory database: change them with python -m sovereign_memory set-setting and read them with show-settings.
"""

import sys

from .runtime import main

sys.exit(main())
