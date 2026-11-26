# houdini-mcp

Let an AI agent work inside your **open Houdini session** while you watch, or **headless** in hython for batch
jobs, farms and CI.

houdini-mcp is an [MCP](https://modelcontextprotocol.io) server. It gives an agent such as Claude Code a set of tools
for a Houdini scene: read the network, change parameters, build and wire nodes, edit HDAs, cook and check for errors,
render a preview it can actually look at, export USD and write caches. It was built for working on a real production scene, not a
sandbox, so every edit is backed up, every edit is one undo step, and the agent can't undo anything you did by hand.

```
Claude Code ──MCP (stdio)──▶ server.py              normal Python 3.10+, outside Houdini
                                 │  JSON over a localhost socket (127.0.0.1:9876)
                                 ▼
                     houdini_listener.py            inside your open Houdini (Apprentice works),
                                 │                  or in hython with no GUI (headless)
                                 │  hou.* calls, run on Houdini's main thread
                                 ▼
                             your scene             changes appear live in the viewport
```

It was built for, and used on, a procedural San Francisco Victorian street:
[sf-victorian-procedural](https://github.com/feliyick/sf-victorian-procedural) · [write-up](https://felicityyick.com/project/sf-victorian-street). Nothing in the tool depends on that
project; its settings live in one config file (`examples/sf_victorian_house.json`).

---

## What it can do

**Look at a scene**
- `scene_info`: file, Houdini version, unsaved state, top-level nodes, the Solaris cameras, and any parameters you
  ask it to keep an eye on.
- `list_nodes`, `get_parms`: browse the network (with each node's inputs, so the agent can follow the wiring); read values *and* expressions.
- `geometry_stats`: point/prim counts, bounding box, groups, attributes, detail values, counts by name/material.
- `dump_attribute`: the actual values of one attribute, so the agent checks real geometry instead of guessing.
- `cook_and_check`: force-cook and report every node with an error, plus anything slow to cook.
- `viewport_snapshot`: grab the Scene Viewer (fast OpenGL flipbook) in `/obj` or `/stage`, optionally through a
  camera, framed on everything, or in wireframe/hidden-line. Give it `frames=[1, 48]` and it flipbooks the range and
  returns a labelled contact sheet, so the agent can check playback. Your viewport is put back exactly as it was.
- `quick_render`: a small Karma render through any camera, returned to the agent as an image.

**Make videos and GIFs** (`.mp4` or `.gif`, saved where you say)
- `viewport_video`: flipbook a frame range from the viewport (playback, turntables).
- `sweep_video`: a parameter changing over time, e.g. row count 1 → 7 or a seed sweep, labelled
  "Row Count = 3" on each frame. Framing is held fixed; `display` shows a chosen node, `also_set` flips other
  parameters for the duration (a mode toggle, a lighting look), and everything is restored afterwards.
  `renderer='karma'` renders each step in Karma through a camera for final-quality sweeps.

**Synthetic training data**
- `synth_render`: for each seed and camera, renders the beauty image plus per-pixel **class** and **instance** labels
  and writes a COCO dataset (`images/`, `labels/` and `annotations.json` with boxes, run-length masks and parent links,
  e.g. a bay window inside its bay). Labels come from integer prim attributes on your geometry
  (`semantic_id`, `instance_id`, `parent_id`), rendered as Karma AOVs in a second, opaque pass at one sample per pixel
  with a 1-pixel box filter, so no pixel averages two objects. Every pixel is still checked against the exact set of
  IDs on the geometry (a safety net; it should find none). Under an Apprentice licence the watermark corner
  is marked "ignore".
- `scene_metrics`: measures the scene for simulation: per object group, world-space boxes (walls, door, stairs,
  porch), ground height, step count, riser and tread size, and the doorbell (centre, facing normal, size and the
  point a robot presses) when the asset stamps `bell_*` attributes on class 16 faces. `synth_render` also saves it per seed as `scene/seed_N.json`,
  so a physics scene (MuJoCo, Isaac) can rebuild collision without exporting meshes. It reads the SOP named by
  `synth_label_sop`.
- `stage_info`: read-only look at a LOP's USD stage (prims, types, attributes, primvar interpolation).

**Export and caches** (files only go under the config's `output_dirs`, `$HIP` by default; nothing is overwritten
unless you say so)
- `usd_export`: a LOP's composed stage, flattened into one self-contained `.usd/.usda/.usdc` that opens anywhere
  (usdview, Omniverse, Isaac Sim, Blender), or a SOP's geometry. A frame range writes time samples, only for values
  that actually change. Written with the USD API, so a non-commercial licence still produces standard files.
- `cache_write`: a SOP's cooked geometry to `.bgeo.sc`, `.obj`, `.ply`, `.usd`, `.abc`..., one file per frame over a
  range (`$F4` or `####` in the path). The scene is left as it was.
- `cache_read`: what is in a geometry file on disk (counts, bounds, attributes, groups) without loading it into the scene.

**Editing HDAs**
- `add_parm`: add a parameter (int, float, menu, toggle, string) to an asset's interface inside a named folder, or a
  spare parameter on a node, optionally driven by an expression. An existing parameter is never touched, so it is
  safe to re-run.
- `hda_edit` / `hda_save`: unlock an asset instance (the library file is backed up first), edit inside it with the
  normal tools, then save the definition so every instance updates. `revert_hda` throws edits away.

**Cameras** (read the `houdini://camera-guide` resource first)
- `frame_camera`: create or update a camera that frames a target by its bounding box. Front view keeps the camera
  level and shifts the lens so walls stay vertical; angle view gives a three-quarter shot. Sets the aspect ratio to
  the frame size, ray-tests the line of sight and puts the near clip in open space past anything in the way.
- `look_through`: point your viewport at a camera.

**Change a scene** (every edit has a `dry_run` mode that reports what *would* change)
- `set_parms`: values or expressions (`"=ch('../seed') * 2"`), including your VEX snippets.
- `create_node`, `connect`: build and wire networks.
- `set_look`: flip a lighting-setup switch by name ("golden", "night"), including any parameters that go with it.

**Stay safe and recover**
- `history`: the undo stack (agent edits are labelled `MCP: ...`), recent backups, unsaved state.
- `undo`, `redo`: step through the agent's own edits; refuses to touch anything you did by hand.
- `save_backup`, `save_scene`, `restore_backup`: numbered backups, saving, re-opening an older version.
- `reload_listener`: reload the listener's code after you edit it. **Off unless the config allows it.**
- `run_python`: arbitrary `hou` code for anything the tools don't cover. **Off unless you turn it on.**

**Read your project's rules**
- resource `houdini://project-guide`: serves the `CLAUDE.md` from the folder you started the agent in, so you can
  write down your conventions (naming, what not to touch, how you like changes made) and the agent reads them first.

## Guardrails

| | |
|---|---|
| Scope | Writes are refused outside `allowed_roots` (default `/obj`, `/stage`, `/out`). |
| Backups | `hou.hipFile.saveAsBackup()` before every edit; your working file is untouched unless you turn on `auto_save`. |
| Undo | Each agent edit is one undo group: Cmd/Ctrl+Z in Houdini reverts exactly one agent action. |
| Your edits | The `undo` tool stops at the first step that isn't an agent edit. |
| Previews | `quick_render` changes render settings temporarily, outside the undo history, and puts them back exactly (expressions and `$HIP` paths included). |
| Python | `run_python` is disabled unless the project config says `"allow_python": true`. |
| Audit | Every call is appended to `$HIP/mcp/audit.log`. |
| No clutter | Viewport captures and preview renders go to the OS temp folder and are deleted as soon as the agent has them, like a browser-automation screenshot. Pass `save_to` to keep one. Leftovers older than a day are swept when the listener starts. |
| Files | `usd_export` / `cache_write` only write under `output_dirs` (`$HIP` by default) and never replace a file unless `overwrite=true`. |
| Headless | A hython session never saves over the scene it loaded unless started with `--allow-save` (`save_scene(path=...)` saves a copy). |
| Network | Listens on 127.0.0.1 only. |

---

## Try it

### You need
- Houdini 20.5 or newer (Apprentice / Indie / FX all work). Tested on macOS: 20.5.370 (GUI and headless) and
  21.0.596 (headless). Several Houdini versions can be installed side by side; each gets its own package file.
- Python **3.10+** for the server (separate from Houdini's Python). Check with `python3 --version`.
- An MCP client. These steps use [Claude Code](https://docs.claude.com/en/docs/claude-code); any MCP client works
  (see [Other MCP clients](#other-mcp-clients)).

### 1. Install (once)
```bash
pip install git+https://github.com/feliyick/houdini-mcp.git      # or: uv tool install git+https://github.com/feliyick/houdini-mcp.git
houdini-mcp-install                                             # add --autostart to start the listener with Houdini
claude mcp add --scope user houdini -- houdini-mcp              # houdini-mcp-install prints the exact command
```
- `houdini-mcp` is the MCP server; `houdini-mcp-install` tells every Houdini 20.5+ where the listener is;
  `houdini-mcp-headless` runs it with no GUI.
- `houdini-mcp-install` writes one small Houdini package file per version (`<prefs>/packages/houdini_mcp.json`) and
  touches nothing else. It makes `import houdini_mcp` work inside Houdini **without** putting your Python's
  site-packages on Houdini's path (so your numpy can't shadow Houdini's). `--uninstall` removes it; `--dry-run` shows it.
- `--scope user` makes the tools available in every folder.

From a clone (to hack on it):
```bash
git clone https://github.com/feliyick/houdini-mcp.git && cd houdini-mcp
python3 -m venv .venv && .venv/bin/pip install -e .              # or: uv venv && uv pip install -e .
.venv/bin/houdini-mcp-install
claude mcp add --scope user houdini -- "$PWD/.venv/bin/houdini-mcp"
```
On Windows, use `.venv\Scripts\` in place of `.venv/bin/` (untested so far, reports welcome).

### 2. Start the listener in Houdini (every session, unless you installed with `--autostart`)
Open your scene, then in **Windows > Python Shell** run:
```python
import houdini_mcp.houdini_listener as l; l.start()
```
(Tip: put it on a shelf tool. From a clone you can also `exec(open('/path/to/houdini-mcp/start_listener.py').read())`,
which restarts the listener with fresh code.) You should see:
```
houdini MCP listener on 127.0.0.1:9876  config: defaults (no $HIP/houdini_mcp_config.json)
```
To stop it: `hou.session.mcp_listener.stop()`. Opening another scene picks up that scene's config automatically.

Save your scene first: backups and the audit log are written next to the `.hip` file.

Optional check from a terminal: `python test_connection.py` (in a clone).

### 3. Ask for something
Start `claude` in your project folder (restart it if it was already running, so it picks up the new tools) and try:

- *"Use the houdini tools. Give me scene_info and list what's in /obj."*
- *"Make a geo node in /obj with a box, a polybevel and a mountain SOP, then show me the geometry stats."*
- *"Set the mountain height to 2 with a dry run first, then do it for real, then cook_and_check."*
- *"Undo that last change."*
- *"Take a viewport snapshot of /obj in wireframe, framed on everything."*
- *"Flipbook frames 1 to 24 every 4 frames and tell me if anything pops."*
- *"quick_render through /cameras/camera1 at 640x360."*
- *"Make a GIF of the seed going from 1 to 12 on my asset, framed on its output node."*
- *"frame_camera a front view of /obj/geo1/OUT as a 720x960 portrait, then quick_render it."* (needs a Solaris stage with a camera and a Karma LOP)
- *"What's the history? Restore the backup from before the bevel."*
- *"usd_export /stage/my_last_lop to $HIP/export/scene.usd, then tell me how many prims it has."*
- *"cache_write /obj/geo1/OUT for frames 1-48 to $HIP/cache/out.$F4.bgeo.sc and cache_read the first frame."*

### Headless mode (no GUI)
The same tools run in **hython**, for batch jobs, render farms, CI, or a second session beside your open Houdini.
```bash
houdini-mcp-headless --hip /path/scene.hipnc                  # serve on 127.0.0.1:9877 until shutdown_headless
houdini-mcp-headless --hip scene.hipnc --call usd_export --args '{"node": "/stage/OUT", "path": "$HIP/out/scene.usd"}'
houdini-mcp-headless --hip scene.hipnc --call cache_write --args '{"node": "/obj/geo1/OUT", "path": "$HIP/cache/out.$F4.bgeo.sc", "frame_range": [1, 240]}'
```
- `--call` runs one command, prints its JSON result and exits (non-zero on error): drop it into a farm job or CI step.
- It uses the hython that matches the Houdini version that saved the scene (`--houdini 21.0` to pick another).
- Or let the MCP server start it on demand: register a second server that points at a scene,
  ```bash
  claude mcp add houdini-batch -e HOUDINI_MCP_HIP=/path/scene.hipnc -e HOUDINI_MCP_PORT=9877 -- houdini-mcp
  ```
  and the first tool call launches hython on that scene (log: `mcp_headless.log` beside it).
- What works: everything that doesn't need a window, including `quick_render` and `synth_render` (Karma),
  `usd_export`, `cache_write`, `hda_edit`, `scene_metrics`. Viewport tools (`viewport_snapshot`, `viewport_video`,
  `look_through`) refuse with a clear message.
- Safety: the loaded `.hip` is never saved over unless you pass `--allow-save`; `auto_save` is forced off; the project
  config (allowed roots, output folders, `run_python` off) still applies.

### Other MCP clients
Any client that launches stdio MCP servers can use it. Point it at the `houdini-mcp` command (full path from
`which houdini-mcp`), for example in Claude Desktop's `claude_desktop_config.json` or Cursor's MCP settings:
```json
{ "mcpServers": { "houdini": { "command": "/full/path/to/houdini-mcp" } } }
```
Add `"env": {"HOUDINI_MCP_HIP": "/path/scene.hipnc", "HOUDINI_MCP_PORT": "9877"}` for a headless server.

---

## Per-project config (optional)
Put a `houdini_mcp_config.json` next to your `.hip`. Every key is optional:

| Key | Default | Meaning |
|---|---|---|
| `allowed_roots` | `["/obj", "/stage", "/out"]` | the only places the agent may edit |
| `allow_python` | `false` | enable `run_python` |
| `output_dirs` | `["$HIP"]` | the only folders `usd_export` / `cache_write` may write to |
| `camera_insert_before` | the Karma LOP | `frame_camera` wires new camera LOPs in above this node |
| `occluder_sops` | `[]` | SOPs `frame_camera` ray-tests for things between camera and target |
| `front_axis` | `[0, 0, 1]` | the direction facades face (front-view cameras stand out along it) |
| `synth_label_overrides` | `{}` | parms set for the label pass, e.g. a switch to opaque materials |
| `synth_label_sop` | none | SOP carrying the labels, used to repair blended edge pixels and by `scene_metrics` |
| `synth_slope_parm` | none | optional parm whose value `scene_metrics` reports as the ground slope |
| `allow_reload` | `false` | enable `reload_listener` (pick up edited listener code without re-running `start_listener.py`) |
| `auto_save` | `false` | save the `.hip` after every successful agent edit (backup first). Note this also saves any unsaved hand edits. |
| `check_nodes` | displayed `/obj` nodes | what `cook_and_check` cooks by default |
| `watch_parms` | `{}` | parameters shown in every `scene_info`, e.g. `{"/obj/geo1/asset": ["seed", "style"]}` |
| `karma_lop` | first Karma LOP in `/stage` | the node `quick_render` renders through |
| `look_switch` | none | a Switch LOP that selects your lighting setup |
| `looks` | `{}` | names for its inputs, e.g. `{"day": 0, "night": 1}` |
| `look_parms` | `{}` | extra values to set per look, e.g. `{"night": {"/obj/ctl/stars": 1}}` |

See `examples/sf_victorian_house.json` for a full one.

## Good to know
- Houdini is busy while a tool runs: a quick render blocks the UI for its duration.
- A cook can't be interrupted from outside. The client times out and reports, and the cook finishes in Houdini.
- A forced cook (`cook_and_check`) marks the file as unsaved even when nothing changed. That's Houdini, not an edit.
- If code in your scene runs on cook and edits nodes (a Python SOP/LOP setting a comment, say), Houdini records those
  as undo steps too, and `undo` will stop at them. Wrap such edits in `hou.undos.disabler()`.
- There's no authentication on the socket: anything running on your machine can talk to it while it's on. Keep the
  listener off when you're not using it.

## Limits (so far)
- **Tested on macOS** with Houdini 20.5.370 (GUI and headless) and 21.0.596 (headless), Apprentice. The 21.0 GUI,
  Windows and Linux should work but haven't been tried. Older than 20.5 is not supported.
- **Headless serves one client at a time** and has no viewport.
- **Not covered by dedicated tools yet:** deleting/renaming nodes, ramps and multiparms, keyframing, running
  simulations, TOPs/PDG, COPs and KineFX. `run_python` (off by default) reaches all of it.
- `set_parms` refuses to touch animated parameters unless you pass `overwrite_animation=True`.
- Viewport text labels (`label_attrib`) are experimental and may not draw.

## Troubleshooting
| Symptom | Fix |
|---|---|
| "Houdini listener not reachable" | start the listener in Houdini (`import houdini_mcp.houdini_listener as l; l.start()`); check nothing else uses port 9876 (`HOUDINI_MCP_PORT` changes it) |
| `import houdini_mcp` fails inside Houdini | run `houdini-mcp-install` (then restart Houdini), or use `start_listener.py` from a clone |
| Headless won't start | read `mcp_headless.log` next to the scene; `houdini-mcp-headless --hip scene.hipnc --call ping` shows the error directly |
| "Address already in use" when starting | an old listener is still running: run `start_listener.py` again (it stops the old one), or restart Houdini |
| Tools don't appear in Claude Code | restart `claude`; check `claude mcp list` shows `houdini ✔ Connected` |
| `quick_render` says no Karma LOP | add a Karma LOP in `/stage` or set `karma_lop` in the config |
| `quick_render` times out / no image | open `/out/houdini_mcp_rop` and render it by hand to see Karma's error |
| `undo` refuses | the next undo step was made by hand (or by code on cook): use Cmd/Ctrl+Z in Houdini |

## Files
| File | |
|---|---|
| `houdini_mcp/server.py` | the MCP server (tool definitions, EXR → PNG for previews, starts headless on demand) |
| `houdini_mcp/houdini_listener.py` | runs inside Houdini (GUI or hython): the commands and guardrails |
| `houdini_mcp/headless.py` | hython entry point: serve, or `--call` one command |
| `houdini_mcp/install.py` | `houdini-mcp-install` / `houdini-mcp-headless`: Houdini package file, finding hython |
| `houdini_mcp/houdini_pkg/` | added to HOUDINI_PATH by the package file: makes `import houdini_mcp` work, optional autostart |
| `houdini_mcp/CAMERA_GUIDE.md` | camera rules, served to the agent as `houdini://camera-guide` |
| `server.py`, `houdini_listener.py` | small shims so older registrations and scripts keep working |
| `start_listener.py` | paste/shelf snippet that (re)starts the listener from a clone |
| `test_connection.py` | terminal check that the listener answers |
| `examples/` | project configs |

## Licence
MIT, see `LICENSE`.
