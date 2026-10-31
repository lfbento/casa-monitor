#!/usr/bin/env python3
"""Calibração das zonas (ROIs) — captura 1 frame real e ajuda a definir polígonos.

Modos:
  --grid     salva frame com grade de coordenadas (10%) para você ler os pontos
  --click    janela OpenCV: clique os vértices do polígono (ENTER fecha, ESC cancela)
  --from     usa um JPEG já salvo em vez de capturar ao vivo

Saída: config/zones.yaml
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from capture.decoder import H264StreamDecoder
from capture.ws_client import MilocoClient
from settings import DATA_DIR, ensure_dirs

OUT = Path(__file__).resolve().parent.parent / "config"


async def grab_frame() -> np.ndarray:
    """Captura o primeiro frame decodificado do stream ao vivo."""
    ensure_dirs()
    client = MilocoClient()
    await client.login()
    camera_id = await client.resolve_camera_id()
    decoder = H264StreamDecoder()
    result: np.ndarray | None = None

    async def _grab() -> None:
        nonlocal result
        count = 0
        async for chunk in client.stream_h264_once(camera_id):
            for frame in decoder.feed(chunk):
                count += 1
                # os primeiros frames do stream vêm cinza (warm-up do codec):
                # só aceita frame com variância real de pixels
                if count >= 5 and float(frame.image.std()) > 8.0:
                    result = frame.image
                    break
                if count > 300:
                    raise RuntimeError("300 frames sem imagem válida (tudo cinza?)")
            if result is not None:
                break

    await asyncio.wait_for(_grab(), timeout=45)
    if result is None:
        raise RuntimeError("stream terminou sem frames")
    return result


def draw_grid(img: np.ndarray, out_path: Path, step_pct: int = 10) -> None:
    """Salva a imagem com grade + rótulos de coordenadas em pixels."""
    pil = Image.fromarray(img[:, :, ::-1])
    w, h = pil.size
    draw = ImageDraw.Draw(pil)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 16)
        font_sm = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 13)
    except OSError:
        font = font_sm = ImageFont.load_default()

    step_x = w * step_pct // 100
    step_y = h * step_pct // 100
    for x in range(0, w + 1, step_x):
        color = (255, 60, 60) if x % (step_x * 2) == 0 else (255, 200, 0)
        draw.line([(x, 0), (x, h)], fill=color, width=1)
        draw.text((x + 3, 4), str(x), fill=color, font=font_sm)
    for y in range(0, h + 1, step_y):
        color = (255, 60, 60) if y % (step_y * 2) == 0 else (255, 200, 0)
        draw.line([(0, y), (w, y)], fill=color, width=1)
        draw.text((4, y + 3), str(y), fill=color, font=font_sm)
    draw.text((w // 2 - 200, h - 30), f"frame {w}x{h} — grade a cada {step_pct}%", fill=(255, 255, 255), font=font)
    pil.save(out_path, quality=92)
    print(f"Grade salva em: {out_path}")


def click_zones(img: np.ndarray, out_path: Path) -> None:
    """Coleta polígonos clicando (requer display)."""
    import cv2

    zones = {}
    names = ["ZONA_RUA", "ZONA_PORTAO", "LINHA_ACESSO_CASA"]
    win = "calibração - clique vértices | ENTER fecha polígono | r reinicia | ESC sai"
    current: list[tuple[int, int]] = []

    def on_mouse(event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN:
            current.append((x, y))
            print(f"  ponto: ({x}, {y})")

    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(win, on_mouse)
    for name in names:
        current.clear()
        print(f"\nDefina {name} ({'2 pontos = linha' if 'LINHA' in name else 'polígono'}):")
        while True:
            disp = img.copy()
            for zn, pts in zones.items():
                arr = np.array(pts, np.int32)
                cv2.polylines(disp, [arr], True, (0, 255, 255), 2)
                cv2.putText(disp, zn, tuple(arr[0]), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
            if current:
                arr = np.array(current, np.int32)
                cv2.polylines(disp, [arr], "LINHA" in name, (0, 0, 255), 2)
            cv2.imshow(win, disp)
            key = cv2.waitKey(30) & 0xFF
            if key == 27:
                print("Cancelado.")
                return
            if key == ord("r"):
                current.clear()
            if key in (13, 10) and len(current) >= 2:
                zones[name] = current.copy()
                break
    cv2.destroyAllWindows()
    write_yaml(out_path, img.shape[1], img.shape[0], zones)
    print(f"Zonas salvas em: {out_path}")


def write_yaml(path: Path, w: int, h: int, zones: dict) -> None:
    import yaml

    data = {
        "image_size": [int(w), int(h)],
        "zones": {k: [[int(x), int(y)] for x, y in pts] for k, pts in zones.items()},
        "house_side": [int(w * 0.15), int(h * 0.15)],  # ajuste manual depois
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


def preview_zones(img: np.ndarray, out_path: Path) -> None:
    """Desenha as zonas de config/zones.yaml sobre o frame (validação visual)."""
    from PIL import ImageDraw

    from zones.roi import Zones

    z = Zones.load()
    pil = Image.fromarray(img[:, :, ::-1])
    draw = ImageDraw.Draw(pil)
    colors = {"ZONA_RUA": (255, 200, 0), "ZONA_PORTAO": (255, 40, 40), "ZONA_INTERNA": (40, 220, 40)}
    for name, pts in z.polygons.items():
        color = colors.get(name, (255, 255, 255))
        draw.line(list(pts) + [pts[0]], fill=color, width=3)
        draw.text((pts[0][0] + 6, pts[0][1] + 6), name, fill=color)
    if z.access_line:
        (x1, y1), (x2, y2) = z.access_line
        draw.line([(x1, y1), (x2, y2)], fill=(255, 0, 255), width=4)
        draw.text((x1 + 6, y1 - 22), "LINHA_ACESSO_CASA", fill=(255, 0, 255))
    if z.house_side:
        x, y = z.house_side
        draw.ellipse([x - 8, y - 8, x + 8, y + 8], fill=(255, 0, 255))
        draw.text((x + 12, y - 8), "house_side", fill=(255, 0, 255))
    pil.save(out_path, quality=92)
    print(f"Preview salvo em: {out_path}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Calibra as zonas de monitoramento")
    ap.add_argument("--grid", action="store_true", help="gera frame com grade de coordenadas")
    ap.add_argument("--preview", action="store_true", help="desenha as zonas de zones.yaml sobre o frame")
    ap.add_argument("--click", action="store_true", help="define polígonos clicando (precisa de display)")
    ap.add_argument("--from", dest="src", help="usa um JPEG existente em vez de capturar")
    args = ap.parse_args()

    if args.src:
        img = np.array(Image.open(args.src).convert("RGB"))[:, :, ::-1].copy()
    else:
        img = asyncio.run(grab_frame())

    out = OUT / ("zones_draft.jpg" if args.grid else "zones.yaml")
    if args.grid:
        draw_grid(img, DATA_DIR / "calibration_grid.jpg")
        return 0
    if args.preview:
        preview_zones(img, DATA_DIR / "zones_preview.jpg")
        return 0
    if args.click:
        click_zones(img, out)
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
