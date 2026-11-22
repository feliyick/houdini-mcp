"""
server.py - the MCP server Claude Code talks to (runs in a normal Python 3.10+, NOT inside Houdini).
Forwards each tool call to houdini_listener.py running in your open Houdini.

Register once (from the repo folder):
    claude mcp add houdini -- "$PWD/.venv/bin/python" "$PWD/server.py"
"""
import json, os, socket, itertools
from mcp.server.fastmcp import FastMCP, Image

HOST, PORT = '127.0.0.1', 9876
HERE = os.path.dirname(os.path.abspath(__file__))
mcp = FastMCP('houdini')
_ids = itertools.count(1)


def _call(cmd, timeout=180, **args):
    try:
        s = socket.create_connection((HOST, PORT), timeout=timeout)
    except OSError:
        raise RuntimeError('Houdini listener not reachable on %s:%d. In Houdini, run the contents of start_listener.py in the Python Shell' % (HOST, PORT))
    with s:
        f = s.makefile('rwb')
        f.write((json.dumps({'id': next(_ids), 'cmd': cmd, 'args': args}) + '\n').encode()); f.flush()
        line = f.readline()
    if not line:
        raise RuntimeError('Houdini closed the connection (or the call timed out after %ds)' % timeout)
    resp = json.loads(line)
    if not resp.get('ok'):
        raise RuntimeError(resp.get('error', 'unknown error') + '\n' + resp.get('trace', ''))
    return resp['result']


# ---------------------------------------------------------------- read
@mcp.tool()
def scene_info() -> dict:
    """Overview of the open scene: hip, top-level nodes, watched parameters, current look, cameras, active config."""
    return _call('scene_info')

@mcp.tool()
def list_nodes(path: str = '/obj', recursive: bool = False, limit: int = 300) -> dict:
    """List child nodes (path, type, error/warning flags)."""
    return _call('list_nodes', path=path, recursive=recursive, limit=limit)

@mcp.tool()
def get_parms(node: str, names: list[str] | None = None, contains: str | None = None) -> dict:
    """Read parameter values (and expressions) on a node. Filter by exact names or a substring of name/label."""
    return _call('get_parms', node=node, names=names, contains=contains)

@mcp.tool()
def geometry_stats(node: str) -> dict:
    """Point/prim counts, bbox, groups, attributes, detail attribute values, and counts by paint/material/name."""
    return _call('geometry_stats', node=node)

@mcp.tool()
def dump_attribute(node: str, attrib: str, cls: str = 'prim', limit: int = 200) -> list:
    """Values of one attribute on the first `limit` prims or points (cls = 'prim' | 'point')."""
    return _call('dump_attribute', node=node, attrib=attrib, cls=cls, limit=limit)

@mcp.tool()
def cook_and_check(paths: list[str] | None = None) -> dict:
    """Force-cook nodes (default: hero house, street, Karma) and report every node with errors, plus slow cooks."""
    return _call('cook_and_check', timeout=600, paths=paths)


# ---------------------------------------------------------------- write (backed up, one undo step each, dry_run available)
@mcp.tool()
def set_parms(node: str, values: dict, dry_run: bool = False) -> dict:
    """Set parameters. A string value starting with '=' is set as an expression. Use dry_run=True to preview."""
    return _call('set_parms', node=node, values=values, dry_run=dry_run)

@mcp.tool()
def create_node(parent: str, node_type: str, name: str | None = None, input_from: str | None = None, dry_run: bool = False) -> dict:
    """Create a node (optionally wired to an input)."""
    return _call('create_node', parent=parent, node_type=node_type, name=name, input_from=input_from, dry_run=dry_run)

@mcp.tool()
def connect(src: str, dst: str, input_index: int = 0, dry_run: bool = False) -> dict:
    """Wire src into dst's input."""
    return _call('connect', src=src, dst=dst, input_index=input_index, dry_run=dry_run)

@mcp.tool()
def set_look(look: str) -> dict:
    """Switch the lighting look (names come from the project's houdini_mcp_config.json)."""
    return _call('set_look', look=look)

@mcp.tool()
def save_backup() -> dict:
    """Save a numbered backup of the scene without changing the working file."""
    return _call('save_backup')

@mcp.tool()
def save_scene() -> dict:
    """Save the working .hip file (backs up the previous state first)."""
    return _call('save_scene')

@mcp.tool()
def undo(steps: int = 1) -> dict:
    """Undo the last MCP action(s). Refuses to undo anything the artist did by hand."""
    return _call('undo', steps=steps)

@mcp.tool()
def redo(steps: int = 1) -> dict:
    """Redo MCP action(s) that were undone."""
    return _call('redo', steps=steps)

@mcp.tool()
def history(limit: int = 20) -> dict:
    """Undo/redo stack (agent actions are labelled 'MCP: ...'), recent backups, unsaved state, auto_save on/off."""
    return _call('history', limit=limit)

@mcp.tool()
def restore_backup(name: str, dry_run: bool = False) -> dict:
    """Re-open a backup (a file name from history()) in place of the working scene. Backs up the current state first."""
    return _call('restore_backup', timeout=600, name=name, dry_run=dry_run)

@mcp.tool()
def run_python(code: str) -> dict:
    """Run hou Python in the session (disabled unless allow_python in the config). Put a return value in `result`."""
    return _call('run_python', timeout=600, code=code)


# ---------------------------------------------------------------- see
@mcp.tool()
def quick_render(camera: str, width: int = 640, height: int = 360, samples: int = 24,
                 look: str | None = None) -> Image:
    """Render a small Karma preview through a camera and return it as an image you can look at."""
    res = _call('quick_render', timeout=900, camera=camera, width=width, height=height, samples=samples, look=look)
    return Image(data=_exr_to_png(res['exr']), format='png')

def _exr_to_png(path):
    import io
    import numpy as np, OpenEXR
    from PIL import Image as PILImage
    px = OpenEXR.File(path).channels()['RGBA'].pixels.astype('float32')[..., :3]
    x = np.clip(px, 0, None); k = 0.85
    x = np.where(x > k, k + (1 - k) * np.tanh((x - k) / (1 - k)), x)
    x = np.where(x <= 0.0031308, 12.92 * x, 1.055 * np.power(x, 1 / 2.4) - 0.055)
    buf = io.BytesIO(); PILImage.fromarray((np.clip(x, 0, 1) * 255).astype('uint8')).save(buf, 'PNG')
    return buf.getvalue()


# ---------------------------------------------------------------- context
@mcp.resource('houdini://project-guide')
def project_guide() -> str:
    """The project's guide (CLAUDE.md in the folder Claude Code was started in, or $HOUDINI_MCP_GUIDE)."""
    path = os.environ.get('HOUDINI_MCP_GUIDE') or os.path.join(os.getcwd(), 'CLAUDE.md')
    return open(path, encoding='utf-8').read() if os.path.exists(path) else 'No project guide found at %s' % path


if __name__ == '__main__':
    mcp.run()
