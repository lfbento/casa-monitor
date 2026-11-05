"""Configuração central — carrega o .env da raiz do projeto."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
REPORTS_DIR = ROOT / "reports"

load_dotenv(ROOT / ".env")


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _positive_float(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _positive_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


@dataclass
class MilocoConfig:
    base_url: str = field(default_factory=lambda: os.getenv("MILOCO_BASE_URL", "https://localhost:8010"))
    verify_ssl: bool = field(default_factory=lambda: _bool("MILOCO_VERIFY_SSL", False))
    username: str = field(default_factory=lambda: os.getenv("MILOCO_USERNAME", "admin"))
    login_code: str = field(default_factory=lambda: os.getenv("MILOCO_LOGIN_CODE", ""))
    token: str = field(default_factory=lambda: os.getenv("MILOCO_TOKEN", ""))
    camera_id: str = field(default_factory=lambda: os.getenv("MILOCO_CAMERA_ID", ""))
    channel: int = field(default_factory=lambda: _int("MILOCO_CHANNEL", 0))

    @property
    def ws_url(self) -> str:
        base = self.base_url.rstrip("/")
        ws_base = base.replace("https://", "wss://").replace("http://", "ws://")
        return f"{ws_base}/api/miot/ws/video_stream"


@dataclass
class DetectConfig:
    model: str = field(default_factory=lambda: os.getenv("DETECT_MODEL", "yolo11n.pt"))
    fps: int = field(default_factory=lambda: _int("DETECT_FPS", 10))
    conf: float = field(default_factory=lambda: float(os.getenv("DETECT_CONF", "0.35")))


@dataclass
class RulesConfig:
    loitering_seconds: int = field(default_factory=lambda: _int("LOITERING_SECONDS", 30))
    vehicle_stop_seconds: int = field(default_factory=lambda: _int("VEHICLE_STOP_SECONDS", 20))
    gate_dwell_seconds: int = field(default_factory=lambda: _int("GATE_DWELL_SECONDS", 5))
    night_window: str = field(default_factory=lambda: os.getenv("NIGHT_WINDOW", "00:00-05:30"))
    snapshot_retention_days: int = field(default_factory=lambda: _int("SNAPSHOT_RETENTION_DAYS", 7))

    @property
    def night_bounds(self) -> tuple[int, int, int, int]:
        """Retorna (h1, m1, h2, m2) da janela noturna."""
        start, _, end = self.night_window.partition("-")
        sh, sm = (int(x) for x in start.split(":"))
        eh, em = (int(x) for x in end.split(":"))
        return sh, sm, eh, em


@dataclass
class PipelineLimits:
    """Limites de frescor do frame, fila de análise e latência de alerta."""
    max_frame_age_s: float = field(default_factory=lambda: _positive_float("MAX_FRAME_AGE_SECONDS", 2.0))
    alert_max_latency_s: float = field(default_factory=lambda: _positive_float("ALERT_MAX_LATENCY_SECONDS", 5.0))
    analysis_queue_max: int = field(default_factory=lambda: _positive_int("ANALYSIS_QUEUE_MAX", 8))
    analysis_max_age_s: float = field(default_factory=lambda: _positive_float("ANALYSIS_MAX_AGE_SECONDS", 180.0))
    analysis_dedup_s: float = field(default_factory=lambda: _positive_float("ANALYSIS_DEDUP_SECONDS", 60.0))
    analysis_min_area_px: int = field(default_factory=lambda: _positive_int("ANALYSIS_MIN_AREA_PX", 2500))
    analysis_min_conf: float = field(default_factory=lambda: _positive_float("ANALYSIS_MIN_CONF", 0.5))
    analysis_timeout_s: float = field(default_factory=lambda: _positive_float("ANALYSIS_TIMEOUT_SECONDS", 30.0))


def ensure_dirs() -> None:
    for d in (DATA_DIR, REPORTS_DIR, DATA_DIR / "snapshots"):
        d.mkdir(parents=True, exist_ok=True)
