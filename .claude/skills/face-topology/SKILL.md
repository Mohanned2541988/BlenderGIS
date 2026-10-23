---
name: face-topology
description: Turn any face photo into a 3D-artist "face topology" sheet - curved quad wireframe, concentric edge loops around the mouth, eyes and nostrils, and pastel colour-coded anatomical zones (lips, orbicularis oris, nasolabial, orbit, alar wing, cheek, nose bridge, chin) over a grey clay version of the photo. Use when the user uploads a portrait/selfie and asks for a topology overlay, wireframe face, edge-loop map, retopology guide, "make it look like the topology reference", or runs /face-topology.
---

# Face topology overlay

Reproduces the look of classic facial-topology reference sheets (clay render +
quad mesh + colour-coded edge-loop groups) on a real photo.

## How it works

1. MediaPipe Face Landmarker finds 478 landmarks for every face in the photo.
2. The bundled canonical face mesh (`assets/canonical_face_uv.json`, 468
   vertices + UV layout) is fitted to those landmarks.
3. Edge loops, spokes, the background quad grid and the colour zones are
   designed once in that UV space, then projected onto the face, so they
   bend with the face's real 3D shape. A z-buffer + back-face culling hides
   lines that would be behind the nose / far cheek on turned heads.

## Run it

Dependencies (install once if missing):

```bash
pip install mediapipe opencv-python-headless numpy
# Linux containers only, if import fails with "libEGL.so.1":
apt-get install -y libegl1 libgles2
```

The landmark model (~4 MB) is downloaded on first run to
`~/.cache/face-topology/face_landmarker.task`.

```bash
python .claude/skills/face-topology/scripts/face_topology.py PHOTO --crop -o OUT.png
```

Options:

| flag | effect |
|---|---|
| `--crop` | frame the face(s) as a close-up and upscale small faces (recommended for most photos) |
| `--style clay` / `photo` | grey sculpt look (default) or keep the photo's colours |
| `--density 1.0` | edge density; `0.7` = chunky low-poly, `1.4` = dense |
| `--fill-alpha 0.55` | strength of the coloured zones |
| `--line-alpha 0.7` | wire opacity |
| `--no-fill` / `--no-grid` | wireframe only / edge loops only |
| `--max-faces 5` | how many faces to process |
| `--obj OUT.obj` | also export a 3D head + topology mesh for Blender (see below) |

Output defaults to `<input>_topology.png` next to the input.

## 3D export for Blender (`--obj`)

```bash
python .claude/skills/face-topology/scripts/face_topology.py PHOTO --crop --obj head.obj
```

writes `head.obj`, `head.mtl` and `head_photo.png` (keep them together).
Per detected face the OBJ holds two objects, sized to a real head (~22 cm):

- `Face<N>_Surface` - the 468-vertex face shape fitted to the photo,
  textured with the photo. Use it as the snapping target.
- `Face<N>_Topology` - the edge loops and quad grid as real quads (welded
  where the grid meets the loops), floating 1.2 mm above the surface, with
  one material per colour zone (`topo_lips`, `topo_orbit`, ...).

In Blender: **File > Import > Wavefront (.obj)** with default axes, then
Numpad 1 for the front view. Switch the viewport to Material Preview to see
the photo and zone colours. Depth comes from a single photo, so treat the
shape as a starting point, not a scan.

## Workflow for an uploaded photo

1. Find the uploaded image path (uploads are listed in the turn).
2. Run the script with `--crop`, writing to the scratchpad (or wherever the
   user wants it).
3. Look at the result. If the face is tiny or the mesh looks crowded, re-run
   with `--density 0.8`; if the user wants their skin tones kept, use
   `--style photo`.
4. Send the PNG to the user. If they want it in Blender / 3D, add
   `--obj` and send the `.obj`, `.mtl` and `_photo.png` together.

If the script reports "No face detected", the photo has no real, reasonably
visible face (fully side-on profiles, heavy occlusion, stylised drawings or
3D renders often fail) - tell the user and ask for a more frontal photo.

## Customising the look

All styling lives at the top of `scripts/face_topology.py`:

- `C` - the zone palette (RGB).
- `build_zones()` - each `LoopZone` is an inner contour (landmark ring),
  an outer super-ellipse boundary in UV space, the ring positions `ts`,
  the spoke count, and a `band_color(ring_index, point)` function.
- `static_patches()` - free-form zones (cheek, bridge, chin) that are snapped
  to the grid quads.

UV space: `u` 0..1 left to right (subject's right side is `u < 0.5`), `v`
0..1 chin to forehead. Mouth centre is about `(0.5, 0.31)`, eyes about
`(0.35, 0.615)` / `(0.65, 0.615)`, nose tip `(0.5, 0.45)`.
