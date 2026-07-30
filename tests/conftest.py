"""
conftest.py
Makes the project root importable from tests/ (so `from
kafka_consumer_etl import ...` etc. work without installing the
project as a package -- appropriate for this PoC-scope project).
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
