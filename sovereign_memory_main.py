"""The file the memory service is started from; the entry script runs this and nothing else.

It is for giving the running service one file whose path the shared service supervisor can recognise,
so that a later start supersedes a previous copy of this service and never anything else. It takes the
command-line arguments of the runtime (the memory database, the reset flags, the rebuild options). It
gives back the runtime's exit status. It holds no logic of its own: it hands everything to the runtime.
"""

import sys

from sovereign_memory.runtime import main

sys.exit(main())
