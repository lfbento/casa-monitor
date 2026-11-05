"""Entrega do relatório — Telegram / WhatsApp (Evolution API) / E-mail (SMTP).

Canal selecionado por NOTIFY_CHANNEL no .env. Envio em blocos (Telegram limita
mensagens a 4096 chars).
"""
from __future__ import annotations

import logging
import os
import smtplib
from email.mime.text import MIMEText
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

logger = logging.getLogger(__name__)

CHANNEL = os.getenv("NOTIFY_CHANNEL", "telegram")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")
WA_URL = os.getenv("WHATSAPP_EVOLUTION_URL", "").rstrip("/")
WA_KEY = os.getenv("WHATSAPP_EVOLUTION_KEY", "")
WA_INSTANCE = os.getenv("WHATSAPP_EVOLUTION_INSTANCE", "")
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", "")
SMTP_TO = os.getenv("SMTP_TO", "")


def _chunks(text: str, size: int = 3900) -> list[str]:
    parts, buf = [], ""
    for line in text.splitlines(keepends=True):
        if len(buf) + len(line) > size:
            parts.append(buf)
            buf = ""
        buf += line
    if buf:
        parts.append(buf)
    return parts or [""]


def _http_client() -> httpx.Client:
    """Cliente HTTP com IPv4 forçado e timeout curto.

    O hang de ~60s nos uploads do Telegram era o httpx caindo no buraco
    negro de IPv6 (conexão pendurada até o timeout) — atrasando alertas em
    1 minuto inteiro. IPv4 + timeout curto + retry limitam o pior caso
    a poucos segundos.
    """
    return httpx.Client(
        timeout=httpx.Timeout(12.0, connect=5.0),
        transport=httpx.HTTPTransport(local_address="0.0.0.0"),
    )


def send_telegram(text: str) -> bool:
    if not (TELEGRAM_TOKEN and TELEGRAM_CHAT):
        logger.error("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID não configurados")
        return False
    ok = True
    for part in _chunks(text):
        for attempt in (1, 2):
            try:
                with _http_client() as client:
                    resp = client.post(
                        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                        json={"chat_id": TELEGRAM_CHAT, "text": part},
                    )
                ok = ok and resp.status_code == 200
                if resp.status_code != 200:
                    logger.error("Telegram HTTP %s: %s", resp.status_code, resp.text[:200])
                break
            except Exception as err:  # pylint: disable=broad-exception-caught
                logger.error("Telegram falhou (tentativa %d): %s", attempt, err)
                ok = False
    return ok


def send_photo(image_path: str, caption: str) -> bool:
    """Envia UMA foto com legenda (alerta de evento). Telegram: sendPhoto."""
    from pathlib import Path

    p = Path(image_path)
    if not p.exists():
        logger.error("foto não existe: %s", image_path)
        return send(caption)
    if CHANNEL == "telegram" and TELEGRAM_TOKEN and TELEGRAM_CHAT:
        for attempt in (1, 2):
            try:
                with p.open("rb") as fh, _http_client() as client:
                    resp = client.post(
                        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
                        data={"chat_id": TELEGRAM_CHAT, "caption": caption[:1000]},
                        files={"photo": (p.name, fh, "image/jpeg")},
                    )
                if resp.status_code == 200:
                    return True
                logger.error("sendPhoto HTTP %s: %s", resp.status_code, resp.text[:200])
            except Exception as err:  # pylint: disable=broad-exception-caught
                logger.error("sendPhoto falhou (tentativa %d): %s", attempt, err)
    # fallback: texto (com o caminho da foto)
    return send(f"{caption}\n\n📷 {image_path}")


def send_whatsapp_evolution(text: str) -> bool:
    if not (WA_URL and WA_KEY and WA_INSTANCE):
        logger.error("WHATSAPP_EVOLUTION_* não configurados")
        return False
    try:
        resp = httpx.post(
            f"{WA_URL}/message/sendText/{WA_INSTANCE}",
            headers={"apikey": WA_KEY},
            json={"number": SMTP_TO or TELEGRAM_CHAT, "text": text},
            timeout=30,
        )
        return resp.status_code in (200, 201)
    except Exception as err:  # pylint: disable=broad-exception-caught
        logger.error("Evolution API falhou: %s", err)
        return False


def send_email(text: str) -> bool:
    if not (SMTP_HOST and SMTP_TO):
        logger.error("SMTP_HOST / SMTP_TO não configurados")
        return False
    try:
        msg = MIMEText(text, "plain", "utf-8")
        msg["Subject"] = "Relatório Diário de Segurança — casa"
        msg["From"] = SMTP_FROM or SMTP_USER
        msg["To"] = SMTP_TO
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as s:
            s.starttls()
            if SMTP_USER:
                s.login(SMTP_USER, SMTP_PASSWORD)
            s.send_message(msg)
        return True
    except Exception as err:  # pylint: disable=broad-exception-caught
        logger.error("E-mail falhou: %s", err)
        return False


def send(text: str) -> bool:
    """Dispara pelo canal configurado em NOTIFY_CHANNEL."""
    if CHANNEL == "telegram":
        return send_telegram(text)
    if CHANNEL == "whatsapp_evolution":
        return send_whatsapp_evolution(text)
    if CHANNEL == "email":
        return send_email(text)
    logger.info("NOTIFY_CHANNEL=none — relatório só salvo em disco")
    return True
