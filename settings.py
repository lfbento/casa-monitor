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
    # ---- frescor da captura (OSD vs chegada) ----
    capture_age_warn_s: float = field(default_factory=lambda: _positive_float("CAPTURE_AGE_WARN_SECONDS", 30.0))
    capture_age_fail_s: float = field(default_factory=lambda: _positive_float("CAPTURE_AGE_FAIL_SECONDS", 60.0))
    capture_age_ocr_interval_s: float = field(
        default_factory=lambda: _positive_float("CAPTURE_AGE_OCR_INTERVAL_SECONDS", 30.0))
    capture_age_resync_s: float = field(
        default_factory=lambda: _positive_float("CAPTURE_AGE_RESYNC_SECONDS", 300.0))
    capture_age_resync_wait_s: float = field(
        default_factory=lambda: _positive_float("CAPTURE_AGE_RESYNC_WAIT_SECONDS", 10.0))
    # ---- cadência e gate de movimento ----
    detect_min_effective_ratio: float = field(
        default_factory=lambda: _positive_float("DETECT_MIN_EFFECTIVE_RATIO", 0.5))
    motion_sanity_interval_s: float = field(
        default_factory=lambda: _positive_float("MOTION_SANITY_INTERVAL_SECONDS", 10.0))
    # ---- confirmação e agregação de passagem ----
    track_min_hits: int = field(default_factory=lambda: _positive_int("TRACK_MIN_HITS", 3))
    track_min_lifetime_s: float = field(
        default_factory=lambda: _positive_float("TRACK_MIN_LIFETIME_S", 1.5))
    passage_merge_s: float = field(default_factory=lambda: _positive_float("PASSAGE_MERGE_SECONDS", 5.0))
    passage_merge_distance_px: int = field(
        default_factory=lambda: _positive_int("PASSAGE_MERGE_DISTANCE_PX", 200))
    passage_min_path_px: int = field(
        default_factory=lambda: _positive_int("PASSAGE_MIN_PATH_PX", 40))


def ensure_dirs() -> None:
    for d in (DATA_DIR, REPORTS_DIR, DATA_DIR / "snapshots"):
        d.mkdir(parents=True, exist_ok=True)
