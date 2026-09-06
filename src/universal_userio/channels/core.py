"""Backward-compatible re-export of the stable adapter SDK.

New adapters should import from :mod:`userio_adapter_sdk` directly.
"""
from userio_adapter_sdk import *  # noqa: F401,F403
from userio_adapter_sdk import __all__
