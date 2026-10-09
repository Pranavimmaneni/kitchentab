"""Photo-like degradation for receipt renders (Pillow only; a dev dependency, see ADR-0020).

Order follows what a phone photo of a paper receipt goes through: the print fades (thermal),
the paper crumples (mesh warp), it lies on a table at an angle (background + rotation), the room
light falls off across it (shadow gradient), the lens is a little soft (blur), the sensor adds
noise, and the camera saves a JPEG.
"""

from __future__ import annotations

import math
import random
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageFilter

TABLE = (118, 104, 92)


def _fade(img: Image.Image, amount: float, rng: random.Random) -> Image.Image:
    """Thermal fade: dark print lightens, unevenly (a few vertical streaks and one faded end)."""
    if amount <= 0:
        return img
    white = Image.new("RGB", img.size, (250, 249, 244))
    faded = Image.blend(img, white, amount)
    mask = Image.linear_gradient("L").resize(img.size)
    if rng.random() < 0.5:
        mask = mask.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    draw = ImageDraw.Draw(mask)
    for _ in range(3):
        x = rng.randrange(0, img.width)
        draw.rectangle(
            [x, 0, x + rng.randrange(8, 40), img.height], fill=rng.randrange(150, 255)
        )
    mask = mask.filter(ImageFilter.GaussianBlur(6))
    return Image.composite(faded, img, mask)


def _warp(img: Image.Image, amp: float, rng: random.Random) -> Image.Image:
    """Crumple: smooth sinusoidal displacement on a mesh."""
    if amp <= 0:
        return img
    w, h = img.size
    nx, ny = 8, max(int(h / 120), 4)
    ph1, ph2 = rng.uniform(0, math.tau), rng.uniform(0, math.tau)
    lx, ly = rng.uniform(250, 500), rng.uniform(200, 380)

    def disp(x: float, y: float) -> tuple[float, float]:
        return (
            x + amp * math.sin(y / ly * math.tau + ph1),
            y + amp * 0.7 * math.sin(x / lx * math.tau + ph2),
        )

    mesh = []
    for j in range(ny):
        for i in range(nx):
            x0, x1 = w * i / nx, w * (i + 1) / nx
            y0, y1 = h * j / ny, h * (j + 1) / ny
            box = (int(x0), int(y0), int(x1), int(y1))
            quad = (*disp(x0, y0), *disp(x0, y1), *disp(x1, y1), *disp(x1, y0))
            mesh.append((box, quad))
    return img.transform(
        img.size,
        Image.Transform.MESH,
        mesh,
        Image.Resampling.BICUBIC,
        fillcolor=(250, 249, 244),
    )


def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    """Gaussian elimination for the 8x8 perspective system (no numpy in the dev deps)."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[p] = m[p], m[c]
        for r in range(n):
            if r != c:
                f = m[r][c] / m[c][c]
                m[r] = [x - f * y for x, y in zip(m[r], m[c], strict=True)]
    return [m[i][n] / m[i][i] for i in range(n)]


def _perspective(img: Image.Image, amount: float, rng: random.Random) -> Image.Image:
    """Keystone: the phone was not held parallel to the receipt (one end farther away)."""
    if amount <= 0:
        return img
    w, h = img.size
    dx = w * amount
    far_top = rng.random() < 0.5
    # where each output corner samples from in the input
    src = (
        [(-dx, 0), (w + dx, 0), (w, h), (0, h)]
        if far_top
        else [(0, 0), (w, 0), (w + dx, h), (-dx, h)]
    )
    dst = [(0, 0), (w, 0), (w, h), (0, h)]
    a: list[list[float]] = []
    b: list[float] = []
    for (x, y), (u, v) in zip(dst, src, strict=True):
        a.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        b.append(u)
        a.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        b.append(v)
    coeffs = _solve(a, b)
    return img.transform(
        img.size,
        Image.Transform.PERSPECTIVE,
        coeffs,
        Image.Resampling.BICUBIC,
        fillcolor=TABLE,
    )


def _shadow(img: Image.Image, amount: float, rng: random.Random) -> Image.Image:
    if amount <= 0:
        return img
    # Rotate a gradient as wide as the photo diagonal, then crop the middle: no black corners.
    side = int(math.hypot(*img.size)) + 4
    big = (
        Image.linear_gradient("L")
        .resize((side, side))
        .rotate(rng.uniform(0, 360), resample=Image.Resampling.BILINEAR)
    )
    left, top = (side - img.width) // 2, (side - img.height) // 2
    grad = big.crop((left, top, left + img.width, top + img.height))
    lo = int(255 * (1 - amount))
    grad = grad.point(lambda v: lo + (255 - lo) * v // 255)
    return ImageChops.multiply(img, Image.merge("RGB", (grad, grad, grad)))


def apply_noise(path: Path, n: dict[str, Any], rng: random.Random) -> Image.Image:
    img = Image.open(path).convert("RGB")
    img = _fade(img, float(n.get("fade", 0)), rng)
    img = _warp(img, float(n.get("warp", 0)), rng)
    mx, my = int(img.width * 0.06), int(img.width * 0.05)
    canvas = Image.new("RGB", (img.width + 2 * mx, img.height + 2 * my), TABLE)
    canvas.paste(img, (mx, my))
    img = _perspective(canvas, float(n.get("persp", 0)), rng)
    img = img.rotate(
        float(n.get("rot", 0)),
        resample=Image.Resampling.BICUBIC,
        expand=True,
        fillcolor=TABLE,
    )
    img = _shadow(img, float(n.get("shadow", 0)), rng)
    if n.get("blur"):
        img = img.filter(ImageFilter.GaussianBlur(float(n["blur"])))
    grain = Image.effect_noise(img.size, 18).convert("RGB")
    img = Image.blend(img, grain, 0.07)
    return img


def save_image(
    imgs: list[Image.Image], out: Path, fid: str, fmt: str, quality: int
) -> list[str]:
    files: list[str] = []
    if fmt == "pdf":
        name = f"{fid}.pdf"
        rgb = [im.convert("RGB") for im in imgs]
        # Pillow stores each page as a JPEG image: an image-only ("scanned") PDF with no text layer.
        rgb[0].save(
            out / name,
            "PDF",
            resolution=200.0,
            save_all=True,
            append_images=rgb[1:],
            quality=quality,
        )
        return [name]
    for k, im in enumerate(imgs, start=1):
        suffix = f"_{k}" if len(imgs) > 1 else ""
        name = f"{fid}{suffix}.{fmt}"
        if fmt == "png":
            im.convert("L").save(out / name, "PNG", optimize=True)
        else:
            im.save(out / name, "JPEG", quality=quality, optimize=True)
        files.append(name)
    return files
