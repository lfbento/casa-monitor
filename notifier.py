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


def send_telegram(text: str) -> bool:
    if not (TELEGRAM_TOKEN and TELEGRAM_CHAT):
        logger.error("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID não configurados")
        return False
    ok = True
    for part in _chunks(text):
        try:
            resp = httpx.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT, "text": part},
                timeout=30,
            )
            ok = ok and resp.status_code == 200
            if resp.status_code != 200:
                logger.error("Telegram HTTP %s: %s", resp.status_code, resp.text[:200])
        except Exception as err:  # pylint: disable=broad-exception-caught
            logger.error("Telegram falhou: %s", err)
            ok = False
    return ok


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
