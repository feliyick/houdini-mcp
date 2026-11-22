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
import json, os, socket, sys, threading, time, traceback

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
    "auto_save": False                              # save the .hip after every successful write (backup first)
}
# commands that change the scene (auto_save runs after these when they succeed and weren't a dry run)
WRITE_CMDS = {'set_parms', 'create_node', 'connect', 'set_look', 'run_python', 'undo', 'redo'}
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
        out.append({'path': n.path(), 'type': n.type().name(), 'errors': bool(n.errors()), 'warnings': bool(n.warnings())})
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

def cmd_quick_render(camera, width=640, height=360, samples=24, look=None):
    """Karma render (blocking), so the agent can SEE the result. Returns the EXR path.
    Everything it changes (Karma settings, and the look if one is given) is put back exactly afterwards."""
    karma = _karma_lop()
    with hou.undos.disabler():                  # the preview ROP is tooling, not part of the artist's undo history
        rop = hou.node('/out/houdini_mcp_rop') or hou.node('/out').createNode('usdrender', 'houdini_mcp_rop')
        rop.parm('loppath').set(karma.path())
        if rop.parm('renderer'): rop.parm('renderer').set('BRAY_HdKarma')
        for p in rop.parms():
            lab = (p.parmTemplate().label() + ' ' + p.name()).lower()
            if p.parmTemplate().type() != hou.parmTemplateType.Toggle: continue
            if 'mplay' in lab or 'background' in lab or 'async' in lab: p.set(0)
            elif 'wait' in lab or 'block' in lab: p.set(1)
    out = _hip('mcp', 'renders', '%s_%s.exr' % (time.strftime('%H%M%S'), camera.split('/')[-1]))
    os.makedirs(os.path.dirname(out), exist_ok=True)
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

def cmd_save_backup():
    return {'backup': _backup()}

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
    _server = _Server(); _server.start()
    print('houdini MCP listener on %s:%d  config: %s' % (HOST, PORT, cfg or 'defaults (no $HIP/houdini_mcp_config.json)'))
    print('   writes limited to %s, run_python %s, auto_save %s' % (CONFIG['allowed_roots'],
          'ON' if CONFIG['allow_python'] else 'off', 'ON' if CONFIG['auto_save'] else 'off'))

def stop():
    global _server
    if _server: _server.stop(); _server = None
    print('houdini MCP listener stopped')
