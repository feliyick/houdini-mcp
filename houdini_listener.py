"""
houdini_listener.py - runs INSIDE your open Houdini session.

A tiny JSON-over-TCP command server (127.0.0.1:9876, localhost only). The MCP server (server.py) connects
to it and asks it to do things; every `hou` call runs on Houdini's main thread.

Start it from the Python Shell (or a shelf tool): see start_listener.py.
Per-project settings: $HIP/houdini_mcp_config.json (see examples/).

Guardrails:
  * writes only under the configured allowed_roots
  * a backup of the scene before the first write of every call  (hou.hipFile.saveAsBackup - your file is untouched)
  * every write is ONE undo group: Cmd+Z reverts one agent action
  * dry_run on every write tool: report what would change, change nothing
  * run_python is OFF unless the config turns it on
  * every call is appended to $HIP/mcp/audit.log
"""
import json, math, os, socket, sys, tempfile, threading, time, traceback

import hou
import hdefereval

HOST, PORT = '127.0.0.1', 9876

# Per-project settings live in $HIP/houdini_mcp_config.json (see examples/), so the tool works on any scene.
DEFAULT_CONFIG = {
    "allowed_roots": ["/obj", "/stage", "/out"],   # writes are refused outside these
    "allow_python": False,                          # run_python tool
    "check_nodes": [],                              # cook_and_check defaults (empty = every displayed /obj node)
    "watch_parms": {},                              # scene_info: {"/obj/geo1/asset": ["seed", "style"]}
    "karma_lop": "",                                # quick_render: the Karma LOP to render through
    "look_switch": "",                              # set_look: a Switch LOP choosing the lighting look
    "looks": {},                                    # set_look: {"sunny": 4, ...}
    "look_parms": {},                               # set_look: extra parm values per look {"night": {"/obj/x/ctl/night_sky": 1}}
    "auto_save": False,                             # save the .hip after every successful write (backup first)
    "allow_reload": False,                          # reload_listener tool: pick up new listener code from disk
    "camera_insert_before": "",                     # frame_camera: new camera LOPs are wired in above this LOP (default: karma_lop)
    "occluder_sops": [],                            # frame_camera: SOP outputs to ray-test for things between camera and target
    "front_axis": [0, 0, 1]                         # frame_camera: direction the facades face (camera stands out along this)
}
# commands that change the scene (auto_save runs after these when they succeed and weren't a dry run)
WRITE_CMDS = {'set_parms', 'create_node', 'connect', 'set_look', 'run_python', 'undo', 'redo', 'frame_camera'}
CONFIG = dict(DEFAULT_CONFIG)

def load_config():
    CONFIG.clear(); CONFIG.update(DEFAULT_CONFIG)
    path = hou.text.expandString('$HIP/houdini_mcp_config.json')
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f: CONFIG.update(json.load(f))
    return path if os.path.exists(path) else None


# ----------------------------------------------------------------------------- helpers
def _hip(*p):
    return os.path.join(hou.text.expandString('$HIP'), *p)

def _scratch(*p):
    """Throwaway images (viewport captures, preview renders) live in the OS temp folder, never the project.
    The MCP server deletes each one right after reading it; start() sweeps anything older than a day."""
    d = os.path.join(tempfile.gettempdir(), 'houdini-mcp', *p)
    os.makedirs(d, exist_ok=True)
    return d

def _sweep_scratch(max_age_h=24):
    root = os.path.join(tempfile.gettempdir(), 'houdini-mcp')
    n = 0
    for dirpath, _, files in os.walk(root):
        for f in files:
            fp = os.path.join(dirpath, f)
            try:
                if time.time() - os.path.getmtime(fp) > max_age_h * 3600: os.remove(fp); n += 1
            except OSError: pass
    return n

def _audit(cmd, args, ok, note=''):
    os.makedirs(_hip('mcp'), exist_ok=True)
    with open(_hip('mcp', 'audit.log'), 'a', encoding='utf-8') as f:
        f.write('%s\t%s\t%s\t%s\t%s\n' % (time.strftime('%Y-%m-%d %H:%M:%S'), 'OK' if ok else 'ERR', cmd,
                                          json.dumps(args)[:400], note[:300]))

def _node(path):
    n = hou.node(path)
    if n is None:
        raise ValueError('no node at %s' % path)
    return n

def _writable(path):
    roots = CONFIG['allowed_roots']
    if not any(path == r or path.startswith(r.rstrip('/') + '/') for r in roots):
        raise PermissionError('%s is outside the allowed roots %s' % (path, roots))

def _backup():
    try:
        return hou.hipFile.saveAsBackup()
    except Exception as e:                      # untitled scene etc.
        return 'backup failed: %s' % e

def _karma_lop():
    """The Karma LOP to render through: the config's, else the first Karma node found under /stage."""
    if CONFIG['karma_lop']:
        return _node(CONFIG['karma_lop'])
    stage = hou.node('/stage')
    for n in (stage.allSubChildren() if stage else ()):
        if n.type().name().split('::')[0] == 'karma':
            return n
    raise ValueError('no Karma LOP found: add one in /stage, or set "karma_lop" in houdini_mcp_config.json')

def _parm_value(p):
    try:
        return {'expr': p.expression(), 'value': p.eval()}
    except hou.OperationFailed:
        v = p.eval()
        return v if isinstance(v, (int, float, str)) else str(v)

def _jsonable(v):
    if isinstance(v, (list, tuple)): return [_jsonable(x) for x in v]
    if isinstance(v, dict): return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (int, float, str, bool)) or v is None: return v
    return str(v)


# ----------------------------------------------------------------------------- commands (main thread)
def cmd_ping():
    return {'houdini': hou.applicationVersionString(), 'hip': hou.hipFile.path(), 'config': CONFIG}

def cmd_scene_info():
    out = {'hip': hou.hipFile.path(), 'houdini': hou.applicationVersionString(),
           'unsaved_changes': hou.hipFile.hasUnsavedChanges(), 'frame': hou.frame(),
           'obj': [n.name() for n in hou.node('/obj').children()]}
    if hou.node('/stage'):
        out['stage'] = [n.name() for n in hou.node('/stage').children()][:200]
    out['watched'] = {}
    for path, names in CONFIG['watch_parms'].items():
        n = hou.node(path)
        if n: out['watched'][path] = {nm: _parm_value(n.parm(nm)) for nm in names if n.parm(nm)}
    if CONFIG['look_switch'] and hou.node(CONFIG['look_switch']):
        idx = hou.node(CONFIG['look_switch']).parm('input').eval()
        out['look'] = next((k for k, v in CONFIG['looks'].items() if v == idx), idx)
    try:
        st = _karma_lop().stage()
        out['cameras'] = sorted(str(p.GetPath()) for p in st.Traverse() if p.GetTypeName() == 'Camera')
    except Exception:
        pass
    return out

def cmd_list_nodes(path='/obj', recursive=False, limit=300):
    root = _node(path)
    nodes = root.allSubChildren() if recursive else root.children()
    out = []
    for n in nodes[:limit]:
        e = {'path': n.path(), 'type': n.type().name(), 'errors': bool(n.errors()), 'warnings': bool(n.warnings())}
        ins = [i.path() if i else None for i in n.inputs()]
        if any(ins): e['inputs'] = ins                      # wiring, so the agent can follow the graph
        try:
            if n.isDisplayFlagSet(): e['display'] = True
        except AttributeError: pass
        out.append(e)
    return {'count': len(nodes), 'nodes': out}

def cmd_get_parms(node, names=None, contains=None):
    n = _node(node)
    out = {}
    for p in n.parms():
        nm = p.name()
        if names and nm not in names: continue
        if contains and contains.lower() not in (nm + ' ' + p.parmTemplate().label()).lower(): continue
        out[nm] = _parm_value(p)
        if len(out) >= 400: break
    return out

def cmd_set_parms(node, values, dry_run=False):
    n = _node(node); _writable(n.path())
    changes = {}
    for nm, v in values.items():
        p = n.parm(nm) or (n.parmTuple(nm) if isinstance(v, (list, tuple)) else None)
        if p is None: raise ValueError('%s has no parm %s' % (node, nm))
        before = _parm_value(p) if isinstance(p, hou.Parm) else list(p.eval())
        changes[nm] = {'from': before, 'to': v}
    if dry_run:
        return {'dry_run': True, 'would_change': changes}
    backup = _backup()
    with hou.undos.group('MCP: set_parms %s' % n.name()):
        for nm, v in values.items():
            p = n.parm(nm)
            if p is not None:
                p.deleteAllKeyframes()
                if isinstance(v, str) and v.startswith('='):
                    p.setExpression(v[1:])
                else:
                    p.set(v)
            else:
                n.parmTuple(nm).set(v)
    return {'changed': changes, 'backup': backup}

def cmd_create_node(parent, node_type, name=None, input_from=None, dry_run=False):
    par = _node(parent); _writable(par.path())
    if dry_run:
        return {'dry_run': True, 'would_create': '%s/%s (%s)' % (parent, name or node_type, node_type)}
    backup = _backup()
    with hou.undos.group('MCP: create %s' % node_type):
        n = par.createNode(node_type, name)
        if input_from: n.setInput(0, _node(input_from))
        n.moveToGoodPosition()
    return {'created': n.path(), 'backup': backup}

def cmd_connect(src, dst, input_index=0, dry_run=False):
    d = _node(dst); _writable(d.path()); s = _node(src)
    if dry_run:
        return {'dry_run': True, 'would_connect': '%s -> %s[%d]' % (src, dst, input_index)}
    backup = _backup()
    with hou.undos.group('MCP: connect'):
        d.setInput(input_index, s)
    return {'connected': '%s -> %s[%d]' % (src, dst, input_index), 'backup': backup}

def cmd_cook_and_check(paths=None):
    paths = paths or CONFIG['check_nodes'] or [n.path() for n in hou.node('/obj').children() if n.isDisplayFlagSet()]
    bad = []
    for path in paths:
        n = hou.node(path)
        if n is None: continue
        t = time.time()
        try: n.cook(force=True)
        except hou.OperationFailed: pass
        dt = time.time() - t
        scope = [n] + (list(n.allSubChildren()) if n.isSubNetwork() or n.type().definition() else [])
        for c in scope:
            if c.errors(): bad.append({'node': c.path(), 'errors': [e[:300] for e in c.errors()]})
        bad.append({'node': path, 'cook_seconds': round(dt, 2)}) if dt > 10 else None
    errs = [b for b in bad if 'errors' in b]
    return {'ok': not errs, 'problems': bad}

def cmd_geometry_stats(node):
    g = _node(node).geometry()
    if g is None: raise ValueError('%s has no geometry' % node)
    bb = g.boundingBox()
    out = {'points': len(g.points()), 'prims': len(g.prims()),
           'bbox_min': list(bb.minvec()), 'bbox_max': list(bb.maxvec()),
           'prim_groups': {gr.name(): len(gr.prims()) for gr in g.primGroups()},
           'prim_attribs': [a.name() for a in g.primAttribs()], 'point_attribs': [a.name() for a in g.pointAttribs()],
           'detail_attribs': {a.name(): _jsonable(g.attribValue(a.name())) for a in g.globalAttribs()}}
    for key in ('paint', 'material', 'name'):
        a = g.findPrimAttrib(key)
        if a and a.dataType() == hou.attribData.String:
            counts = {}
            for v in g.primStringAttribValues(key): counts[v] = counts.get(v, 0) + 1
            out['count_by_' + key] = dict(sorted(counts.items(), key=lambda kv: -kv[1])[:40])
    return out

def cmd_dump_attribute(node, attrib, cls='prim', limit=200):
    g = _node(node).geometry()
    elems = {'prim': g.prims, 'point': g.points}[cls]()[:limit]
    return [{'i': e.number(), 'value': _jsonable(e.attribValue(attrib))} for e in elems]

def _snapshot(parm):
    """Exact restorable state of a parm: expression, raw string (keeps $HIP etc.), or value."""
    try: return ('expr', parm.expression(), parm.expressionLanguage())
    except hou.OperationFailed: pass
    if parm.parmTemplate().type() == hou.parmTemplateType.String:
        return ('str', parm.unexpandedString(), None)
    return ('val', parm.eval(), None)

def _restore(parm, snap):
    kind, v, lang = snap
    parm.deleteAllKeyframes()
    if kind == 'expr': parm.setExpression(v, lang)
    else: parm.set(v)

def _look_parms(look):
    """All parms a look touches: the switch input + its extra look_parms."""
    out = [_node(CONFIG['look_switch']).parm('input')]
    for path in CONFIG['look_parms'].get(look, {}):
        p = hou.parm(path)
        if p is not None: out.append(p)
    return out

def cmd_set_look(look):
    if not CONFIG['look_switch']: raise ValueError('no look_switch configured in houdini_mcp_config.json')
    if look not in CONFIG['looks']: raise ValueError('unknown look %r, have %s' % (look, sorted(CONFIG['looks'])))
    _writable(CONFIG['look_switch'])
    with hou.undos.group('MCP: set_look %s' % look):
        _node(CONFIG['look_switch']).parm('input').set(CONFIG['looks'][look])
        for path, v in CONFIG['look_parms'].get(look, {}).items():
            p = hou.parm(path)
            if p is not None: p.set(v)
    return {'look': look}

def cmd_quick_render(camera, width=640, height=None, samples=24, look=None):
    """Karma render (blocking), so the agent can SEE the result. Returns the EXR path.
    Everything it changes (Karma settings, and the look if one is given) is put back exactly afterwards."""
    karma = _karma_lop()
    if height is None:                          # match the camera's own frame shape (aperture ratio)
        try:
            prim = karma.stage().GetPrimAtPath(camera)
            ha, va = prim.GetAttribute('horizontalAperture').Get(), prim.GetAttribute('verticalAperture').Get()
            height = int(round(width * va / ha))
        except Exception:
            height = int(round(width * 9 / 16))
    with hou.undos.disabler():                  # the preview ROP is tooling, not part of the artist's undo history
        rop = hou.node('/out/houdini_mcp_rop') or hou.node('/out').createNode('usdrender', 'houdini_mcp_rop')
        rop.parm('loppath').set(karma.path())
        if rop.parm('renderer'): rop.parm('renderer').set('BRAY_HdKarma')
        for p in rop.parms():
            lab = (p.parmTemplate().label() + ' ' + p.name()).lower()
            if p.parmTemplate().type() != hou.parmTemplateType.Toggle: continue
            if 'mplay' in lab or 'background' in lab or 'async' in lab: p.set(0)
            elif 'wait' in lab or 'block' in lab: p.set(1)
    out = os.path.join(_scratch('renders'), '%s_%s.exr' % (time.strftime('%H%M%S'), camera.split('/')[-1]))
    touched = [karma.parm(nm) for nm in ('camera', 'picture', 'resolutionx', 'resolutiony', 'pathtracedsamples') if karma.parm(nm)]
    if look:
        if not CONFIG['look_switch']: raise ValueError('no look_switch configured in houdini_mcp_config.json')
        touched += _look_parms(look)
    saved = [(p, _snapshot(p)) for p in touched]
    t = time.time()
    # temporary changes, put back below: kept OFF the undo stack so Cmd+Z never steps into render settings
    with hou.undos.disabler():
        try:
            if look: cmd_set_look(look)
            karma.parm('camera').set(camera); karma.parm('picture').set(out)
            karma.parm('resolutionx').set(width); karma.parm('resolutiony').set(height)
            if karma.parm('pathtracedsamples'): karma.parm('pathtracedsamples').set(samples)
            rop.render(frame_range=(1, 1), verbose=False)
            while not os.path.exists(out) and time.time() - t < 600: time.sleep(1)
        finally:
            for p, snap in saved: _restore(p, snap)
    if not os.path.exists(out):
        raise RuntimeError('Karma produced no image after %ds (check the ROP /out/houdini_mcp_rop)' % (time.time() - t))
    return {'exr': out, 'seconds': round(time.time() - t, 1)}

# ----------------------------------------------------------------------------- cameras
# Rules learned on the SF Victorian project, built into one tool so an agent can't skip them:
#  * derive the camera from the TARGET's bounding box, never hard-code coordinates
#  * architecture: keep the camera LEVEL and use vertical LENS SHIFT, so walls stay vertical
#  * set the camera's aspect ratio to the render resolution, or the vertical aperture silently stays 16:9
#  * ray-test the line of sight; if geometry sits between camera and target, clip it with the near plane,
#    placing the plane in open space (never slicing through a tree or lamp), and report it
#  * judge framing with quick_render, NOT the GL viewport (flipbooks ignore lens settings)
_HAP = 20.955

def _bbox_of(target):
    bb = None
    for path in (target if isinstance(target, list) else [target]):
        g = _node(path).geometry()
        b = g.boundingBox()
        if bb is None: bb = b
        else: bb.enlargeToContain(b)
    return bb

def _first_hit(geos, orig, d, max_dist):
    best = None
    for g in geos:
        pos, nrm, uvw = hou.Vector3(), hou.Vector3(), hou.Vector3()
        pr = g.intersect(orig, d, pos, nrm, uvw, None, 0.01, max_dist)
        if pr >= 0:
            dist = (pos - orig).length()
            if best is None or dist < best: best = dist
    return best

def _near_clip(eye, targets, d, bb=None):
    """Find occluders between camera and target. Walks each ray all the way to the target (the first hit inside the
    target's bbox), so long lenses that stand far back are handled too. Returns (near_clip or None, notes); the
    clip goes in the open gap between the last occluder and the target, never inside an occluder."""
    geos = [hou.node(p).geometry() for p in CONFIG['occluder_sops'] if hou.node(p) and hou.node(p).geometry()]
    if not geos: return None, ['no occluder_sops configured: line of sight not checked']
    def in_target(pt):
        if bb is None: return False
        mn, mx = bb.minvec(), bb.maxvec(); e = 0.05
        return all(mn[i] - e <= pt[i] <= mx[i] + e for i in range(3))
    last_occ, first_tgt = 0.0, None
    for t in targets:
        dirv = (t - eye); dist = dirv.length(); dirv = dirv.normalized()
        travelled = 0.0
        for _ in range(200):
            o = eye + dirv * travelled
            h = _first_hit(geos, o, dirv, dist + 1.0 - travelled)
            if h is None: break
            travelled += h
            if in_target(eye + dirv * travelled):
                first_tgt = travelled if first_tgt is None else min(first_tgt, travelled); break
            last_occ = max(last_occ, travelled); travelled += 0.02
    if last_occ <= 0: return None, ['line of sight clear']
    gap_end = first_tgt if first_tgt is not None else d
    if gap_end - last_occ < 0.2:
        return last_occ + 0.1, ['occluder within 0.2 m of the target (%.1f m): near clip set right behind it; consider another angle' % last_occ]
    near = last_occ + min(1.0, (gap_end - last_occ) * 0.5)
    return near, ['geometry between camera and target up to %.1f m: near clip set to %.1f m, in the open gap before the target (%.1f m)' % (last_occ, near, gap_end)]

def cmd_frame_camera(name, target, view='front', width=1280, height=720, margin=0.08, eye_height=1.6,
                     distance=None, lens_mm=None, yaw_deg=35.0, elevation_deg=12.0, ground_y=None, dry_run=False):
    """Create/update a camera LOP framing `target` (a SOP path, or list of them).
    view='front': level, centred, lens-shifted (architecture).  view='angle': three-quarter look-at, yaw/elevation."""
    bb = _bbox_of(target)
    mn, mx = bb.minvec(), bb.maxvec(); c = bb.center()
    ax = hou.Vector3(CONFIG['front_axis']).normalized()
    side = ax.cross(hou.Vector3(0, 1, 0)).normalized()
    W = abs((mx - mn).dot(side)); H = mx[1] - mn[1]
    Wm, Hm = W * (1 + 2 * margin), H * (1 + 2 * margin)
    vap = _HAP * height / float(width)
    gy = mn[1] if ground_y is None else ground_y
    notes = []
    if view == 'front':
        face = max((mx - c).dot(ax), (mn - c).dot(ax))                  # front face along the axis
        if distance is None:
            f = lens_mm or 35.0
            distance = max(f * Wm / _HAP, f * Hm / vap)
        f = lens_mm or min(_HAP * distance / Wm, vap * distance / Hm)
        eye = c + ax * (face + distance); eye = hou.Vector3(eye[0], gy + eye_height, eye[2])
        yaw = math.degrees(math.atan2(ax[0], ax[2])); pitch = 0.0
        shift = (c[1] - eye[1]) * f / distance                          # vertical lens shift, mm
        aim = [c]
    else:
        r = math.radians(yaw_deg); e = math.radians(elevation_deg)
        dirv = (ax * math.cos(r) + side * math.sin(r)); dirv = hou.Vector3(dirv[0] * math.cos(e), math.sin(e), dirv[2] * math.cos(e))
        f = lens_mm or 35.0
        if distance is None:
            # fit the 8 bbox corners on each axis separately (a bounding sphere wastes most of a wide frame)
            fw = -dirv.normalized()
            rt = fw.cross(hou.Vector3(0, 1, 0)).normalized(); up = rt.cross(fw).normalized()
            k = 1.0 + 2 * margin; distance = 0.0
            for cx in (mn[0], mx[0]):
                for cy in (mn[1], mx[1]):
                    for cz in (mn[2], mx[2]):
                        q = hou.Vector3(cx, cy, cz) - c
                        distance = max(distance, q.dot(dirv) + abs(q.dot(rt)) * k * 2 * f / _HAP,
                                       q.dot(dirv) + abs(q.dot(up)) * k * 2 * f / vap)
        eye = c + dirv * distance
        if eye[1] < gy + eye_height: eye = hou.Vector3(eye[0], gy + eye_height, eye[2])
        dv = c - eye
        yaw = math.degrees(math.atan2(-dv[0], -dv[2])); pitch = math.degrees(math.atan2(dv[1], math.hypot(dv[0], dv[2])))
        shift = 0.0; aim = [c]
        notes.append('angle view: verticals will converge (tilted camera); use view="front" for straight walls')
    corners = [hou.Vector3(x, y, z) for x in (mn[0], mx[0]) for y in (mn[1], mx[1]) for z in (mn[2], mx[2])]
    near, n2 = _near_clip(eye, aim + corners, (c - eye).length(), bb); notes += n2
    plan = {'camera': '/cameras/' + name, 'position': list(eye), 'rotate': [pitch, yaw, 0], 'focal_mm': round(f, 2),
            'aperture_mm': [_HAP, round(vap, 3)], 'aspect': [width, height], 'vertical_shift_mm': round(shift, 3),
            'near_clip': near, 'distance_m': round(distance, 2), 'target_size_m': [round(W, 2), round(H, 2)], 'notes': notes,
            'preview': 'quick_render(camera="/cameras/%s", width=%d, height=%d)' % (name, min(width, 640), int(min(width, 640) * height / width))}
    if dry_run:
        plan['dry_run'] = True; return plan
    node_name = 'mcp_cam_' + name
    stage_net = hou.node('/stage')
    before = hou.node(CONFIG['camera_insert_before'] or CONFIG['karma_lop'] or '')
    if before is None: raise ValueError('set camera_insert_before or karma_lop in houdini_mcp_config.json')
    _writable(stage_net.path() + '/' + node_name)
    backup = _backup()
    with hou.undos.group('MCP: frame_camera %s' % name):
        cam = stage_net.node(node_name)
        if cam is None:
            cam = stage_net.createNode('camera', node_name)
            cam.setInput(0, before.inputs()[0] if before.inputs() else None); before.setInput(0, cam)
            cam.setPosition(before.position() + hou.Vector2(2, 0.6))
        def sp(nm, v):
            p = cam.parm(nm)
            if p is None: return
            ctl = cam.parm(nm + '_control')
            if ctl is not None:
                try: ctl.set('set')
                except Exception: pass
            p.set(v)
        cam.parm('primpath').set('/cameras/' + name)
        cam.parmTuple('t').set(tuple(eye)); cam.parmTuple('r').set((pitch, yaw, 0))
        sp('focalLength', f); sp('horizontalAperture', _HAP); sp('verticalAperture', vap)
        sp('aspectratiox', float(width)); sp('aspectratioy', float(height))
        sp('verticalApertureOffset', shift); sp('horizontalApertureOffset', 0.0)
        if cam.parmTuple('clippingRange'): cam.parmTuple('clippingRange').set((near or 0.1, 100000.0))
        sp('focusDistance', (c - eye).length())
    plan['node'] = cam.path(); plan['backup'] = backup
    return plan

def cmd_look_through(camera):
    """Point the Scene Viewer at a camera prim (e.g. /cameras/cam_front) so the artist sees it."""
    sv = hou.ui.paneTabOfType(hou.paneTabType.SceneViewer)
    if sv is None: raise ValueError('no Scene Viewer open')
    sv.curViewport().setCamera(camera)
    return {'viewing': camera, 'note': 'the GL viewport ignores lens shift/aperture; trust quick_render for framing'}

def cmd_save_backup():
    return {'backup': _backup()}

# ----------------------------------------------------------------------------- viewport
def _shading_modes():
    return {'smooth': hou.glShadingType.Smooth, 'flat': hou.glShadingType.Flat, 'wire': hou.glShadingType.Wire,
            'wire_shaded': hou.glShadingType.SmoothWire, 'hidden_line': hou.glShadingType.HiddenLineInvisible}

def _display_sets():
    return [v for v in hou.displaySetType.__dict__.values() if isinstance(v, hou.EnumValue)]

def _viewport_state(vp):
    """Everything a capture may change on one viewport. Read it ALL before changing anything: some display
    sets are linked, so changing one changes another."""
    cam = None
    try: cam = vp.camera()
    except Exception: pass
    if cam is None:
        try: cam = vp.cameraPath() or None
        except Exception: pass
    shade = []
    for ds in _display_sets():
        try: shade.append((ds, vp.settings().displaySet(ds).shadedMode()))
        except Exception: pass
    scheme = None
    try: scheme = vp.settings().colorScheme()
    except Exception: pass
    return {'cam': cam, 'view': vp.defaultCamera().stash(), 'shade': shade, 'scheme': scheme}

def _put_viewport_state(vp, st):
    if st.get('scheme') is not None:
        try: vp.settings().setColorScheme(st['scheme'])
        except Exception: pass
    for ds, mode in st['shade']:
        try: vp.settings().displaySet(ds).setShadedMode(mode)
        except Exception: pass
    if st['cam'] is not None:
        try: vp.setCamera(st['cam']); return
        except Exception: pass
    vp.useDefaultCamera(); vp.setDefaultCamera(st['view'])

def _set_background(vp, background):
    """light | grey | dark viewport background (Houdini's colour schemes)."""
    if not background: return
    schemes = {'light': hou.viewportColorScheme.Light, 'grey': hou.viewportColorScheme.Grey,
               'dark': hou.viewportColorScheme.Dark}
    if background not in schemes: raise ValueError('background must be light, grey or dark')
    vp.settings().setColorScheme(schemes[background])

def _frame(vp, display=None):
    """Frame the shown node's bounding box if one was given (frameAll would also fit ground planes, skies...)."""
    if display:
        try:
            g = _node(display).geometry()
            if g is not None: vp.frameBoundingBox(g.boundingBox()); return
        except Exception: pass
    vp.frameAll()

def _show(path):
    """Temporarily move the display flag to `path` (outside the undo history). Returns a function that puts it back."""
    n = _node(path)
    sibs = [c for c in n.parent().children() if hasattr(c, 'isDisplayFlagSet')]
    was = [c for c in sibs if c.isDisplayFlagSet()]
    with hou.undos.disabler(): n.setDisplayFlag(True)
    def back():
        with hou.undos.disabler():
            for c in was: c.setDisplayFlag(True)
    return back

def cmd_viewport_snapshot(width=800, height=450, network=None, camera=None, frame_all=False, shading=None,
                          frames=None, step=1, renderer='Houdini GL', limit=48, display=None, background=None):
    """Capture the Scene Viewer with a flipbook (OpenGL, fast). Works in /obj and /stage.
    frames=[start, end] captures a range (playback). Everything changed for the capture (network, camera, view,
    shading, renderer, frame) is put back afterwards; the artist's viewport is left as it was."""
    sv = hou.ui.paneTabOfType(hou.paneTabType.SceneViewer)
    if sv is None: raise ValueError('no Scene Viewer pane is open')
    if shading and shading not in _shading_modes():
        raise ValueError('shading must be one of %s' % sorted(_shading_modes()))
    f0, f1 = (frames if frames else (hou.frame(), hou.frame()))
    step = max(1, int(step))
    count = int((f1 - f0) // step) + 1
    limit = min(int(limit), 240)
    if count > limit: raise ValueError('%d frames: keep it to %d or fewer (raise step)' % (count, limit))
    out_dir = _scratch('snapshots')
    stamp = '%d' % (time.time() * 1000)            # unique per capture (ms), so back-to-back captures never collide

    # the artist's state, captured before touching anything
    frame_before = hou.frame()
    pwd_before = sv.pwd()
    home = _viewport_state(sv.curViewport())
    renderer_before = None
    unshow = _show(display) if display else None
    try:
        switched = bool(network) and _node(network) != pwd_before
        if switched: sv.setPwd(_node(network))
        vp = sv.curViewport()
        target = _viewport_state(vp) if switched else home
        is_lop = False
        try: is_lop = sv.pwd().childTypeCategory() == hou.lopNodeTypeCategory()
        except Exception: pass
        if renderer and is_lop and hasattr(sv, 'currentHydraRenderer'):   # Hydra renderers only exist in Solaris
            try:
                cur = sv.currentHydraRenderer()
                if cur and cur != renderer: sv.setHydraRenderer(renderer); renderer_before = cur
            except Exception: pass
        if camera:
            vp.setCamera(hou.node(camera) if camera.startswith('/obj/') else camera)
        if frame_all:
            if camera: vp.useDefaultCamera()
            _frame(vp, display)
        _set_background(vp, background)
        if shading:
            for ds, _ in target['shade']:
                try: vp.settings().displaySet(ds).setShadedMode(_shading_modes()[shading])
                except Exception: pass
        fb = sv.flipbookSettings().stash()              # a copy: the artist's flipbook settings are untouched
        fb.outputToMPlay(False)
        fb.frameRange((f0, f1)); fb.frameIncrement(step)
        fb.output(os.path.join(out_dir, stamp + '.$F4.png'))
        fb.useResolution(True); fb.resolution((int(width), int(height)))
        t = time.time(); sv.flipbook(vp, fb)
    finally:
        # put back in reverse: target network's viewport, renderer, network, then the artist's own viewport
        try:
            if network and sv.pwd() != pwd_before: _put_viewport_state(sv.curViewport(), target)
        except Exception: pass
        if renderer_before:
            try: sv.setHydraRenderer(renderer_before)
            except Exception: pass
        try:
            if sv.pwd() != pwd_before: sv.setPwd(pwd_before)
        except Exception: pass
        try: _put_viewport_state(sv.curViewport(), home)
        except Exception: pass
        if unshow: unshow()
        hou.setFrame(frame_before)
    shots = []
    f = f0
    while f <= f1 + 1e-6:
        p = os.path.join(out_dir, '%s.%04d.png' % (stamp, int(round(f))))
        if os.path.exists(p): shots.append({'frame': f, 'png': p})
        f += step
    if not shots: raise RuntimeError('flipbook produced no image (is the viewer showing anything?)')
    return {'images': shots, 'seconds': round(time.time() - t, 2), 'network': (network or sv.pwd().path())}

def cmd_parm_sweep(node, parm, values, width=800, height=450, network=None, camera=None, frame_all=True,
                   shading=None, renderer='Houdini GL', display=None, also_set=None, samples=24, background=None):
    """Step one parameter through `values`, capturing the viewport at each (for sweep videos / GIFs).
    Framing is held fixed: with frame_all, the view is framed once on the LAST value (usually the biggest).
    also_set={parm_path: value} sets other parameters for the duration (e.g. a mode toggle).
    renderer='karma' renders each value with quick_render through `camera` (slow, final look) instead of the viewport.
    Every parameter is restored exactly afterwards and the changes never enter the undo history."""
    n = _node(node); _writable(n.path())
    p = n.parm(parm)
    if p is None: raise ValueError('%s has no parm %s' % (node, parm))
    if not values or len(values) > 120: raise ValueError('give 1-120 values')
    sv = hou.ui.paneTabOfType(hou.paneTabType.SceneViewer)
    if sv is None: raise ValueError('no Scene Viewer pane is open')
    snap = _snapshot(p)
    extra = []
    for path, v in (also_set or {}).items():
        q = hou.parm(path)
        if q is None: raise ValueError('no parm %s' % path)
        _writable(q.node().path()); extra.append((q, _snapshot(q), v))
    pwd_before = sv.pwd(); home = _viewport_state(sv.curViewport()); target = None
    shots = []
    unshow = _show(display) if display else None
    try:
        with hou.undos.disabler():
            for q, _, v in extra: q.deleteAllKeyframes(); q.set(v)
            if network and _node(network) != pwd_before:
                sv.setPwd(_node(network)); target = _viewport_state(sv.curViewport())
            vp = sv.curViewport()
            if (renderer or '').lower() == 'karma':
                pass
            elif camera:
                vp.setCamera(hou.node(camera) if camera.startswith('/obj/') else camera)
            elif frame_all:
                p.deleteAllKeyframes(); p.set(values[-1])
                vp.useDefaultCamera(); _frame(vp, display)
            karma = (renderer or '').lower() == 'karma'
            if karma and not camera: raise ValueError("renderer='karma' needs a camera")
            for v in values:
                p.deleteAllKeyframes(); p.set(v)
                lab = '%s = %s' % (p.parmTemplate().label(), v)
                if karma:
                    r = cmd_quick_render(camera, width=width, height=height, samples=samples)
                    shots.append({'exr': r['exr'], 'frame': len(shots) + 1, 'label': lab})
                else:
                    r = cmd_viewport_snapshot(width=width, height=height, shading=shading, renderer=renderer, background=background)
                    shots.append(dict(r['images'][0], label=lab))
    finally:
        with hou.undos.disabler():
            _restore(p, snap)
            for q, sn, _ in extra: _restore(q, sn)
            try:
                if target is not None: _put_viewport_state(sv.curViewport(), target)
                if sv.pwd() != pwd_before: sv.setPwd(pwd_before)
                _put_viewport_state(sv.curViewport(), home)
            except Exception: pass
        if unshow: unshow()
    return {'images': shots}

def cmd_reload_listener():
    """Reload this module from disk and restart the listener, so code changes apply without the artist
    re-pasting start_listener.py. Off unless the project config sets "allow_reload": true."""
    if not CONFIG.get('allow_reload'):
        raise PermissionError('reload_listener is disabled (set "allow_reload": true in houdini_mcp_config.json)')
    mod = sys.modules[__name__]
    def later():                                   # runs after this reply has gone out
        import importlib
        mod.stop(); importlib.reload(mod)
        hou.session.mcp_listener = mod
        mod.start()
    hdefereval.executeDeferred(later)
    return {'reloading': os.path.abspath(__file__)}

# ----------------------------------------------------------------------------- save / undo / recover
MCP_TAG = 'MCP:'        # every agent write is an undo group labelled 'MCP: ...'

def _save():
    backup = _backup()
    hou.hipFile.save()
    return {'saved': hou.hipFile.path(), 'backup': backup}

def cmd_save_scene():
    """Save the working .hip (a backup of the previous state is made first)."""
    return _save()

def _step(kind, steps):
    labels_fn = hou.undos.undoLabels if kind == 'undo' else hou.undos.redoLabels
    perform = hou.undos.performUndo if kind == 'undo' else hou.undos.performRedo
    done = []
    for _ in range(max(1, min(int(steps), 50))):
        labels = labels_fn()
        if not labels: break
        if not labels[0].startswith(MCP_TAG):
            if not done:
                raise PermissionError('next %s step is not an MCP action (%r): refusing so the artist\'s own edits '
                                      'are never touched. Use Cmd+Z / Cmd+Shift+Z in Houdini for those.' % (kind, labels[0]))
            break
        perform(); done.append(labels[0])
    return {('undone' if kind == 'undo' else 'redone'): done}

def cmd_undo(steps=1):
    """Undo the last MCP action(s). Stops at (and never undoes) an edit the artist made by hand."""
    return _step('undo', steps)

def cmd_redo(steps=1):
    """Redo MCP action(s) that were undone."""
    return _step('redo', steps)

def _backup_dir():
    return os.environ.get('HOUDINI_BACKUP_DIR') or _hip('backup')

def _backups(limit=20):
    stem = os.path.splitext(hou.hipFile.basename())[0]
    d = _backup_dir()
    files = [f for f in os.listdir(d) if f.startswith(stem + '_bak')] if os.path.isdir(d) else []
    files.sort(key=lambda f: os.path.getmtime(os.path.join(d, f)), reverse=True)
    return [{'name': f, 'saved': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(os.path.getmtime(os.path.join(d, f))))}
            for f in files[:limit]]

def cmd_history(limit=20):
    """Undo / redo stack (MCP actions marked) and the most recent backups."""
    return {'undo': list(hou.undos.undoLabels())[:limit], 'redo': list(hou.undos.redoLabels())[:limit],
            'backups': _backups(limit), 'unsaved_changes': hou.hipFile.hasUnsavedChanges(),
            'auto_save': bool(CONFIG['auto_save'])}

def cmd_restore_backup(name, dry_run=False):
    """Re-open a backup from the backup folder in place of the working scene. The current state is backed up
    first, so a restore can itself be reverted. The working file on disk only changes on the next save
    (immediately, if auto_save is on)."""
    if os.path.basename(name) != name: raise ValueError('pass a backup file name from history(), not a path')
    src = os.path.join(_backup_dir(), name)
    if not os.path.exists(src): raise ValueError('no backup %s in %s' % (name, _backup_dir()))
    working = hou.hipFile.path()
    if dry_run:
        return {'dry_run': True, 'would_restore': src, 'as': working}
    before = _backup()
    hou.hipFile.load(src, suppress_save_prompt=True, ignore_load_warnings=True)
    hou.hipFile.setName(working)                       # keep saving to the working file, not over the backup
    load_config()
    try: hou.session.mcp_listener = sys.modules[__name__]   # hou.session is replaced on load; keep the stop() handle
    except Exception: pass
    out = {'restored': name, 'working_file': working, 'backup_of_previous_state': before}
    if CONFIG['auto_save']: out.update(_save())
    return out

def cmd_run_python(code):
    if not CONFIG['allow_python']:
        raise PermissionError('run_python is disabled (set "allow_python": true in houdini_mcp_config.json)')
    backup = _backup()
    env = {'hou': hou, 'result': None}
    with hou.undos.group('MCP: run_python'):
        exec(code, env)
    return {'result': _jsonable(env.get('result')), 'backup': backup}

COMMANDS = {k[4:]: v for k, v in globals().items() if k.startswith('cmd_')}


# ----------------------------------------------------------------------------- socket server (background thread)
class _Server(threading.Thread):
    daemon = True
    def __init__(self):
        super().__init__(name='houdini-mcp-listener')
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((HOST, PORT)); self.sock.listen(4); self.running = True
    def run(self):
        while self.running:
            try: conn, _ = self.sock.accept()
            except OSError: break
            threading.Thread(target=self._client, args=(conn,), daemon=True).start()
    def _client(self, conn):
        f = conn.makefile('rwb')
        for line in f:
            req = {}
            try:
                req = json.loads(line)
                cmd, args = req['cmd'], req.get('args') or {}
                fn = COMMANDS[cmd]
                autosave = cmd in WRITE_CMDS and not args.get('dry_run')
                def run():
                    r = fn(**args)
                    if autosave and CONFIG['auto_save'] and isinstance(r, dict) and r.get('undone', r.get('redone', True)) != []:
                        r = dict(r, auto_saved=_save()['saved'])
                    return _jsonable(r)
                res = hdefereval.executeInMainThreadWithResult(run)
                resp = {'id': req.get('id'), 'ok': True, 'result': res}
                _audit(cmd, args, True, 'auto-saved' if isinstance(res, dict) and 'auto_saved' in res else '')
            except Exception as e:
                resp = {'id': req.get('id'), 'ok': False, 'error': '%s: %s' % (type(e).__name__, e),
                        'trace': traceback.format_exc()[-1500:]}
                _audit(req.get('cmd', '?'), req.get('args') or {}, False, str(e))
            f.write((json.dumps(resp) + '\n').encode()); f.flush()
        conn.close()
    def stop(self):
        self.running = False
        try: self.sock.close()
        except OSError: pass

_server = None

def start():
    global _server
    if _server and _server.running:
        print('houdini MCP listener already running on %s:%d' % (HOST, PORT)); return
    cfg = load_config()
    swept = _sweep_scratch()
    _server = _Server(); _server.start()
    print('houdini MCP listener on %s:%d  config: %s' % (HOST, PORT, cfg or 'defaults (no $HIP/houdini_mcp_config.json)'))
    print('   writes limited to %s, run_python %s, auto_save %s' % (CONFIG['allowed_roots'],
          'ON' if CONFIG['allow_python'] else 'off', 'ON' if CONFIG['auto_save'] else 'off'))
    if swept: print('   cleared %d old capture(s) from %s' % (swept, _scratch()))

def stop():
    global _server
    if _server: _server.stop(); _server = None
    print('houdini MCP listener stopped')
