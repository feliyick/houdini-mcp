# start_listener.py: start (or restart, after an update) the houdini-mcp listener in the open Houdini session.
# Paste into the Python Shell or a shelf tool. Set HOUDINI_MCP_DIR to wherever you cloned the repo.
import os, sys, importlib, hou
HOUDINI_MCP_DIR = os.path.expanduser('~/HoudiniProjects/houdini-mcp')
if HOUDINI_MCP_DIR not in sys.path: sys.path.insert(0, HOUDINI_MCP_DIR)
_old = sys.modules.get('houdini_listener')
if _old is not None: _old.stop()          # free the port before reloading new code
import houdini_listener
importlib.reload(houdini_listener)
hou.session.mcp_listener = houdini_listener
houdini_listener.start()
# stop:  hou.session.mcp_listener.stop()
