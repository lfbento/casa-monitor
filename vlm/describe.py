"""Análise semântica de eventos com VLM (OpenAI-compatible) — spec §3.5.

Provedor configurável via .env: Gemini Flash, OpenAI-compatible (MiMo/TencentDB)
ou Ollama local. O VLM recebe 1-3 keyframes do evento e devolve descrição
(pessoa/veículo/postura) + risk_level.
"""
from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import httpx

from settings import ROOT

logger = logging.getLogger(__name__)

import os

VLM_BASE = os.getenv("VLM_BASE_URL", "http://localhost:8421/v1").rstrip("/")
VLM_KEY = os.getenv("VLM_API_KEY", "")
VLM_MODEL = os.getenv("VLM_MODEL", "mimo-v2.6-pro")

_PROMPT = """Você é um sistema de segurança residencial. Analise esta imagem (câmera de segurança \
frente à porta/portão de uma casa, com visão para a rua) e o evento detectado: "{evento}".

Descreva objetivamente:
1. PESSOA(S): roupas (cor/tipo), objetos em mãos, postura/direção.
2. VEÍCULO (se houver): tipo, cor, modelo aparente, placa se legível.
3. POSTURA/ação: alguém demonstra comportamento suspeito (olhando por cima do portão, \
mexendo em fechadura/manopla de carro, testando algo) ou é apenas passagem normal?

Responda SOMENTE com JSON:
{{"descricao": "resumo curto em português", "risk_level": "baixo|medio|alto", "suspeito": true|false, "motivo": "breve"}}"""


@dataclass
class VlmResult:
    descricao: str
    risk_level: str
    suspeito: bool
    motivo: str


def _encode_image(path: Path | str) -> str:
    return base64.b64encode(Path(path).read_bytes()).decode("ascii")


def describe_event(event_type: str, snapshot_paths: list[Path | str],
                   timeout: float = 60.0) -> VlmResult | None:
    """Envia os keyframes ao VLM e devolve a análise. None se indisponível.

    Aceita caminhos como `Path` ou `str` (o banco e o EventImages guardam
    textos); normaliza na entrada para não quebrar o worker por tipo.
    """
    imgs = [Path(p) for p in snapshot_paths]
    imgs = [p for p in imgs if p.exists()][:3]
    if not imgs:
        return None
    content = [{"type": "text", "text": _PROMPT.format(evento=event_type)}]
    for p in imgs:
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{_encode_image(p)}"},
        })
    payload = {
        "model": VLM_MODEL,
        "messages": [{"role": "user", "content": content}],
        "temperature": 0.2,
        "max_tokens": 2000,  # modelo de raciocínio: o reasoning consome boa parte
    }
    headers = {"Authorization": f"Bearer {VLM_KEY}"} if VLM_KEY else {}
    try:
        resp = httpx.post(f"{VLM_BASE}/chat/completions", json=payload,
                          headers=headers, timeout=timeout)
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"]
    except Exception as err:  # pylint: disable=broad-exception-caught
        logger.warning("VLM indisponível (%s)", err)
        return None

    # tolera cercas de markdown
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        logger.warning("VLM respondeu sem JSON: %.200s", text)
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError as err:
        logger.warning("JSON do VLM inválido (%s): %.200s", err, text)
        return None
    risk = data.get("risk_level", "baixo")
    if risk not in ("baixo", "medio", "alto"):
        risk = "baixo"
    return VlmResult(
        descricao=str(data.get("descricao", ""))[:500],
        risk_level=risk,
        suspeito=bool(data.get("suspeito", False)),
        motivo=str(data.get("motivo", ""))[:200],
    )
