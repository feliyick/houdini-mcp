# Camera guide (read before creating or moving any camera)

Use the `frame_camera` tool. It encodes most of this. Then verify with `quick_render`.

## Placement
- **Derive from the target's bounding box, never hard-code coordinates.** Scenes grow (sidewalks widen,
  rows get longer), and hard-coded cameras end up inside walls.
- **Re-check old cameras after scene changes.** A camera that was fine yesterday can be inside a new
  building today. A black or one-colour preview almost always means the camera is inside geometry.
- **Clip occluders with the near plane, mid-road.** To stand far enough back for a face-on shot you may need to
  stand inside the building opposite. `frame_camera` puts the near clip in the middle of the open gap nearest the subject
  that is at least road-width (4 m): the road. Not the widest gap, which can be empty space between backdrop rows
  and leaves the buildings across the street in view: never right in front of the subject, where a seed with deeper bays or a
  bigger stoop gets sliced, and never through a tree or lamp (that leaves dark blobs).
- **Long lenses at eye level see foreground clutter** (trees, parked cars). Raise the camera above it
  (an upstairs window: 4–8 m) rather than zooming more.

## Framing architecture
- **Level camera + vertical lens shift** keeps walls vertical (real architectural photography). Tilting
  makes buildings lean. `view="front"` does this. Use `view="angle"` only when convergence is wanted.
- **Match the frame shape to the subject:** a single tall house is portrait (720×960), a row face-on is a
  strip (1280×540), a street scene is 16:9. Squares and the wrong aspect crop cornices and stoops.
- **Set the camera's aspect ratio to the render resolution.** Houdini's camera derives its vertical
  aperture from aspect-ratio parms; left at 16:9 it silently squashes a portrait frame.
- **Check the edges.** Wide shots shouldn't reach past the "hero" content into low-detail filler. Extend
  the real content, or reframe.
- **For a series (gallery, dataset), keep distance constant and solve the lens per subject**, so
  perspective matches between shots.

## Previewing
- **The GL viewport and flipbooks ignore lens shift / aperture.** Don't judge framing there. Use `quick_render`.
- **The Karma viewport frames to the render resolution.** To preview a non-16:9 camera there, the render
  resolution must match.
- Hide other cameras' guides in viewport captures.

## Sweeps (a parameter changing under a fixed camera)
- **Set the near clip for the worst case, not the framing case.** When the layout changes during a sweep (row count,
  seed), things in front of the subject move too. A clip placed for 7 houses sliced the houses across the street
  at 1 house, leaving black slabs. Probe each extreme with `frame_camera(dry_run=True)` and use the clip that is
  clear for all of them, just in front of the subject's nearest part (stoops).
- Turn off filler that only exists to pad the frame (e.g. street extends) when it would move through the clip.
- Check the extremes with a 2-frame, low-res Karma sweep before committing to the full render.

## Composition
- Orbit first, compute second: the artist's favourite angles came from looking around. When the artist finds
  a view she likes, save it as a camera rather than recomputing.
- Drop shots that don't add anything over a wider one (tight close-ups rarely beat a good wide).
