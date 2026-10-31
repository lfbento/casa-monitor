#!/usr/bin/env python3
"""casa-monitor — CLI principal.

Uso:
  python main.py --capture-test [--seconds 20] [--save-every 5]
  python main.py --detect-test  [--seconds 60]     # YOLO + eventos sem VLM
  python main.py --run                            # daemon 24/7 + relatório 22h
  python main.py --generate-report-now            # gera e envia o relatório agora
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time

from PIL import Image

import settings
from capture.decoder import H264StreamDecoder
from capture.ws_client import MilocoClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s - %(message)s",
)
# httpx em INFO loga URLs de requisição (vazaria tokens de bot/API no console)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
log = logging.getLogger("casa-monitor")


async def capture_test(seconds: int, save_every: int) -> int:
    """Conecta no stream real, decodifica e salva frames de amostra."""
    settings.ensure_dirs()
    out_dir = settings.DATA_DIR / "capture_test"
    out_dir.mkdir(parents=True, exist_ok=True)

    client = MilocoClient()
    await client.login()
    camera_id = await client.resolve_camera_id()
    decoder = H264StreamDecoder()

    saved = decoded = 0
    t0 = time.monotonic()
    last_pts = 0.0
    log.info("Capturando por %ds da câmera %s ...", seconds, camera_id)

    async for chunk in client.stream_h264(camera_id):
        for frame in decoder.feed(chunk):
            decoded += 1
            last_pts = frame.pts
            if save_every and decoded % save_every == 0:
                path = out_dir / f"frame_{frame.seq:06d}.jpg"
                Image.fromarray(frame.image[:, :, ::-1]).save(path, quality=90)
                saved += 1
            if decoded == 1:
                h, w = frame.image.shape[:2]
                log.info("Primeiro frame: %dx%d", w, h)
        if time.monotonic() - t0 >= seconds:
            break

    elapsed = time.monotonic() - t0
    fps = decoded / elapsed if elapsed else 0
    log.info(
        "OK: %d frames decodificados em %.1fs (%.1f FPS), %d salvos em %s | último pts=%.2fs",
        decoded, elapsed, fps, saved, out_dir, last_pts,
    )
    return 0 if decoded > 0 else 2


def detect_test(seconds: int) -> int:
    """Roda YOLO + máquina de eventos por N segundos e resume."""
    from pipeline import MonitorPipeline

    pipe = MonitorPipeline()
    asyncio.run(pipe.run(duration_seconds=seconds))
    return 0


def generate_report_now(send: bool = True) -> int:
    from db.store import Store
    from notifier import send as notify
    from report.daily_report import generate_report

    settings.ensure_dirs()
    store = Store()
    path = generate_report(store)
    text = path.read_text(encoding="utf-8")
    log.info("Relatório: %s (%d chars)", path, len(text))
    if send:
        ok = notify(text)
        log.info("Envio: %s", "ok" if ok else "FALHOU (veja logs do notifier)")
    return 0


def identify_person(event_id: int, name: str) -> int:
    """Registra a identidade da pessoa de um evento (quando o usuário responde)."""
    from pathlib import Path

    from db.store import Store
    from events.identity import crop_hist

    store = Store()
    ev = store.event_by_id(event_id)
    if not ev:
        log.error("evento #%d não encontrado", event_id)
        return 1
    if not ev.get("snapshot_path") or not Path(ev["snapshot_path"]).exists():
        log.error("evento #%d sem snapshot válido", event_id)
        return 1
    hist = crop_hist(Path(ev["snapshot_path"]))
    if hist is None:
        log.error("não consegui extrair a aparência do snapshot")
        return 1
    desc = (ev.get("vlm_description") or "")[:200]
    pid = store.add_known_person(name, desc, ev["snapshot_path"], hist.tobytes(),
                                 source_event_id=event_id)
    store.set_event_person_label(event_id, name)
    log.info("pessoa registrada: #%d %s (evento #%d) — eventos futuros parecidos sairão identificados",
             pid, name, event_id)
    return 0


def list_people() -> int:
    from db.store import Store

    people = Store().known_people()
    if not people:
        print("nenhuma pessoa registrada ainda")
        return 0
    for p in people:
        print(f"#{p['id']} {p['name']} — {p['description'] or '(sem descrição)'} "
              f"[evento origem #{p['source_event_id']}]")
    return 0


def run_daemon() -> int:
    """Daemon 24/7: pipeline contínuo + relatório às 22h00 + manutenção 23h00."""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    from pipeline import MonitorPipeline
    from notifier import send as notify
    from report.daily_report import generate_report

    async def _main() -> None:
        pipe = MonitorPipeline()
        scheduler = AsyncIOScheduler(timezone="America/Sao_Paulo")

        def daily_report() -> None:
            try:
                path = generate_report(pipe.store)
                notify(path.read_text(encoding="utf-8"))
                log.info("relatório 22h enviado: %s", path)
            except Exception as err:  # pylint: disable=broad-exception-caught
                log.error("relatório 22h falhou: %s", err)

        scheduler.add_job(daily_report, "cron", hour=22, minute=0, id="relatorio_22h")
        scheduler.add_job(pipe.maintenance, "cron", hour=23, minute=0, id="manutencao")
        scheduler.start()
        log.info("agendador ativo: relatório 22h00 + manutenção 23h00 (America/Sao_Paulo)")
        await pipe.run()

    asyncio.run(_main())
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitoramento inteligente + relatório diário 22h")
    parser.add_argument("--capture-test", action="store_true", help="testa a captura real da câmera")
    parser.add_argument("--detect-test", action="store_true", help="roda YOLO+eventos por N segundos")
    parser.add_argument("--seconds", type=int, default=20, help="duração dos testes (s)")
    parser.add_argument("--save-every", type=int, default=25, help="salva 1 JPEG a cada N frames (0=não salva)")
    parser.add_argument("--generate-report-now", action="store_true", help="gera e envia o relatório agora")
    parser.add_argument("--no-send", action="store_true", help="com --generate-report-now: só gera, não envia")
    parser.add_argument("--identify-person", nargs=2, metavar=("EVENTO_ID", "NOME"),
                        help="registra a identidade da pessoa de um evento")
    parser.add_argument("--list-people", action="store_true", help="lista pessoas registradas")
    parser.add_argument("--run", action="store_true", help="roda o daemon 24/7")
    args = parser.parse_args()

    if args.capture_test:
        return asyncio.run(capture_test(args.seconds, args.save_every))
    if args.detect_test:
        return detect_test(args.seconds)
    if args.generate_report_now:
        return generate_report_now(send=not args.no_send)
    if args.identify_person:
        return identify_person(int(args.identify_person[0]), args.identify_person[1])
    if args.list_people:
        return list_people()
    if args.run:
        return run_daemon()

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
