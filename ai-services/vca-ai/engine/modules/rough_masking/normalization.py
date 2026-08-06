"""Compatibility module for rough candidate normalization imports."""

import sys
from importlib import import_module

sys.modules[__name__] = import_module("modules.rough_masking.candidates.normalization")
