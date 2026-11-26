"""houdini-mcp: drive a live (or headless) SideFX Houdini session from an MCP client such as Claude Code.

Modules:
  server            the MCP server (runs in a normal Python 3.10+, talks to the listener over localhost TCP)
  houdini_listener  runs INSIDE Houdini (GUI or hython) and executes the commands
  headless          start the listener in hython on a .hip file, no GUI (batch / farm / CI)
  install           set Houdini up to find the listener, and print the MCP registration command
"""
__version__ = '0.2.0'
