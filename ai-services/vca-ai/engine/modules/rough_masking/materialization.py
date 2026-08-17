"""Compatibility module for the rough-mask artifact materialization API."""

import sys
from importlib import import_module

sys.modules[__name__] = import_module("modules.rough_masking.artifacts.materialization")
