# start_listener.py: start (or restart, after an update) the houdini-mcp listener in the open Houdini session.
# In Houdini's Python Shell (or a shelf tool):
#     exec(open('/path/to/houdini-mcp/start_listener.py').read())
# After `houdini-mcp-install` you can instead run:  import houdini_mcp.houdini_listener as l; l.start()
# (or install with --autostart and it starts with Houdini).
# This script finds a clone from $HOUDINI_MCP_DIR, ~/houdini-mcp or ~/HoudiniProjects/houdini-mcp, else the installed package.
import os, sys, importlib, hou
_cands = [os.environ.get('HOUDINI_MCP_DIR', ''), '~/houdini-mcp', '~/HoudiniProjects/houdini-mcp']
HOUDINI_MCP_DIR = next((os.path.expanduser(c) for c in _cands
                        if c and os.path.exists(os.path.join(os.path.expanduser(c), 'houdini_mcp', 'houdini_listener.py'))), None)
if HOUDINI_MCP_DIR and HOUDINI_MCP_DIR not in sys.path: sys.path.insert(0, HOUDINI_MCP_DIR)
for _name in ('houdini_mcp.houdini_listener', 'houdini_listener'):     # stop whichever copy is running (frees the port)
    _old = sys.modules.get(_name)
    if _old is not None and hasattr(_old, 'stop'):
        try: _old.stop()
        except Exception: pass
import houdini_mcp.houdini_listener as _listener
importlib.reload(_listener)
hou.session.mcp_listener = _listener
_listener.start()
# stop:  hou.session.mcp_listener.stop()
