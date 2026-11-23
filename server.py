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
    """Force-cook nodes (default: the config's check_nodes, else every displayed /obj node) and report every node with errors, plus slow cooks."""
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
def revert_hda(node: str, dry_run: bool = False) -> dict:
    """Re-match an HDA instance to its saved definition (relocks it; discards that instance's local edits). Backed up first."""
    return _call('revert_hda', node=node, dry_run=dry_run)

@mcp.tool()
def reload_listener() -> dict:
    """Reload the listener's code inside Houdini (after editing houdini_listener.py). Needs "allow_reload": true
    in the project config. Waits until the new listener answers."""
    import time
    res = _call('reload_listener')
    for _ in range(30):
        time.sleep(0.5)
        try: return {'reloaded': res['reloading'], 'ping': _call('ping', timeout=5)['houdini']}
        except Exception: pass
    raise RuntimeError('listener did not come back after reload; re-run start_listener.py in Houdini')

@mcp.tool()
def run_python(code: str) -> dict:
    """Run hou Python in the session (disabled unless allow_python in the config). Put a return value in `result`."""
    return _call('run_python', timeout=600, code=code)


# ---------------------------------------------------------------- see
@mcp.tool()
def quick_render(camera: str, width: int = 640, height: int | None = None, samples: int = 24,
                 look: str | None = None, save_to: str | None = None) -> Image:
    """Render a small Karma preview through a camera and return it as an image you can look at.
    height=None matches the camera's own frame shape. This is the ONLY reliable way to judge camera framing
    (the GL viewport ignores lens shift / aperture). The render is deleted once returned; save_to keeps a copy."""
    res = _call('quick_render', timeout=900, camera=camera, width=width, height=height, samples=samples, look=look)
    try:
        png = _exr_to_png(res['exr'])
    finally:
        _discard([res['exr']])
    return _deliver(png, save_to)

@mcp.tool()
def viewport_snapshot(width: int = 800, height: int = 450, network: str | None = None, camera: str | None = None,
                      frame_all: bool = False, shading: str | None = None, frames: list[float] | None = None,
                      step: int = 1, renderer: str = 'Houdini GL', save_to: str | None = None,
                      display: str | list[str] | None = None, background: str | None = None,
                      label_attrib: str | None = None, label_class: str = 'point',
                      unbypass: list[str] | None = None, view: str | None = None,
                      template: list[str] | None = None, template_shading: str = 'wire',
                      grid: bool = False, also_set: dict | None = None, tint: dict | None = None) -> Image:
    """Capture the Scene Viewer (fast OpenGL flipbook) and return it as an image. Works for /obj and /stage.
    network: show this network instead of the current one (e.g. '/obj' or '/stage').
    camera: look through a camera (/obj node path, or a USD prim path in /stage). frame_all: fit everything in view.
    shading: smooth | flat | wire | wire_shaded | hidden_line. display: temporarily show this node, or a list of nodes merged together (display flag restored after).
    background: light | grey | dark viewport background for the capture.
    view: front | back | left | right | top | bottom | persp. template: nodes shown as a ghost behind (drawn in template_shading, default wire). grid: show viewport grids (default off). also_set={parm_path: value}: set for the capture only.
    tint={node_path: [r,g,b]}: colour that node's output for the capture (temporary Color SOP; use with display).
    unbypass: switch these bypassed nodes on for the capture (works inside a locked HDA; restored exactly after).
    label_attrib: (experimental, may not draw) write an attribute's value as text on each point/prim (label_class).
    frames: [start, end] to capture a frame range (playback); returned as one labelled contact sheet.
    The artist's viewport (view, camera, shading, network, renderer, frame) is restored afterwards.
    Captures are temporary (deleted once returned, like a browser-automation screenshot); pass save_to
    (a .png path) to keep one."""
    res = _call('viewport_snapshot', timeout=600, width=width, height=height, network=network, camera=camera,
                frame_all=frame_all, shading=shading, frames=frames, step=step, renderer=renderer, display=display,
                background=background, label_attrib=label_attrib, label_class=label_class,
                unbypass=unbypass, view=view, template=template,
                template_shading=template_shading, grid=grid, also_set=also_set, tint=tint)
    shots = res['images']
    try:
        png = open(shots[0]['png'], 'rb').read() if len(shots) == 1 else _contact_sheet(shots)
    finally:
        _discard([s['png'] for s in shots])
    return _deliver(png, save_to)

def _discard(paths):
    for p in paths:
        try: os.remove(p)
        except OSError: pass

def _deliver(png, save_to=None):
    if save_to:
        path = os.path.abspath(os.path.expanduser(save_to))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f: f.write(png)
    return Image(data=png, format='png')


# ---------------------------------------------------------------- cameras
@mcp.tool()
def frame_camera(name: str, target: str | list[str], view: str = 'front', width: int = 1280, height: int = 720,
                 margin: float = 0.08, eye_height: float = 1.6, distance: float | None = None, lens_mm: float | None = None,
                 yaw_deg: float = 35.0, elevation_deg: float = 12.0, ground_y: float | None = None, dry_run: bool = False) -> dict:
    """Create or update camera /cameras/<name> framing a target (SOP path or list) by its bounding box.
    ALWAYS use this instead of setting camera transforms by hand (read houdini://camera-guide first). It applies the rules:
      view='front' -> level camera + vertical lens shift (walls stay vertical; use for buildings / facades)
      view='angle' -> three-quarter look-at (yaw/elevation), verticals converge
      aspect ratio set from width x height (portrait 720x960 for one tall house, strip 1280x540 for a row)
      ray-tests the line of sight against occluder_sops and puts the near clip in open space past any blocker
    Read the returned notes, then CHECK with quick_render. For a series of shots, fix the distance and let the
    lens be solved per subject so perspective matches."""
    return _call('frame_camera', name=name, target=target, view=view, width=width, height=height, margin=margin,
                 eye_height=eye_height, distance=distance, lens_mm=lens_mm, yaw_deg=yaw_deg,
                 elevation_deg=elevation_deg, ground_y=ground_y, dry_run=dry_run)

@mcp.tool()
def look_through(camera: str) -> dict:
    """Point the artist's viewport at a camera prim so they can see it (framing there is approximate)."""
    return _call('look_through', camera=camera)

@mcp.resource('houdini://camera-guide')
def camera_guide() -> str:
    """How to set up cameras well (lessons from the SF Victorian project). Read before any camera work."""
    return open(os.path.join(HERE, 'CAMERA_GUIDE.md'), encoding='utf-8').read()


# ---------------------------------------------------------------- video
@mcp.tool()
def viewport_video(save_to: str, frames: list[float], step: int = 1, fps: int = 24, width: int = 960, height: int = 540,
                   network: str | None = None, camera: str | None = None, frame_all: bool = False,
                   shading: str | None = None, renderer: str = 'Houdini GL', display: str | None = None,
                   background: str | None = None) -> list:
    """Flipbook a frame range from the viewport and save it as a video: save_to ending in .mp4 or .gif.
    Returns a contact sheet of the frames so you can check it, plus the saved path. Up to 240 frames."""
    res = _call('viewport_snapshot', timeout=1800, width=width, height=height, network=network, camera=camera,
                frame_all=frame_all, shading=shading, frames=frames, step=step, renderer=renderer, limit=240, display=display, background=background)
    return _finish_video(res['images'], save_to, fps, labels=False)

@mcp.tool()
def sweep_video(save_to: str, node: str, parm: str, values: list | None = None, start: float | None = None,
                end: float | None = None, count: int = 8, fps: float = 4, hold_last: int = 2, label: bool = True,
                width: int = 960, height: int = 540, network: str | None = None, camera: str | None = None,
                frame_all: bool = True, shading: str | None = None, renderer: str = 'Houdini GL',
                display: str | None = None, also_set: dict | None = None, samples: int = 24,
                background: str | None = None, unbypass: list[str] | None = None, view: str | None = None,
                template: list[str] | None = None, tint: dict | None = None) -> list:
    """Video of a parameter changing (e.g. row count 1..7, seed sweep): steps node.parm through `values`
    (or `count` evenly spaced values from start to end), captures the viewport at each, and saves an .mp4 or .gif.
    Framing is held fixed (frame_all frames once on the last value). The parameter is restored afterwards.
    display: show this node during the sweep. also_set={parm_path: value}: other parms set for the duration.
    renderer='karma' (needs camera) renders each value in Karma instead of the viewport: slow (~1-2 min a frame), final look.
    label=True writes "Parm = value" on each frame. Returns a contact sheet plus the saved path."""
    if values is None:
        if start is None or end is None: raise ValueError('give values, or start + end (+ count)')
        values = [start + (end - start) * i / max(1, count - 1) for i in range(count)]
        if all(float(v).is_integer() for v in (start, end)) and count == int(end - start) + 1:
            values = [int(round(v)) for v in values]
        else:
            values = [round(v, 4) for v in values]
    res = _call('parm_sweep', timeout=7200, node=node, parm=parm, values=values, width=width, height=height,
                network=network, camera=camera, frame_all=frame_all, shading=shading, renderer=renderer,
                display=display, also_set=also_set, samples=samples, background=background,
                unbypass=unbypass, view=view, template=template, tint=tint)
    for sh in res['images']:                       # Karma frames come back as EXR: convert to PNG once
        if 'exr' in sh:
            png = sh['exr'][:-4] + '.png'
            try:
                with open(png, 'wb') as f: f.write(_exr_to_png(sh['exr']))
            finally:
                _discard([sh['exr']])
            sh['png'] = png
    shots = res['images'] + [res['images'][-1]] * max(0, int(hold_last))
    return _finish_video(shots, save_to, fps, labels=label)

def _finish_video(shots, save_to, fps, labels):
    import json as _json
    try:
        path = _encode(shots, save_to, fps, labels)
        sheet = _contact_sheet(shots[:24] if len(shots) <= 24 else shots[::max(1, len(shots) // 24)][:24])
    finally:
        _discard(sorted({s['png'] for s in shots}))
    return [Image(data=sheet, format='png'),
            _json.dumps({'saved': path, 'frames': len(shots), 'fps': fps, 'bytes': os.path.getsize(path)})]

def _encode(shots, save_to, fps, labels):
    import subprocess, tempfile
    from PIL import Image as PILImage, ImageDraw, ImageFont
    path = os.path.abspath(os.path.expanduser(save_to))
    ext = os.path.splitext(path)[1].lower()
    if ext not in ('.mp4', '.gif'): raise ValueError('save_to must end in .mp4 or .gif')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    frames = []
    for s in shots:
        im = PILImage.open(s['png']).convert('RGB')
        if labels and s.get('label'):
            d = ImageDraw.Draw(im)
            try: font = ImageFont.truetype('/System/Library/Fonts/SFNSMono.ttf', max(14, im.height // 22))
            except Exception: font = ImageFont.load_default()
            x, y = 14, im.height - max(14, im.height // 22) - 18
            for dx, dy in ((1, 1), (-1, -1), (1, -1), (-1, 1)): d.text((x + dx, y + dy), s['label'], fill=(0, 0, 0), font=font)
            d.text((x, y), s['label'], fill=(255, 255, 255), font=font)
        frames.append(im)
    if ext == '.gif':
        frames[0].save(path, save_all=True, append_images=frames[1:], duration=int(1000 / fps), loop=0, optimize=True)
        return path
    import imageio_ffmpeg
    with tempfile.TemporaryDirectory() as td:
        for i, im in enumerate(frames): im.save(os.path.join(td, 'f%05d.png' % i))
        cmd = [imageio_ffmpeg.get_ffmpeg_exe(), '-y', '-loglevel', 'error', '-framerate', str(fps),
               '-i', os.path.join(td, 'f%05d.png'), '-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2',
               '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '20', '-movflags', '+faststart', path]
        subprocess.run(cmd, check=True)
    return path

def _contact_sheet(shots, cols=4, max_w=1600):
    import io
    from PIL import Image as PILImage, ImageDraw
    ims = [PILImage.open(s['png']).convert('RGB') for s in shots]
    cols = min(cols, len(ims)); w, h = ims[0].size
    scale = min(1.0, max_w / (cols * w)); tw, th = int(w * scale), int(h * scale)
    rows = (len(ims) + cols - 1) // cols
    sheet = PILImage.new('RGB', (cols * tw, rows * th), (20, 20, 20)); d = ImageDraw.Draw(sheet)
    for i, (im, s) in enumerate(zip(ims, shots)):
        x, y = (i % cols) * tw, (i // cols) * th
        sheet.paste(im.resize((tw, th)), (x, y)); d.text((x + 6, y + 4), s.get('label') or 'f%g' % s['frame'], fill=(255, 255, 0))
    buf = io.BytesIO(); sheet.save(buf, 'PNG'); return buf.getvalue()

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
