"""
install.py - set up houdini-mcp after `pip install` (or a git clone).

    houdini-mcp-install                 # tell every installed Houdini (20.5+) where the listener is
    houdini-mcp-install --autostart     # ... and start the listener when the Houdini GUI opens
    houdini-mcp-install --uninstall     # remove the Houdini package file again
    houdini-mcp-install --dry-run       # show what it would write
    houdini-mcp-headless --hip scene.hipnc [--houdini 20.5]   # hython matching the scene's saved version by default

It writes one Houdini package file per Houdini version, <prefs>/packages/houdini_mcp.json, that adds this package's
houdini_pkg folder to HOUDINI_PATH; its pythonrc.py makes `import houdini_mcp` work from this folder only (nothing else
is put on Houdini's sys.path). With --autostart it also sets HOUDINI_MCP_AUTOSTART=1 so the listener starts with the GUI.
Nothing else in your Houdini preferences is touched. Then it prints the command that registers the MCP server
with Claude Code.

houdini-mcp-headless (also here) finds hython and runs the listener on a scene with no GUI.
"""
import argparse, glob, json, os, platform, re, shutil, subprocess, sys

PKG_DIR = os.path.dirname(os.path.abspath(__file__))          # .../houdini_mcp
HOUDINI_PKG = os.path.join(PKG_DIR, 'houdini_pkg')             # HOUDINI_PATH entry (autostart hook)


# ----------------------------------------------------------------------------- finding Houdini
def houdini_installs():
    """[(version 'major.minor.build', bin_dir)] newest first, from $HFS and the standard install folders."""
    found = {}
    def add(bin_dir):
        hy = os.path.join(bin_dir, 'hython.exe' if os.name == 'nt' else 'hython')
        if not os.path.exists(hy): return
        m = re.search(r'(\d+\.\d+\.\d+)', bin_dir)
        found[os.path.realpath(hy)] = (m.group(1) if m else '0.0.0', bin_dir)
    if os.environ.get('HFS'): add(os.path.join(os.environ['HFS'], 'bin'))
    sysname = platform.system()
    if sysname == 'Darwin':
        for d in glob.glob('/Applications/Houdini/Houdini*/Frameworks/Houdini.framework/Versions/Current/Resources/bin'): add(d)
    elif sysname == 'Linux':
        for d in glob.glob('/opt/hfs*/bin'): add(d)
    else:
        for d in glob.glob(r'C:\Program Files\Side Effects Software\Houdini *\bin'): add(d)
    key = lambda v: tuple(int(x) for x in v[0].split('.'))
    return sorted(set(found.values()), key=key, reverse=True)

def find_hython(version=None):
    for v, b in houdini_installs():
        if version is None or v.startswith(version):
            return os.path.join(b, 'hython.exe' if os.name == 'nt' else 'hython'), v
    raise SystemExit('hython not found%s: install Houdini 20.5+, or set $HFS to its install folder'
                     % (' for Houdini %s' % version if version else ''))

def hip_version(hip):
    """The Houdini version that saved a .hip ('20.5.370'), from its header; None if unknown."""
    try:
        with open(hip, 'rb') as f: head = f.read(400000)
        m = re.search(rb"_HIP_SAVEVERSION = '(\d+\.\d+\.\d+)'", head)
        return m.group(1).decode() if m else None
    except OSError:
        return None

def find_hython_for(hip=None, version=None):
    """hython matching `version`, else the version that saved `hip` (exact build, then major.minor), else newest."""
    if version: return find_hython(version)
    v = hip_version(hip) if hip else None
    if v:
        for want in (v, '.'.join(v.split('.')[:2])):
            try: return find_hython(want)
            except SystemExit: pass
    return find_hython()

def prefs_dir(major_minor):
    sysname = platform.system()
    if os.environ.get('HOUDINI_USER_PREF_DIR'):
        return os.environ['HOUDINI_USER_PREF_DIR'].replace('__HVER__', major_minor)
    if sysname == 'Darwin': return os.path.expanduser('~/Library/Preferences/houdini/%s' % major_minor)
    if sysname == 'Linux': return os.path.expanduser('~/houdini%s' % major_minor)
    return os.path.join(os.path.expanduser('~'), 'Documents', 'houdini%s' % major_minor)


# ----------------------------------------------------------------------------- commands
def server_command():
    here = os.path.join(os.path.dirname(sys.executable), 'houdini-mcp' + ('.exe' if os.name == 'nt' else ''))
    exe = here if os.path.exists(here) else shutil.which('houdini-mcp')
    return [exe] if exe else [sys.executable, '-m', 'houdini_mcp.server']

def headless_command(hython):
    """hython running headless.py by path: it imports this package from its own folder, so no PYTHONPATH change."""
    return [hython, os.path.join(PKG_DIR, 'headless.py')]

def install_main(argv=None):
    ap = argparse.ArgumentParser(prog='houdini-mcp-install', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--autostart', action='store_true', help='start the listener whenever the Houdini GUI opens')
    ap.add_argument('--uninstall', action='store_true', help='remove the package file(s)')
    ap.add_argument('--houdini', help='only this Houdini version, e.g. 20.5')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args(argv)

    versions = sorted({'.'.join(v.split('.')[:2]) for v, _ in houdini_installs()}, reverse=True)
    versions = [v for v in versions if tuple(map(int, v.split('.'))) >= (20, 5)]
    if a.houdini: versions = [a.houdini]
    if not versions: raise SystemExit('No Houdini 20.5+ found. Pass --houdini 20.5 to write the package file anyway.')
    pkg = {'env': [{'HOUDINI_MCP_PKG': PKG_DIR}], 'path': HOUDINI_PKG}      # pythonrc.py imports from there only
    if a.autostart: pkg['env'].append({'HOUDINI_MCP_AUTOSTART': '1'})
    for v in versions:
        target = os.path.join(prefs_dir(v), 'packages', 'houdini_mcp.json')
        if a.uninstall:
            if os.path.exists(target) and not a.dry_run: os.remove(target)
            print('%s %s' % ('would remove' if a.dry_run else 'removed', target)); continue
        if a.dry_run:
            print('would write %s:\n%s' % (target, json.dumps(pkg, indent=2))); continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, 'w', encoding='utf-8') as f: json.dump(pkg, f, indent=2)
        print('wrote %s' % target)
    if a.uninstall: return
    cmd = ' '.join('"%s"' % c if ' ' in c else c for c in server_command())
    print('\nNext:')
    print('  1. Register the MCP server with Claude Code (once):\n       claude mcp add houdini -- %s' % cmd)
    if a.autostart:
        print('  2. Open Houdini: the listener starts by itself (Python Shell shows "houdini MCP listener on 127.0.0.1:9876").')
    else:
        print('  2. In Houdini\'s Python Shell (or a shelf tool), start the listener:\n'
              '       import houdini_mcp.houdini_listener as l; l.start()')
    print('  3. Optional: copy examples/houdini_mcp_config.json next to your .hip and edit it (allowed paths, Karma LOP, looks).')
    print('  Headless (no GUI):  houdini-mcp-headless --hip /path/scene.hipnc   (see README "Headless mode")')

def headless_main(argv=None):
    """houdini-mcp-headless: find hython and run houdini_mcp.headless with this package importable."""
    argv = list(sys.argv[1:] if argv is None else argv)
    version = None
    if '--houdini' in argv:
        i = argv.index('--houdini'); version = argv[i + 1]; del argv[i:i + 2]
    hip = argv[argv.index('--hip') + 1] if '--hip' in argv and argv.index('--hip') + 1 < len(argv) else None
    hy, v = find_hython_for(hip and os.path.expanduser(hip), version)
    if '--call' not in argv: print('hython %s: %s' % (v, hy), file=sys.stderr, flush=True)
    sys.exit(subprocess.call(headless_command(hy) + argv))


if __name__ == '__main__':
    install_main()
