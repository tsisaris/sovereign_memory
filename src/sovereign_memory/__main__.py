"""Run the Owner runtime with python -m sovereign_memory <memory database>.

Load its settings first with python -m sovereign_memory load-settings <settings file> <memory database>.
"""

import sys

from .runtime import main

sys.exit(main())
