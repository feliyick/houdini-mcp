"""
server.py - the MCP server Claude Code talks to (runs in a normal Python 3.10+, NOT inside Houdini).
Forwards each tool call to houdini_listener.py running in your open Houdini.

Register once (from the repo folder):
    claude mcp add houdini -- "$PWD/.venv/bin/python" "$PWD/server.py"
"""
import io, json, os, socket, itertools
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
def set_parms(node: str, values: dict, dry_run: bool = False, overwrite_animation: bool = False) -> dict:
    """Set parameters. A string value starting with '=' is set as an expression. Use dry_run=True to preview.
    Refuses on animated (keyframed) parameters unless overwrite_animation=True, so animation is never lost by accident."""
    return _call('set_parms', node=node, values=values, dry_run=dry_run, overwrite_animation=overwrite_animation)

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
def hda_edit(node: str, dry_run: bool = False) -> dict:
    """Unlock an HDA instance for editing (backs up the HDA library file first). Then use create_node / connect /
    set_parms inside it, and finish with hda_save, or revert_hda to throw the edits away."""
    return _call('hda_edit', node=node, dry_run=dry_run)

@mcp.tool()
def hda_save(node: str, dry_run: bool = False) -> dict:
    """Save the unlocked HDA instance's contents into its definition (all instances update) and re-lock it."""
    return _call('hda_save', node=node, dry_run=dry_run)

@mcp.tool()
def revert_hda(node: str, dry_run: bool = False) -> dict:
    """Re-match an HDA instance to its saved definition (relocks it; discards that instance's local edits). Backed up first."""
    return _call('revert_hda', node=node, dry_run=dry_run)

@mcp.tool()
def stage_info(lop: str, root: str = '/', depth: int = 3, attrs: bool = True, limit: int = 300, attr_filter: str | None = None) -> dict:
    """Read-only: the USD stage at a LOP node, prims under `root` (to `depth` levels) with type and authored attributes."""
    return _call('stage_info', lop=lop, root=root, depth=depth, attrs=attrs, limit=limit, attr_filter=attr_filter)

@mcp.tool()
def scene_metrics(label_sop: str | None = None, slope_parm: str | None = None) -> dict:
    """Read-only: per-house stairs (count, riser, tread, width), landing/porch box, door box, ground and facade height,
    measured from the labelled geometry (defaults from the config's synth_label_sop / synth_slope_parm)."""
    return _call('scene_metrics', label_sop=label_sop, slope_parm=slope_parm)

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


# ---------------------------------------------------------------- synthetic data
SYNTH_CLASSES = {  # id: (name, preview colour)  - keep in sync with the scene's semantic_labels / street labels
    0: ('background', (0, 0, 0)), 1: ('wall', (128, 128, 128)), 2: ('window', (0, 200, 230)), 3: ('door', (0, 200, 100)),
    4: ('garage', (230, 220, 0)), 5: ('bay', (255, 0, 128)), 6: ('stairs', (255, 150, 0)), 7: ('porch', (150, 90, 230)),
    8: ('roof', (140, 80, 40)), 9: ('ornament', (255, 255, 255)), 10: ('house_number', (230, 30, 30)),
    11: ('road', (50, 50, 60)), 12: ('sidewalk', (190, 190, 180)), 13: ('vegetation', (40, 140, 40)),
    14: ('street_furniture', (60, 90, 220)), 15: ('distant_building', (200, 170, 130)),
}

@mcp.tool()
def synth_render(out_dir: str, camera: str, seeds: list[int] | None = None,
                 seed_parm: str | None = None, width: int = 640, height: int | None = None,
                 samples: int = 24, look: str | None = None, name: str = 'synth', label_samples: int = 8,
                 label_overrides: dict | None = None, ignore_box: list[float] | None = None, min_area: int = 16) -> list:
    """Synthetic training data: for each seed, render beauty + per-pixel class and instance labels (from the scene's
    semantic_id / instance_id / parent_id primvars) through `camera`, and write a COCO dataset to out_dir:
      images/<name>_<seed>.png                beauty
      labels/<name>_<seed>_class.png          class id per pixel (8-bit)   + _class_vis.png colour preview
      labels/<name>_<seed>_instance.png       instance id per pixel (16-bit)
      annotations.json                        COCO: images, categories, annotations (bbox, RLE mask, area, parent_id)
    seed_parm: the parameter the seeds go into (e.g. /obj/geo/asset/seed). Restores everything afterwards.
    Labels come from a second, opaque render (label_overrides, default from the config's synth_label_overrides) so
    glass doesn't blend IDs. ignore_box [x0,y0,x1,y1] (fractions) is marked class 255 / left out of annotations;
    under an Apprentice licence it defaults to the watermark corner. Instances smaller than min_area px are dropped.
    Returns a preview (beauty | classes | instances of the first frame) plus a summary."""
    import json as _json, numpy as np, OpenEXR
    from PIL import Image as PILImage
    res = _call('synth_render', timeout=7200, camera=camera, seeds=seeds, seed_parm=seed_parm, width=width,
                height=height, samples=samples, look=look, label_samples=label_samples, label_overrides=label_overrides)
    if ignore_box is None and res.get('apprentice'): ignore_box = [0.68, 0.80, 0.975, 0.965]
    out_dir = os.path.abspath(os.path.expanduser(out_dir))
    os.makedirs(os.path.join(out_dir, 'images'), exist_ok=True); os.makedirs(os.path.join(out_dir, 'labels'), exist_ok=True)
    coco = {'info': {'description': 'houdini-mcp synthetic data', 'camera': camera},
            'categories': [{'id': k, 'name': v[0]} for k, v in SYNTH_CLASSES.items() if k > 0],
            'images': [], 'annotations': []}
    lut = np.zeros((256, 3), np.uint8); lut[255] = (255, 0, 255)                  # 255 = ignore (magenta in preview)
    for k, (_, c) in SYNTH_CLASSES.items(): lut[k] = c
    preview = None; ann_id = 1; repaired_total = 0
    try:
        for fi, fr in enumerate(res['frames']):
            exf = OpenEXR.File(fr['labels']); ch = {}
            for part in exf.parts:                                   # Karma writes AOVs into extra EXR parts
                for k, v in part.channels.items(): ch.setdefault(k, v)
            def layer(nm):
                for key in (nm, nm + '.R', nm + '.r', nm + '.Y'):
                    if key in ch: return ch[key].pixels.astype(np.float32).reshape(ch[key].pixels.shape[:2])
                cands = [k for k in ch if k.split('.')[0] == nm]
                if cands: return ch[cands[0]].pixels.astype(np.float32)[..., 0] if ch[cands[0]].pixels.ndim == 3 else ch[cands[0]].pixels.astype(np.float32)
                raise RuntimeError('EXR has no %s layer (has %s)' % (nm, sorted(ch)))
            sem = np.rint(layer('semantic_id')).astype(np.int32); ins = np.rint(layer('instance_id')).astype(np.int32)
            par = np.rint(layer('parent_id')).astype(np.int32)
            h0, w0 = sem.shape; keep = np.ones((h0, w0), bool)
            if ignore_box:
                keep[int(ignore_box[1] * h0):int(ignore_box[3] * h0), int(ignore_box[0] * w0):int(ignore_box[2] * w0)] = False
            repaired = 0
            if fr.get('ids'):                    # safety net: should find ~0 pixels now the label pass is 1 spp
                sem, ins, par, repaired = _repair_ids(sem, ins, fr['ids'], keep)
            beauty = _exr_to_png(fr['beauty'])
            stem = '%s_%s' % (name, fr['seed'] if fr['seed'] is not None else fi)
            h, w = sem.shape
            if ignore_box:                                                   # e.g. the Apprentice watermark
                bx0, by0, bx1, by1 = int(ignore_box[0] * w), int(ignore_box[1] * h), int(ignore_box[2] * w), int(ignore_box[3] * h)
                sem[by0:by1, bx0:bx1] = 255; ins[by0:by1, bx0:bx1] = 0; par[by0:by1, bx0:bx1] = 0
            open(os.path.join(out_dir, 'images', stem + '.png'), 'wb').write(beauty)
            dkey = next((k for k in ch if k.split('.')[0] in ('depth', 'hitPz', 'Pz')), None)
            if dkey is not None:                                             # depth: metres * 256 in uint16 (KITTI style)
                d = ch[dkey].pixels.astype(np.float32); d = d[..., 0] if d.ndim == 3 else d
                d = np.abs(d); d[~np.isfinite(d) | (d >= 255.99)] = 0                # sky / beyond range -> 0 = no depth
                PILImage.fromarray(np.clip(d * 256.0, 0, 65535).astype(np.uint16)).save(os.path.join(out_dir, 'labels', stem + '_depth.png'))
            nkeys = sorted(k for k in ch if k.split('.')[0] in ('N', 'hitN'))
            if nkeys:
                if len(nkeys) == 1 and ch[nkeys[0]].pixels.ndim == 3: n = ch[nkeys[0]].pixels.astype(np.float32)[..., :3]
                else: n = np.stack([ch[k].pixels.astype(np.float32) for k in nkeys[:3]], -1)
                PILImage.fromarray(np.clip((n * 0.5 + 0.5) * 255, 0, 255).astype(np.uint8)).save(os.path.join(out_dir, 'labels', stem + '_normal.png'))
            PILImage.fromarray(sem.clip(0, 255).astype(np.uint8)).save(os.path.join(out_dir, 'labels', stem + '_class.png'))
            vis = lut[sem.clip(0, 255)]
            PILImage.fromarray(vis).save(os.path.join(out_dir, 'labels', stem + '_class_vis.png'))
            PILImage.fromarray(ins.clip(0, 65535).astype(np.uint16)).save(os.path.join(out_dir, 'labels', stem + '_instance.png'))
            img_id = fi + 1
            repaired_total += repaired
            coco['images'].append({'id': img_id, 'file_name': 'images/%s.png' % stem, 'width': w, 'height': h, 'seed': fr['seed'],
                                   'ignore_box': [int(v * (w if k % 2 == 0 else h)) for k, v in enumerate(ignore_box)] if ignore_box else None,
                                   'camera': fr.get('camera'), 'look': look, 'camera_path': camera,
                                   'depth_file': 'labels/%s_depth.png' % stem, 'depth_encoding': 'uint16 = metres * 256'})
            boxes3d = fr.get('boxes_3d') or {}
            if fr.get('scene'):                                              # per-seed robotics/simulation numbers
                os.makedirs(os.path.join(out_dir, 'scene'), exist_ok=True)
                with open(os.path.join(out_dir, 'scene', 'seed_%s.json' % fr['seed']), 'w') as sf: _json.dump(fr['scene'], sf, indent=1)
                coco['images'][-1]['scene_file'] = 'scene/seed_%s.json' % fr['seed']
            for iid in np.unique(ins):
                if iid <= 0: continue
                m = ins == iid
                if m.sum() < min_area: continue
                classes = sem[m]; classes = classes[classes != 255]; core = classes[classes != 9]
                if not classes.size: continue                 # ornament rides on its parent object
                cat = int(np.bincount(core if core.size else classes).argmax())
                ys, xs = np.nonzero(m)
                x0, y0, x1, y1 = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
                flat = m.flatten(order='F').astype(np.uint8)                   # COCO uncompressed RLE: column-major
                change = np.flatnonzero(np.diff(np.concatenate([[0], flat, [0]])))
                counts = np.diff(np.concatenate([[0], change])).tolist()
                if flat[0] == 1: counts = [0] + counts
                pid = int(np.bincount(par[m].clip(0)).argmax())
                coco['annotations'].append({'id': ann_id, 'image_id': img_id, 'category_id': cat, 'instance_id': int(iid),
                                            'bbox_3d': boxes3d.get(str(int(iid))),
                                            'parent_instance_id': pid, 'bbox': [x0, y0, x1 - x0, y1 - y0],
                                            'area': int(m.sum()), 'iscrowd': 0,
                                            'segmentation': {'size': [h, w], 'counts': counts}})
                ann_id += 1
            if preview is None:
                ivis = np.zeros((h, w, 3), np.uint8)
                for iid in np.unique(ins):
                    if iid > 0: ivis[ins == iid] = ((iid * 97) % 255, (iid * 57) % 255, (iid * 173) % 255)
                b = PILImage.open(io.BytesIO(beauty)).convert('RGB')
                sheet = PILImage.new('RGB', (w * 3, h)); sheet.paste(b, (0, 0)); sheet.paste(PILImage.fromarray(vis), (w, 0))
                sheet.paste(PILImage.fromarray(ivis), (w * 2, 0))
                buf = io.BytesIO(); sheet.save(buf, 'PNG'); preview = buf.getvalue()
    finally:
        _discard([fr[k] for fr in res['frames'] for k in ('beauty', 'labels')])
    for a in coco['annotations']:                                              # parent instance -> parent annotation id
        a['parent_id'] = next((b['id'] for b in coco['annotations'] if b['image_id'] == a['image_id'] and b['instance_id'] == a['parent_instance_id']), 0) if a['parent_instance_id'] else 0
    path = os.path.join(out_dir, 'annotations.json')
    with open(path, 'w') as f: _json.dump(coco, f)
    summary = {'out_dir': out_dir, 'images': len(coco['images']), 'annotations': len(coco['annotations']),
               'repaired_pixels': repaired_total,
               'per_class': {}}
    for a in coco['annotations']:
        nm = SYNTH_CLASSES[a['category_id']][0]; summary['per_class'][nm] = summary['per_class'].get(nm, 0) + 1
    return [Image(data=preview, format='png'), _json.dumps(summary)]

def _repair_ids(sem, ins, triples, keep=None, iters=8):
    """Pixels whose (instance, class) pair isn't on the geometry are blends of neighbours: replace each with the most
    common valid pair among its 8 neighbours (repeat to fill wider bands). Parent comes from the geometry lookup."""
    import numpy as np
    valid = {(a, b): c for a, b, c in triples}
    valid[(0, 0)] = 0
    code = ins.astype(np.int64) * 64 + sem.clip(0, 63)
    vcodes = np.array(sorted(a * 64 + b for a, b in valid), np.int64)
    bad = ~np.isin(code, vcodes)
    if keep is not None: bad &= keep                                      # the ignore region is not counted or fixed
    start = int(bad.sum())
    h, w = code.shape
    for _ in range(iters):
        if not bad.any(): break
        ys, xs = np.nonzero(bad)
        new = code.copy(); fixed = np.zeros_like(bad)
        for y, x in zip(ys, xs):
            nb = code[max(0, y - 1):y + 2, max(0, x - 1):x + 2][~bad[max(0, y - 1):y + 2, max(0, x - 1):x + 2]]
            if nb.size:
                vals, cnts = np.unique(nb, return_counts=True); new[y, x] = vals[cnts.argmax()]; fixed[y, x] = True
        code = new; bad = bad & ~fixed
    code[bad] = 0                                                         # unreachable leftovers -> background
    ins2 = (code // 64).astype(np.int32); sem2 = (code % 64).astype(np.int32)
    par2 = np.zeros_like(ins2)
    for (a, b), c in valid.items():
        if c: par2[ins2 == a] = c
    return sem2, ins2, par2, start

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
