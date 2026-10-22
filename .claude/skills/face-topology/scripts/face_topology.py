#!/usr/bin/env python3
"""Render a 3D-artist style "face topology" overlay on a photo.

Detects every face in the photo with MediaPipe Face Landmarker, fits the
canonical face mesh to it, then draws:

  * concentric edge loops around the mouth, eyes and nostril wings,
  * radial spokes so the loops read as quads,
  * a curved quad grid over the rest of the face,
  * pastel colour-coded anatomical zones (lips, orbicularis oris,
    nasolabial area, orbit, alar wing, cheek, nose bridge, chin),

on top of a grey "clay" version of the photo (or the original colours).
Occlusion is handled with a z-buffer so 3/4 views look right.

Usage:
    python face_topology.py INPUT [-o OUTPUT] [--style clay|photo]
                            [--density 1.0] [--crop] [--no-fill] [--no-grid]
"""
import argparse
import json
import os
import sys
import urllib.request

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
MESH_JSON = os.path.join(HERE, "..", "assets", "canonical_face_uv.json")
MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/face_landmarker/"
             "face_landmarker/float16/1/face_landmarker.task")
MODEL_PATH = os.path.join(os.path.expanduser("~"), ".cache", "face-topology",
                          "face_landmarker.task")

UV_RES = 2048

# Landmark rings (MediaPipe 468 indices)
LIPS_OUTER = [61, 146, 91, 181, 84, 17, 314, 405, 321, 375, 291, 409, 270,
              269, 267, 0, 37, 39, 40, 185]
LIPS_INNER = [78, 95, 88, 178, 87, 14, 317, 402, 318, 324, 308, 415, 310,
              311, 312, 13, 82, 81, 80, 191]
EYE_R = [33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160,
         161, 246]
EYE_L = [263, 249, 390, 373, 374, 380, 381, 382, 362, 398, 384, 385, 386,
         387, 388, 466]

# Pastel palette (RGB), close to the classic topology-sheet look
C = {
    "lips":     (196, 104, 120),
    "oris":     (110, 190, 196),
    "oris2":    (128, 136, 196),
    "naso":     (160, 206, 150),
    "philtrum": (222, 206, 180),
    "chin":     (200, 150, 200),
    "lid":      (176, 168, 214),
    "orbit":    (170, 120, 220),
    "ala":      (238, 182, 120),
    "alaring":  (176, 132, 222),
    "cheek":    (150, 196, 140),
    "bridge":   (236, 226, 120),
    "mentum":   (226, 206, 140),
}


# ---------------------------------------------------------------- geometry
def resample_closed(poly, n):
    """Resample closed polyline to n points by arc length, starting at the
    left-most point and running counter-clockwise in UV (v up)."""
    poly = np.asarray(poly, np.float64)
    x, y = poly[:, 0], poly[:, 1]
    if 0.5 * np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y) < 0:
        poly = poly[::-1]
    poly = np.roll(poly, -int(np.argmin(poly[:, 0])), axis=0)
    closed = np.vstack([poly, poly[:1]])
    seg = np.linalg.norm(np.diff(closed, axis=0), axis=1)
    s = np.concatenate([[0], np.cumsum(seg)])
    t = np.linspace(0, s[-1], n, endpoint=False)
    return np.stack([np.interp(t, s, closed[:, 0]),
                     np.interp(t, s, closed[:, 1])], 1)


def superellipse(c, rx, ry_top, ry_bot, p=2.4, n=400):
    a = np.linspace(0, 2 * np.pi, n, endpoint=False)
    ca, sa = np.cos(a), np.sin(a)
    x = c[0] + rx * np.sign(ca) * np.abs(ca) ** (2 / p)
    ry = np.where(sa >= 0, ry_top, ry_bot)
    y = c[1] + ry * np.sign(sa) * np.abs(sa) ** (2 / p)
    return np.stack([x, y], 1)


class LoopZone:
    """Concentric edge loops from an inner contour out to a boundary."""

    def __init__(self, inner, outer, ts, spokes, band_color, inner_color=None,
                 accent=True):
        self.inner = resample_closed(inner, spokes)
        self.outer = resample_closed(outer, spokes)
        self.ts = ts
        self.band_color = band_color      # f(band_idx, point) -> colour|None
        self.inner_color = inner_color
        self.accent = accent              # draw the inner loop in red
        self.rings = [(1 - t) * self.inner + t * self.outer for t in ts]

    def ring(self, t, n=None):
        r = (1 - t) * self.inner + t * self.outer
        return r if n is None else resample_closed(r, n)


def to_px(uv):
    uv = np.asarray(uv, np.float64)
    return np.stack([uv[:, 0] * (UV_RES - 1), (1 - uv[:, 1]) * (UV_RES - 1)], 1)


# ---------------------------------------------------------------- zones
def build_zones(uv, density):
    sp = lambda n: max(8, int(round(n * density)))
    zones = []

    lips = uv[LIPS_OUTER]
    mc = lips.mean(0)

    def mouth_color(i, p):
        upper = p[1] > mc[1]
        if i == 0:
            return C["oris"]
        if upper:
            if abs(p[0] - 0.5) < 0.028:
                return C["philtrum"]
            return C["naso"] if i >= 2 else C["oris2"]
        return C["oris2"] if i == 1 else (C["chin"] if i <= 3 else None)

    zones.append(LoopZone(
        lips, superellipse(mc, 0.19, 0.088, 0.125, 2.3),
        [0, .16, .34, .54, .76, 1.0], sp(26), mouth_color, C["lips"]))

    for ring, side in ((EYE_R, -1), (EYE_L, 1)):
        e = uv[ring]
        ec = e.mean(0)

        def eye_color(i, p, _c=ec):
            if i <= 1:
                return C["lid"]
            if i == 2:
                return C["orbit"]
            return None

        zones.append(LoopZone(
            e, superellipse(ec + [0, 0.008], 0.135, 0.125, 0.098, 2.2),
            [0, .2, .4, .6, .8, 1.0], sp(22), eye_color))

    for side in (-1, 1):
        ac = np.array([0.5 + side * 0.083, 0.441])
        inner = superellipse(ac, 0.02, 0.016, 0.014, 2.0, 120)

        def ala_color(i, p):
            return C["alaring"] if i == 1 else (C["ala"] if i == 0 else None)

        zones.append(LoopZone(
            inner, superellipse(ac + [side * 0.004, 0.002], 0.05, 0.042,
                                0.036, 2.2),
            [0, .45, 1.0], sp(14), ala_color, C["ala"], accent=False))
    return zones


def static_patches():
    """Free-form zones that sit under the edge loops."""
    patches = []
    for s in (-1, 1):
        cheek = [(0.5 + s * x, y) for x, y in
                 [(0.045, 0.50), (0.15, 0.505), (0.205, 0.47), (0.19, 0.405),
                  (0.13, 0.385), (0.085, 0.43), (0.045, 0.47)]]
        patches.append((cheek, C["cheek"]))
    patches.append(([(0.475, 0.70), (0.525, 0.70), (0.535, 0.47),
                     (0.465, 0.47)], C["bridge"]))
    patches.append((superellipse((0.5, 0.11), 0.085, 0.05, 0.05, 2.6),
                    C["mentum"]))
    return patches


def paint_uv(zones, step):
    rgba = np.zeros((UV_RES, UV_RES, 4), np.float32)
    zmask = np.zeros((UV_RES, UV_RES), np.uint8)

    def fill(poly_uv, col):
        pts = np.round(to_px(poly_uv) * 4).astype(np.int32)
        cv2.fillPoly(rgba, [pts], (*[c / 255 for c in col], 1.0),
                     cv2.LINE_AA, shift=2)

    # free-form patches are snapped to the grid's quads, like a real model
    centers = np.arange(0, 1, step) + step / 2
    cu, cv = np.meshgrid(centers, centers)
    cells = np.stack([cu.ravel(), cv.ravel()], 1)
    cpx = np.round(to_px(cells)).astype(int).clip(0, UV_RES - 1)
    for poly, col in static_patches():
        m = np.zeros((UV_RES, UV_RES), np.uint8)
        cv2.fillPoly(m, [np.round(to_px(poly)).astype(np.int32)], 255)
        for (u, v), hit in zip(cells, m[cpx[:, 1], cpx[:, 0]] > 0):
            if hit:
                h = step / 2
                fill(np.array([[u - h, v - h], [u + h, v - h],
                               [u + h, v + h], [u - h, v + h]]), col)
    for z in zones:
        pts = np.round(to_px(z.outer) * 4).astype(np.int32)
        cv2.fillPoly(rgba, [pts], (0, 0, 0, 0), cv2.LINE_AA, shift=2)
        cv2.fillPoly(zmask, [pts], 255, cv2.LINE_8, shift=2)
        n = len(z.inner)
        for i in range(len(z.rings) - 1):
            a, b = z.rings[i], z.rings[i + 1]
            for k in range(n):
                k2 = (k + 1) % n
                mid = (a[k] + a[k2] + b[k] + b[k2]) / 4
                col = z.band_color(i, mid)
                if col is not None:
                    fill(np.array([a[k], a[k2], b[k2], b[k]]), col)
        if z.inner_color is not None:
            fill(z.inner, z.inner_color)
    return rgba, zmask


# ---------------------------------------------------------------- mesh fit
class FaceFit:
    def __init__(self, img_xyz, uv, tris, shape):
        self.p = img_xyz              # (468, 3) image px + depth
        self.uv = uv
        self.tris = tris
        H, W = shape
        self.H, self.W = H, W
        self._uv_lookup()
        self._rasterize()

    def _uv_lookup(self):
        ids = np.full((UV_RES, UV_RES), -1, np.float32)
        px = to_px(self.uv)
        for t, (a, b, c) in enumerate(self.tris):
            cv2.fillConvexPoly(ids, np.round(px[[a, b, c]]).astype(np.int32),
                               float(t))
        k = np.ones((3, 3), np.uint8)
        for _ in range(4):           # grow into seams so edges still map
            hole = ids < 0
            ids[hole] = cv2.dilate(ids, k)[hole]
        self.uv_ids = ids.astype(np.int32)

    def _rasterize(self):
        H, W = self.H, self.W
        zb = np.full((H, W), np.inf, np.float32)
        u = np.zeros((H, W), np.float32)
        v = np.zeros((H, W), np.float32)
        P, UV = self.p, self.uv
        for a, b, c in self.tris:
            p0, p1, p2 = P[a], P[b], P[c]
            # back-face culling: winding in image must match UV winding
            d1, d2 = p1[:2] - p0[:2], p2[:2] - p0[:2]
            area = d1[0] * d2[1] - d1[1] * d2[0]
            e1, e2 = UV[b] - UV[a], UV[c] - UV[a]
            if abs(area) < 1e-6 or (area > 0) == (e1[0] * e2[1] - e1[1] * e2[0] > 0):
                continue
            x0 = max(int(np.floor(min(p0[0], p1[0], p2[0]))), 0)
            x1 = min(int(np.ceil(max(p0[0], p1[0], p2[0]))), W - 1)
            y0 = max(int(np.floor(min(p0[1], p1[1], p2[1]))), 0)
            y1 = min(int(np.ceil(max(p0[1], p1[1], p2[1]))), H - 1)
            if x1 < x0 or y1 < y0:
                continue
            ys, xs = np.mgrid[y0:y1 + 1, x0:x1 + 1].astype(np.float32)
            w1 = ((xs - p0[0]) * d2[1] - (ys - p0[1]) * d2[0]) / area
            w2 = ((ys - p0[1]) * d1[0] - (xs - p0[0]) * d1[1]) / area
            w0 = 1 - w1 - w2
            inside = (w0 >= -1e-3) & (w1 >= -1e-3) & (w2 >= -1e-3)
            z = w0 * p0[2] + w1 * p1[2] + w2 * p2[2]
            zs = zb[y0:y1 + 1, x0:x1 + 1]
            m = inside & (z < zs)
            if not m.any():
                continue
            zs[m] = z[m]
            u[y0:y1 + 1, x0:x1 + 1][m] = (w0 * UV[a, 0] + w1 * UV[b, 0] + w2 * UV[c, 0])[m]
            v[y0:y1 + 1, x0:x1 + 1][m] = (w0 * UV[a, 1] + w1 * UV[b, 1] + w2 * UV[c, 1])[m]
        self.zb, self.u, self.v = zb, u, v
        self.cover = np.isfinite(zb)
        # nearest surface depth in a small neighbourhood, so points on the
        # rim of the eye / mouth openings still count as visible
        zf = np.where(self.cover, zb, 1e9).astype(np.float32)
        self.zb_near = cv2.erode(zf, np.ones((7, 7), np.uint8))

    def map(self, uvpts):
        """UV points -> (image xy, visible mask)."""
        uvpts = np.asarray(uvpts, np.float64)
        px = np.round(to_px(uvpts)).astype(int)
        ok = (px[:, 0] >= 0) & (px[:, 0] < UV_RES) & (px[:, 1] >= 0) & (px[:, 1] < UV_RES)
        t = np.full(len(uvpts), -1)
        t[ok] = self.uv_ids[px[ok, 1], px[ok, 0]]
        ok &= t >= 0
        out = np.zeros((len(uvpts), 2))
        vis = np.zeros(len(uvpts), bool)
        if not ok.any():
            return out, vis
        tri = self.tris[t[ok]]
        A, B, Cc = self.uv[tri[:, 0]], self.uv[tri[:, 1]], self.uv[tri[:, 2]]
        q = uvpts[ok]
        v0, v1, v2 = B - A, Cc - A, q - A
        den = v0[:, 0] * v1[:, 1] - v1[:, 0] * v0[:, 1]
        den[np.abs(den) < 1e-12] = 1e-12
        w1 = (v2[:, 0] * v1[:, 1] - v1[:, 0] * v2[:, 1]) / den
        w2 = (v0[:, 0] * v2[:, 1] - v2[:, 0] * v0[:, 1]) / den
        w0 = 1 - w1 - w2
        P = self.p
        xyz = (w0[:, None] * P[tri[:, 0]] + w1[:, None] * P[tri[:, 1]]
               + w2[:, None] * P[tri[:, 2]])
        out[ok] = xyz[:, :2]
        xi = np.clip(np.round(xyz[:, 0]).astype(int), 0, self.W - 1)
        yi = np.clip(np.round(xyz[:, 1]).astype(int), 0, self.H - 1)
        tol = 0.012 * self.face_size()
        zn = self.zb_near[yi, xi]
        vis[ok] = (zn < 1e8) & (xyz[:, 2] <= zn + tol)
        return out, vis

    def face_size(self):
        xy = self.p[:, :2]
        return float(np.linalg.norm(xy.max(0) - xy.min(0)))


# ---------------------------------------------------------------- drawing
def polylines_from(fit, uvline, closed=False):
    """Map a dense UV polyline, splitting it where it becomes invisible."""
    if closed:
        uvline = np.vstack([uvline, uvline[:1]])
    xy, vis = fit.map(uvline)
    # drop tiny fragments: they only flicker along the silhouette
    out, cur = [], []
    for p, ok in zip(xy, vis):
        if ok:
            cur.append(p)
            continue
        if len(cur) >= 6:
            out.append(np.array(cur))
        cur = []
    if len(cur) >= 6 or (len(cur) > 1 and len(cur) == len(xy)):
        out.append(np.array(cur))
    return out


def densify(pts, step=0.0015, closed=False):
    pts = np.asarray(pts)
    if closed:
        pts = np.vstack([pts, pts[:1]])
    res = []
    for a, b in zip(pts[:-1], pts[1:]):
        n = max(1, int(np.ceil(np.linalg.norm(b - a) / step)))
        res.append(a + (b - a) * np.linspace(0, 1, n, endpoint=False)[:, None])
    res.append(pts[-1:])
    out = np.vstack(res)
    return out[:-1] if closed else out


def draw_face(canvas, line_layer, accent_layer, fit, zones, zmask, uv_rgba,
              args):
    H, W = canvas.shape[:2]
    fs = fit.face_size()
    th = max(1, int(round(fs / 800)))

    # --- colour fills: sample the UV painting through the fitted mesh
    if not args.no_fill:
        mx = (fit.u * (UV_RES - 1)).astype(np.float32)
        my = ((1 - fit.v) * (UV_RES - 1)).astype(np.float32)
        col = cv2.remap(uv_rgba, mx, my, cv2.INTER_LINEAR,
                        borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        a = col[..., 3] * fit.cover * args.fill_alpha
        rgb = col[..., :3][..., ::-1]               # to BGR
        tint = 1 - a[..., None] + a[..., None] * rgb
        canvas[:] = canvas * tint

    def stroke(layer, lines, t):
        for ln in lines:
            cv2.polylines(layer, [np.round(ln * 16).astype(np.int32)], False,
                          255, t, cv2.LINE_AA, shift=4)

    # --- quad grid over the rest of the face
    if not args.no_grid:
        step = grid_step(args.density)
        ts = np.arange(0, 1.0001, 0.0015)
        for g in np.arange(0, 1.0001, step):
            for line in (np.stack([np.full_like(ts, g), ts], 1),
                         np.stack([ts, np.full_like(ts, g)], 1)):
                px = np.round(to_px(line)).astype(int).clip(0, UV_RES - 1)
                keep = zmask[px[:, 1], px[:, 0]] == 0
                # split on zone interiors, then map each piece
                idx = np.flatnonzero(np.diff(np.r_[0, keep.astype(int), 0]))
                for s, e in zip(idx[::2], idx[1::2]):
                    if e - s > 1:
                        stroke(line_layer, polylines_from(fit, line[s:e]), th)

    # --- edge loops + spokes
    for z in zones:
        for i, t in enumerate(z.ts):
            r = densify(z.ring(t), closed=True)
            layer = accent_layer if i == 0 and z.accent else line_layer
            stroke(layer, polylines_from(fit, r, closed=True), th)
        dense_t = np.linspace(0, 1, 60)
        for k in range(len(z.inner)):
            sp = (1 - dense_t)[:, None] * z.inner[k] + dense_t[:, None] * z.outer[k]
            stroke(line_layer, polylines_from(fit, sp), th)


def grid_step(density):
    return 1.0 / round(32 * density)


def clay(img):
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    g = cv2.createCLAHE(2.0, (8, 8)).apply(g).astype(np.float32)
    g = 70 + 0.72 * g
    out = np.stack([g * 0.99, g * 0.99, g * 1.0], -1)   # very slight cool tint
    return out


# ---------------------------------------------------------------- main
def ensure_model():
    if not os.path.exists(MODEL_PATH):
        os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
        print("Downloading face landmark model...", file=sys.stderr)
        urllib.request.urlretrieve(MODEL_URL, MODEL_PATH + ".part")
        os.replace(MODEL_PATH + ".part", MODEL_PATH)
    return MODEL_PATH


def detect(img_bgr, max_faces):
    import mediapipe as mp
    from mediapipe.tasks.python import BaseOptions
    from mediapipe.tasks.python.vision import face_landmarker as fl
    opts = fl.FaceLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=ensure_model()),
        num_faces=max_faces, min_face_detection_confidence=0.3,
        min_face_presence_confidence=0.3)
    with fl.FaceLandmarker.create_from_options(opts) as det:
        rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
        res = det.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
    return res.face_landmarks


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("input")
    ap.add_argument("-o", "--output")
    ap.add_argument("--style", choices=["clay", "photo"], default="clay",
                    help="clay = grey sculpt look (default), photo = keep colours")
    ap.add_argument("--density", type=float, default=1.0,
                    help="edge density multiplier (0.6 sparse .. 1.6 dense)")
    ap.add_argument("--fill-alpha", type=float, default=0.55)
    ap.add_argument("--line-alpha", type=float, default=0.7)
    ap.add_argument("--no-fill", action="store_true")
    ap.add_argument("--no-grid", action="store_true")
    ap.add_argument("--crop", action="store_true",
                    help="crop to the face(s) and upscale for a close-up")
    ap.add_argument("--max-faces", type=int, default=5)
    args = ap.parse_args()

    img = cv2.imread(args.input, cv2.IMREAD_COLOR)
    if img is None:
        sys.exit(f"Could not read image: {args.input}")
    H, W = img.shape[:2]

    faces = detect(img, args.max_faces)
    if not faces:
        sys.exit("No face detected in the photo.")

    mesh = json.load(open(MESH_JSON))
    uv = np.array(mesh["uv"], np.float64)
    holes = [set(LIPS_INNER), set(EYE_R), set(EYE_L)]
    tris = np.array([t for t in mesh["tris"]
                     if not any(set(t) <= h for h in holes)], np.int64)
    zones = build_zones(uv, args.density)
    uv_rgba, zmask = paint_uv(zones, grid_step(args.density))

    Ps = [np.array([[p.x * W, p.y * H, p.z * W] for p in lm[:468]])
          for lm in faces]
    if args.crop:
        # frame the face(s) like a close-up and upscale small faces so the
        # wireframe stays crisp
        xy = np.vstack([P[:, :2] for P in Ps])
        lo, hi = xy.min(0), xy.max(0)
        pad = (hi - lo).max() * 0.22
        x0, y0 = np.maximum(np.floor(lo - pad), 0).astype(int)
        x1, y1 = np.minimum(np.ceil(hi + pad), [W, H]).astype(int)
        img = img[y0:y1, x0:x1]
        scale = float(np.clip(1100 / max(hi - lo), 1, 4))
        img = cv2.resize(img, None, fx=scale, fy=scale,
                         interpolation=cv2.INTER_CUBIC)
        Ps = [(P - [x0, y0, 0]) * scale for P in Ps]
        H, W = img.shape[:2]

    canvas = clay(img) if args.style == "clay" else img.astype(np.float32)
    lines = np.zeros((H, W), np.uint8)
    accent = np.zeros((H, W), np.uint8)
    for P in Ps:
        fit = FaceFit(P, uv, tris, (H, W))
        draw_face(canvas, lines, accent, fit, zones, zmask, uv_rgba, args)

    la = (lines.astype(np.float32) / 255 * args.line_alpha)[..., None]
    canvas = canvas * (1 - la) + np.array([38, 38, 42]) * la
    aa = (accent.astype(np.float32) / 255 * 0.85)[..., None]
    canvas = canvas * (1 - aa) + np.array([60, 50, 190]) * aa

    out = args.output or os.path.splitext(args.input)[0] + "_topology.png"
    cv2.imwrite(out, np.clip(canvas, 0, 255).astype(np.uint8))
    print(f"{len(faces)} face(s) -> {out}")


if __name__ == "__main__":
    main()
