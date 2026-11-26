"""
headless.py - run the houdini-mcp listener in hython (no GUI): batch jobs, render farms, CI, or a second
session beside the artist's GUI.

Usually started through the launcher, which finds hython for you:
    houdini-mcp-headless --hip /path/scene.hipnc [--port 9877] [--allow-save]
    houdini-mcp-headless --hip scene.hipnc --call cache_write --args '{"node": "/obj/geo1/OUT", "path": "$HIP/cache/out.$F4.bgeo.sc", "frame_range": [1, 24]}'
or directly:
    hython /path/to/houdini_mcp/headless.py --hip scene.hipnc

Serve mode listens on 127.0.0.1:<port> for the MCP server (server.py), exactly like the GUI listener.
--call runs one command, prints its JSON result and exits (non-zero exit on error).

Safety in headless mode:
  * the loaded .hip is never saved over unless --allow-save (auto_save is forced off); save_scene(path=...) saves a copy
  * the project's houdini_mcp_config.json still applies (allowed_roots, output_dirs, run_python off ...)
  * viewport tools refuse (no GUI); quick_render / synth_render / usd_export / cache_write work
"""
import argparse, json, os, sys

def _register_houdini_mcp(pkg_dir):
    """Make `houdini_mcp` importable from its folder WITHOUT putting the surrounding site-packages on Houdini's
    sys.path (a pip venv may be a different Python than Houdini's, and its numpy etc. must not shadow Houdini's)."""
    import importlib.util, sys
    if 'houdini_mcp' in sys.modules: return
    spec = importlib.util.spec_from_file_location('houdini_mcp', os.path.join(pkg_dir, '__init__.py'),
                                                  submodule_search_locations=[pkg_dir])
    mod = importlib.util.module_from_spec(spec); sys.modules['houdini_mcp'] = mod; spec.loader.exec_module(mod)

if __name__ == '__main__' and not __package__:      # run as a file by the launcher: import the package from beside us
    _register_houdini_mcp(os.path.dirname(os.path.abspath(__file__)))


def main(argv=None):
    ap = argparse.ArgumentParser(prog='houdini-mcp-headless (hython)', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--hip', required=True, help='scene to load (.hip / .hipnc / .hiplc)')
    ap.add_argument('--port', type=int, default=None, help='TCP port (default $HOUDINI_MCP_PORT or 9877)')
    ap.add_argument('--allow-save', action='store_true', help='let save_scene overwrite the loaded .hip')
    ap.add_argument('--call', help='run one command and exit, e.g. cache_write')
    ap.add_argument('--args', default='{}', help='JSON arguments for --call')
    ap.add_argument('--frame', type=float, default=None, help='set this frame after loading')
    a = ap.parse_args(argv)

    os.environ['HOUDINI_MCP_PORT'] = str(a.port or os.environ.get('HOUDINI_MCP_PORT') or 9877)
    import hou
    if hou.isUIAvailable():
        raise SystemExit('headless.py is for hython; in the GUI run start_listener.py instead')
    hip = os.path.abspath(os.path.expanduser(a.hip))
    if not os.path.exists(hip): raise SystemExit('no such scene: %s' % hip)
    hou.hipFile.load(hip, suppress_save_prompt=True, ignore_load_warnings=True)
    if a.frame is not None: hou.setFrame(a.frame)

    from houdini_mcp import houdini_listener as L
    L.OVERRIDES.update({'auto_save': False,                 # a batch session never saves behind your back
                        'headless_allow_save': bool(a.allow_save), 'allow_reload': False})
    cfg = L.load_config()

    if a.call:
        resp = L.handle(json.dumps({'id': 1, 'cmd': a.call, 'args': json.loads(a.args)}))
        print(json.dumps(resp.get('result') if resp['ok'] else resp, indent=1, default=str))
        sys.exit(0 if resp['ok'] else 1)
    print('config: %s | writes limited to %s | output folders %s | run_python %s | saving over the scene %s'
          % (cfg or 'defaults', L.CONFIG['allowed_roots'], L.CONFIG['output_dirs'],
             'ON' if L.CONFIG['allow_python'] else 'off', 'allowed' if a.allow_save else 'off'), flush=True)
    L.serve_forever()


if __name__ == '__main__':
    main()
