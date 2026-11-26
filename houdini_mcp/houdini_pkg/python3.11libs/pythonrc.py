# houdini-mcp startup hook (the Houdini package written by `houdini-mcp-install` adds this folder to HOUDINI_PATH).
#  * makes `import houdini_mcp` work in Houdini and hython, from $HOUDINI_MCP_PKG only (nothing else added to sys.path)
#  * with HOUDINI_MCP_AUTOSTART=1 (`houdini-mcp-install --autostart`), starts the listener once the GUI is up
import os

def _register_houdini_mcp(pkg_dir):
    """Make `houdini_mcp` importable from its folder WITHOUT putting the surrounding site-packages on Houdini's
    sys.path (a pip venv may be a different Python than Houdini's, and its numpy etc. must not shadow Houdini's)."""
    import importlib.util, sys
    if 'houdini_mcp' in sys.modules: return
    spec = importlib.util.spec_from_file_location('houdini_mcp', os.path.join(pkg_dir, '__init__.py'),
                                                  submodule_search_locations=[pkg_dir])
    mod = importlib.util.module_from_spec(spec); sys.modules['houdini_mcp'] = mod; spec.loader.exec_module(mod)


try:
    if os.environ.get('HOUDINI_MCP_PKG'):
        _register_houdini_mcp(os.environ['HOUDINI_MCP_PKG'])
    if os.environ.get('HOUDINI_MCP_AUTOSTART') == '1':
        import hou
        if hou.isUIAvailable():
            import hdefereval
            def _start_mcp():
                import houdini_mcp.houdini_listener as _l
                hou.session.mcp_listener = _l
                _l.start()
            hdefereval.executeDeferred(_start_mcp)
except Exception as e:                          # never block Houdini from starting
    print('houdini-mcp startup hook failed: %s' % e)
