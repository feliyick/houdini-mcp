# Compatibility shim: the code lives in houdini_mcp/server.py. Kept so existing registrations
# (`claude mcp add houdini -- .venv/bin/python server.py`) and `import server` in scripts keep working.
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from houdini_mcp.server import *          # noqa: F401,F403
from houdini_mcp.server import main, _call  # noqa: F401

if __name__ == '__main__':
    main()
