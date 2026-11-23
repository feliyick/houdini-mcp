# houdini-mcp

Let an AI agent work inside your **open Houdini session**, while you watch.

houdini-mcp is an [MCP](https://modelcontextprotocol.io) server. It gives an agent such as Claude Code a set of tools
for a live Houdini scene: read the network, change parameters, build and wire nodes, cook and check for errors,
and render a preview it can actually look at. It was built for working on a real production scene, not a
sandbox, so every edit is backed up, every edit is one undo step, and the agent can't undo anything you did by hand.

```
Claude Code ──MCP (stdio)──▶ server.py              normal Python 3.10+, outside Houdini
                                 │  JSON over a localhost socket (127.0.0.1:9876)
                                 ▼
                     houdini_listener.py            inside your open Houdini (Apprentice works)
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
  (`semantic_id`, `instance_id`, `parent_id`), rendered as Karma AOVs in a second, opaque pass. Edge pixels that Karma
  blends are repaired against the exact set of IDs on the geometry. Under an Apprentice licence the watermark corner
  is marked "ignore".
- `scene_metrics`: measures the scene for simulation: per object group, world-space boxes (walls, door, stairs,
  porch), ground height, step count, riser and tread size. `synth_render` also saves it per seed as `scene/seed_N.json`,
  so a physics scene (MuJoCo, Isaac) can rebuild collision without exporting meshes. It reads the SOP named by
  `synth_label_sop`.
- `stage_info`: read-only look at a LOP's USD stage (prims, types, attributes, primvar interpolation).

**Editing HDAs**
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
| Network | Listens on 127.0.0.1 only. |

---

## Try it

### You need
- Houdini 20.5 or newer, with the GUI open (Apprentice / Indie / FX all work). Tested on 20.5.370, macOS.
- Python **3.10+** for the server (separate from Houdini's Python). Check with `python3 --version`.
- An MCP client. These steps use [Claude Code](https://docs.claude.com/en/docs/claude-code); any MCP client works
  (see [Other MCP clients](#other-mcp-clients)).

### 1. Install (once)
```bash
git clone https://github.com/feliyick/houdini-mcp.git ~/houdini-mcp
cd ~/houdini-mcp
python3 -m venv .venv                        # or:  uv venv --python 3.12 .venv
.venv/bin/pip install -r requirements.txt    # or:  uv pip install --python .venv/bin/python -r requirements.txt
claude mcp add --scope user houdini -- "$PWD/.venv/bin/python" "$PWD/server.py"
```
`--scope user` makes the tools available in every folder. Without it they're only registered for the folder you
ran the command in.

On Windows, use `.venv\Scripts\python.exe` in place of `.venv/bin/python` (untested so far, reports welcome).

### 2. Start the listener in Houdini (every session)
Open your scene, then in **Windows > Python Shell** run:
```python
exec(open('/full/path/to/houdini-mcp/start_listener.py').read())
```
It finds the repo in `~/houdini-mcp` or `~/HoudiniProjects/houdini-mcp`; anywhere else, set the `HOUDINI_MCP_DIR`
environment variable. Tip: put that line on a shelf tool. You should see:
```
houdini MCP listener on 127.0.0.1:9876  config: defaults (no $HIP/houdini_mcp_config.json)
```
Running it again restarts the listener, which is how you pick up an update. To stop it:
`hou.session.mcp_listener.stop()`.

Save your scene first: backups and the audit log are written next to the `.hip` file.

Optional check from a terminal: `.venv/bin/python test_connection.py`.

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

### Other MCP clients
Any client that launches stdio MCP servers can use it. Point it at the venv's Python and `server.py`, for example in
Claude Desktop's `claude_desktop_config.json`:
```json
{ "mcpServers": { "houdini": { "command": "/full/path/houdini-mcp/.venv/bin/python",
                               "args": ["/full/path/houdini-mcp/server.py"] } } }
```

---

## Per-project config (optional)
Put a `houdini_mcp_config.json` next to your `.hip`. Every key is optional:

| Key | Default | Meaning |
|---|---|---|
| `allowed_roots` | `["/obj", "/stage", "/out"]` | the only places the agent may edit |
| `allow_python` | `false` | enable `run_python` |
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
- **GUI only.** The listener runs inside an open Houdini window (it needs the main-thread event loop); no hython/headless.
- **Tested on macOS** with Houdini 20.5.370 Apprentice. Windows/Linux should work but haven't been tried.
- **Not covered by dedicated tools yet:** deleting/renaming nodes, ramps and multiparms, keyframing, simulations and
  caches, exports, TOPs, COPs and KineFX. `run_python` (off by default) reaches all of it.
- `set_parms` refuses to touch animated parameters unless you pass `overwrite_animation=True`.
- Viewport text labels (`label_attrib`) are experimental and may not draw.

## Troubleshooting
| Symptom | Fix |
|---|---|
| "Houdini listener not reachable" | run `start_listener.py` in Houdini; check nothing else uses port 9876 |
| "Address already in use" when starting | an old listener is still running: run `start_listener.py` again (it stops the old one), or restart Houdini |
| Tools don't appear in Claude Code | restart `claude`; check `claude mcp list` shows `houdini ✔ Connected` |
| `quick_render` says no Karma LOP | add a Karma LOP in `/stage` or set `karma_lop` in the config |
| `quick_render` times out / no image | open `/out/houdini_mcp_rop` and render it by hand to see Karma's error |
| `undo` refuses | the next undo step was made by hand (or by code on cook): use Cmd/Ctrl+Z in Houdini |

## Files
| File | |
|---|---|
| `server.py` | the MCP server (tool definitions, EXR → PNG for previews) |
| `houdini_listener.py` | runs inside Houdini: the commands and guardrails |
| `start_listener.py` | paste/shelf snippet that (re)starts the listener |
| `test_connection.py` | terminal check that the listener answers |
| `CAMERA_GUIDE.md` | camera rules, served to the agent as `houdini://camera-guide` |
| `examples/` | project configs |

## Licence
MIT, see `LICENSE`.
