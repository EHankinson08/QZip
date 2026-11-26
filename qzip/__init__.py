"""QZip: verified (byte-exact) compression. GPL-3.0-or-later (see LICENSE, NOTICE)."""
import os as _os
# Quiet, local-only defaults: no Ray cluster, no log file in the working directory.
_os.environ.setdefault("QZIP_NO_RAY", "1")
_os.environ.setdefault("QZIP_LOG_FILE", "")
__version__ = "1.2.0"
