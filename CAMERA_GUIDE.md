# Camera guide (read before creating or moving any camera)

Use the `frame_camera` tool. It encodes most of this. Then verify with `quick_render`.

## Placement
- **Derive from the target's bounding box, never hard-code coordinates.** Scenes grow (sidewalks widen,
  rows get longer), and hard-coded cameras end up inside walls.
- **Re-check old cameras after scene changes.** A camera that was fine yesterday can be inside a new
  building today. A black or one-colour preview almost always means the camera is inside geometry.
- **Clip occluders with the near plane, in open space.** To stand far enough back for a face-on shot you
  may need to stand inside the building opposite. Put the near clip just past the occluder, mid-road,
  never slicing through a tree or lamp (that leaves dark blobs). `frame_camera` ray-tests and does this.
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

## Composition
- Orbit first, compute second: the artist's favourite angles came from looking around. When Felicity finds
  a view she likes, save it as a camera rather than recomputing.
- Drop shots that don't add anything over a wider one (tight close-ups rarely beat a good wide).
