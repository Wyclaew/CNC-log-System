"""Test suite. Run with: python3 -m unittest discover -s tests -v"""

import os
import sys

# Allow running the tests without installing the package.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
