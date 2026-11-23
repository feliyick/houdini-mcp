# start_listener.py: start (or restart, after an update) the houdini-mcp listener in the open Houdini session.
# In Houdini's Python Shell (or a shelf tool):
#     exec(open('/path/to/houdini-mcp/start_listener.py').read())
# It finds the repo from $HOUDINI_MCP_DIR, or ~/houdini-mcp, or ~/HoudiniProjects/houdini-mcp.
import os, sys, importlib, hou
_cands = [os.environ.get('HOUDINI_MCP_DIR', ''), '~/houdini-mcp', '~/HoudiniProjects/houdini-mcp']
HOUDINI_MCP_DIR = next((os.path.expanduser(c) for c in _cands
                        if c and os.path.exists(os.path.join(os.path.expanduser(c), 'houdini_listener.py'))), None)
if HOUDINI_MCP_DIR is None:
    raise RuntimeError('houdini-mcp not found: set HOUDINI_MCP_DIR to the folder you cloned it into')
if HOUDINI_MCP_DIR not in sys.path: sys.path.insert(0, HOUDINI_MCP_DIR)
_old = sys.modules.get('houdini_listener')
if _old is not None: _old.stop()          # free the port before reloading new code
import houdini_listener
importlib.reload(houdini_listener)
hou.session.mcp_listener = houdini_listener
houdini_listener.start()
# stop:  hou.session.mcp_listener.stop()
