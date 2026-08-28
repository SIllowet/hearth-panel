"""Shared setup: import the panel against a scratch config, never the real one."""
import os
import sys
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

# Must be set before app is imported for the first time - it reads its config
# at import time and writes a default one if there is none.
os.environ.setdefault('HEARTH_CONFIG',
                      os.path.join(tempfile.mkdtemp(prefix='hearth-test-'), 'config.json'))
os.environ.setdefault('HEARTH_NO_OPEN', '1')
