# Compatibility shim: the listener lives in houdini_mcp/houdini_listener.py. Kept so an older start_listener.py
# (and reload_listener from a listener started before the move) still find it.
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import importlib
import houdini_mcp.houdini_listener as _impl
importlib.reload(_impl)
from houdini_mcp.houdini_listener import *   # noqa: F401,F403
start, stop = _impl.start, _impl.stop
