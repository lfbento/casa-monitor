"""Zonas / ROIs configuráveis — carrega config/zones.yaml e faz consultas geométricas.

Formato do zones.yaml (coordenadas em pixels do frame decodificado, ex.: 1280x720):
  image_size: [1280, 720]
  zones:
    ZONA_RUA:      [[x,y], [x,y], ...]        # polígono
    ZONA_PORTAO:   [[x,y], [x,y], ...]        # polígono
    LINHA_ACESSO_CASA: [[x1,y1], [x2,y2]]     # linha virtual (2 pontos)
  house_side: [x, y]   # ponto do lado DE DENTRO da casa da LINHA_ACESSO_CASA;
                       # cruza a linha terminando nesse lado = ENTRY
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

ZONES_FILE = Path(__file__).resolve().parent.parent / "config" / "zones.yaml"


def point_in_polygon(x: float, y: float, polygon: list[tuple[float, float]]) -> bool:
    """Ray casting."""
    inside = False
    n = len(polygon)
    for i in range(n):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xint = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < xint:
                inside = not inside
    return inside


def seg_side(p1, p2, pt) -> float:
    """Sinal orientado: de que lado da reta p1->p2 está pt (>0 / <0 / 0)."""
    return (p2[0] - p1[0]) * (pt[1] - p1[1]) - (p2[1] - p1[1]) * (pt[0] - p1[0])


def segments_cross(a1, a2, b1, b2) -> bool:
    d1 = seg_side(b1, b2, a1)
    d2 = seg_side(b1, b2, a2)
    d3 = seg_side(a1, a2, b1)
    d4 = seg_side(a1, a2, b2)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))


@dataclass
class Zones:
    image_size: tuple[int, int] = (1280, 720)
    polygons: dict[str, list[tuple[float, float]]] = field(default_factory=dict)
    access_line: Optional[tuple[tuple[float, float], tuple[float, float]]] = None
    house_side: Optional[tuple[float, float]] = None

    @classmethod
    def load(cls, path: Path = ZONES_FILE) -> "Zones":
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        z = cls()
        z.image_size = tuple(raw.get("image_size", [1280, 720]))
        for name, pts in (raw.get("zones") or {}).items():
            pts = [tuple(map(float, p)) for p in pts]
            if name == "LINHA_ACESSO_CASA" and len(pts) == 2:
                z.access_line = (pts[0], pts[1])
            else:
                z.polygons[name] = pts
        hs = raw.get("house_side")
        z.house_side = tuple(map(float, hs)) if hs else None
        return z

    def zone_at(self, x: float, y: float) -> Optional[str]:
        """Nome da zona que contém o ponto (ordem de prioridade: portão > rua)."""
        for name in ("ZONA_PORTAO", "ZONA_RUA", "ZONA_INTERNA"):
            if name in self.polygons and point_in_polygon(x, y, self.polygons[name]):
                return name
        return None

    def crossing_entry(self, prev_pt, new_pt) -> Optional[str]:
        """Se o segmento prev->new cruzou a LINHA_ACESSO_CASA, devolve
        'ENTRY' (terminou do lado da casa) ou 'EXIT'. Caso contrário, None."""
        if not self.access_line or not self.house_side:
            return None
        a, b = self.access_line
        if not segments_cross(prev_pt, new_pt, a, b):
            return None
        side = seg_side(a, b, self.house_side)
        end_side = seg_side(a, b, new_pt)
        return "ENTRY" if (side > 0) == (end_side > 0) else "EXIT"
