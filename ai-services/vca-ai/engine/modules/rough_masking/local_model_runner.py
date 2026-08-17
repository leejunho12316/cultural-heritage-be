"""Compatibility module for local model runner imports."""

import sys
from importlib import import_module

sys.modules[__name__] = import_module("modules.rough_masking.local_model.runner")
