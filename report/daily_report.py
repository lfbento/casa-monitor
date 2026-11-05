"""Gerador do Relatório Diário de Inteligência e Segurança Residencial (22h00).

Fluxo: janela 22h→22h → consolidação SQL exata → log estruturado → LLM
sintetiza Markdown com as 4 seções obrigatórias → reports/relatorio_YYYY-MM-DD.md
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import TypedDict

import httpx

from db.store import Store
from settings import REPORTS_DIR

logger = logging.getLogger(__name__)

LLM_BASE = os.getenv("LLM_BASE_URL", "http://localhost:8421/v1").rstrip("/")
LLM_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "mimo-v2.6-pro")
REPORT_LLM_MAX_CHARS = int(os.getenv("REPORT_LLM_MAX_CHARS", "80000"))

_REQUIRED_SECTIONS = (
    "## 🚨 1. Alertas de Segurança e Comportamentos Atípicos",
    "## 🏠 2. Controle de Portão e Permanência na Casa",
    "## 🚗 3. Veículos e Embarque/Desembarque",
    "## 🐾 4. Animais e Movimentação Geral da Rua",
)
_ROUTINE_TYPES = ("passagem_pessoa", "passagem_veiculo", "animal")
_PRIORITY_TYPES = ("alerta_suspeito", "acesso_casa")
_SECONDARY_TYPES = ("aproximacao_portao", "embarque_desembarque")
_SECONDARY_EVENT_LIMIT = 200
_DESCRIPTION_LIMIT = 500
_REDUCED_DESCRIPTION_LIMIT = 160
_FAIXAS = ("manhã (06-12)", "tarde (12-18)", "noite (18-24)", "madrugada (00-06)")


class LLMResponse(TypedDict):
    content: str
    finish_reason: str | None


_PROMPT = """Você é um analista de segurança residencial. Com base no log estruturado de eventos \
de uma câmera (frente à porta/portão da casa, com visão para a rua) das últimas 24h, \
gere um RELATÓRIO DIÁRIO em Markdown, em português claro e direto, com EXATAMENTE estas seções:

# 📋 Relatório Diário de Segurança — {data}
## 🚨 1. Alertas de Segurança e Comportamentos Atípicos
(destaque imediato para pessoa em atitude suspeita, parada prolongada no portão ou \
movimentação em horário incomum — horários exatos e descrição visual. Se não houver, escreva "Nenhum alerta no período.")
## 🏠 2. Controle de Portão e Permanência na Casa
(quem chegou perto do portão: horário, descrição, tempo no portão. Quem entrou/saiu: \
**Entrada**, **Saída**, **Tempo de Permanência (h:min)**. Liste quem ainda está dentro às 22h.)
## 🚗 3. Veículos e Embarque/Desembarque
(carros/motos que pararam, quem entrou/saiu de cada veículo, horários; contagem de veículos \
que só passaram, dividida por faixa de horário.)
## 🐾 4. Animais e Movimentação Geral da Rua
(animais avistados com horários; resumo do fluxo de pedestres.)

Regras: use apenas dados do log; horários no formato HH:MM; quando houver descrição visual \
(vlm_description) use-a; seja objetivo; não invente eventos.

LOG ESTRUTURADO:
{log}"""


def _llm(messages: list[dict], timeout: float = 120.0) -> LLMResponse | None:
    headers = {"Authorization": f"Bearer {LLM_KEY}"} if LLM_KEY else {}
    try:
        resp = httpx.post(f"{LLM_BASE}/chat/completions",
                          json={"model": LLM_MODEL, "messages": messages,
                                "temperature": 0.3, "max_tokens": 4096},
                          headers=headers, timeout=timeout)
        resp.raise_for_status()
        choice = resp.json()["choices"][0]
        return {
            "content": choice["message"]["content"],
            "finish_reason": choice.get("finish_reason"),
        }
    except Exception as err:  # pylint: disable=broad-exception-caught
        logger.error("LLM indisponível: %s", err)
        return None


def _faixa_horaria(timestamp: str) -> str | None:
    try:
        hour = datetime.fromisoformat(timestamp).hour
    except (ValueError, TypeError):
        return None
    if 6 <= hour < 12:
        return _FAIXAS[0]
    if 12 <= hour < 18:
        return _FAIXAS[1]
    if 18 <= hour < 24:
        return _FAIXAS[2]
    return _FAIXAS[3]


def _truncate_description(value: str | None, limit: int) -> str | None:
    if value is None or len(value) <= limit:
        return value
    return value[:max(0, limit - 1)] + "…"


def _project_event(event: dict, description_limit: int) -> dict:
    projected = dict(event)
    projected["descricao_vlm"] = _truncate_description(
        event.get("descricao_vlm"), description_limit)
    if isinstance(event.get("meta"), str):
        projected["meta"] = _truncate_description(event["meta"], description_limit)
    return projected


def _serialize_projection(projection: dict) -> str:
    return json.dumps(projection, ensure_ascii=False, separators=(",", ":"))


def _project_log_for_llm(log: dict, max_chars: int = REPORT_LLM_MAX_CHARS) -> str:
    """Projeta o log sem alterar as contagens nem os dados usados pelo fallback."""
    routine = {
        event_type: {faixa: 0 for faixa in _FAIXAS}
        for event_type in _ROUTINE_TYPES
    }
    prioritized: list[dict] = []
    secondary: list[dict] = []
    for event in log["eventos"]:
        event_type = event["tipo"]
        if event_type in _ROUTINE_TYPES:
            faixa = _faixa_horaria(event["hora"])
            if faixa is not None:
                routine[event_type][faixa] += 1
        elif event_type in _PRIORITY_TYPES:
            prioritized.append(event)
        elif event_type in _SECONDARY_TYPES:
            secondary.append(event)
        else:
            secondary.append(event)

    base = {
        "janela": log["janela"],
        "contagem_por_tipo": log["contagem_por_tipo"],
        "eventos_por_faixa_horaria": log["eventos_por_faixa_horaria"],
        "passagens_rotineiras_agregadas": routine,
        "eventos": [],
        "eventos_secundarios_omitidos": max(0, len(secondary) - _SECONDARY_EVENT_LIMIT),
        "eventos_prioritarios_omitidos": 0,
        "sessoes_casa": log["sessoes_casa"],
        "ainda_dentro_agora": log["ainda_dentro_agora"],
    }

    for description_limit, include_secondary in (
        (_DESCRIPTION_LIMIT, True),
        (_REDUCED_DESCRIPTION_LIMIT, False),
    ):
        events = prioritized + (secondary[:_SECONDARY_EVENT_LIMIT] if include_secondary else [])
        projection = dict(base)
        projection["eventos_secundarios_omitidos"] = (
            len(secondary) - len(secondary[:_SECONDARY_EVENT_LIMIT])
            if include_secondary else len(secondary)
        )
        projection["eventos"] = [
            _project_event(event, description_limit) for event in events
        ]
        serialized = _serialize_projection(projection)
        if len(serialized) <= max_chars:
            return serialized

    # Em volumes excepcionais, descarta primeiro os eventos de menor prioridade.
    projection["eventos"] = [
        _project_event(event, _REDUCED_DESCRIPTION_LIMIT) for event in prioritized
    ]
    while projection["eventos"]:
        serialized = _serialize_projection(projection)
        if len(serialized) <= max_chars:
            return serialized
        projection["eventos"].pop()
        projection["eventos_prioritarios_omitidos"] += 1

    for key in ("ainda_dentro_agora", "sessoes_casa"):
        while projection[key]:
            serialized = _serialize_projection(projection)
            if len(serialized) <= max_chars:
                return serialized
            projection[key] = projection[key][:-1]

    serialized = _serialize_projection(projection)
    return serialized if len(serialized) <= max_chars else "{}"


def build_structured_log(store: Store, ts_from: str, ts_to: str) -> dict:
    """Consolidação SQL exata — o que a LLM recebe (nada de adivinhação)."""
    events = store.events_between(ts_from, ts_to)
    sessions = store.sessions_between(ts_from, ts_to)
    open_now = store.open_sessions(ts_to)

    def count(tipo: str) -> int:
        return sum(1 for e in events if e["event_type"] == tipo)

    por_faixa = {faixa: 0 for faixa in _FAIXAS}
    for event in events:
        faixa = _faixa_horaria(event["timestamp_start"])
        if faixa is not None:
            por_faixa[faixa] += 1

    return {
        "janela": {"de": ts_from, "ate": ts_to},
        "contagem_por_tipo": {
            "passagem_pessoa": count("passagem_pessoa"),
            "passagem_veiculo": count("passagem_veiculo"),
            "animal": count("animal"),
            "embarque_desembarque": count("embarque_desembarque"),
            "aproximacao_portao": count("aproximacao_portao"),
            "acesso_casa": count("acesso_casa"),
            "alerta_suspeito": count("alerta_suspeito"),
        },
        "eventos_por_faixa_horaria": por_faixa,
        "eventos": [
            {
                "hora": e["timestamp_start"],
                "tipo": e["event_type"],
                "zona": e["zone"],
                "risco": e["risk_level"],
                "descricao_vlm": e["vlm_description"],
                "meta": e["meta"],
            }
            for e in events
        ],
        "sessoes_casa": sessions,
        "ainda_dentro_agora": open_now,
    }


def _status_label(status: str | None) -> str:
    return {
        "INSIDE": "dentro",
        "UNCERTAIN": "presença incerta",
        "STALE": "sem confirmação de saída (antiga)",
        "COMPLETED": "concluída",
    }.get(status or "", status or "—")


def _render_totals(log: dict) -> str:
    """Totais oficiais derivados da consolidação — independentes do LLM."""
    c = log["contagem_por_tipo"]
    faixa = log["eventos_por_faixa_horaria"]
    return (
        "\n**Resumo do período (consolidação):** "
        f"alertas={c['alerta_suspeito']}, acessos={c['acesso_casa']}, "
        f"portão={c['aproximacao_portao']}, embarques={c['embarque_desembarque']}, "
        f"veículos={c['passagem_veiculo']}, pessoas={c['passagem_pessoa']}, animais={c['animal']}. "
        f"Por faixa: {faixa}.\n"
    )


def _inject_totals(md: str, log: dict) -> str:
    lines = md.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("# "):
            lines.insert(i + 1, _render_totals(log))
            break
    else:
        lines.insert(0, _render_totals(log))
    return "\n".join(lines)


def _is_valid_report(resp: LLMResponse | None) -> bool:
    if not resp:
        return False
    if resp.get("finish_reason") == "length":
        return False
    content = resp.get("content") or ""
    pos = -1
    for section in _REQUIRED_SECTIONS:
        idx = content.find(section, pos + 1)
        if idx == -1:
            return False
        pos = idx
    return True


def generate_report(store: Store, now: datetime | None = None) -> Path:
    now = now or datetime.now().astimezone()
    # janela: 22h de ontem até 22h de hoje (às 22h exatas = últimas 24h)
    ts_to = now.isoformat(timespec="seconds")
    ts_from = (now - timedelta(hours=24)).isoformat(timespec="seconds")
    log = build_structured_log(store, ts_from, ts_to)
    data = now.strftime("%Y-%m-%d")

    projection = _project_log_for_llm(log)
    llm_resp = _llm([{"role": "user", "content": _PROMPT.format(data=data, log=projection)}])

    md = None
    if _is_valid_report(llm_resp):
        md = re.sub(r"^```(markdown|md)?\s*|\s*```$", "", llm_resp["content"].strip(), flags=re.M)
    if md is None:
        # fallback: relatório estruturado sem LLM (nunca deixa de entregar)
        logger.warning("resposta do LLM inválida/truncada — usando fallback determinístico")
        md = _fallback_report(data, log)

    md = _inject_totals(md, log)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORTS_DIR / f"relatorio_{data}.md"
    out.write_text(md, encoding="utf-8")
    logger.info("Relatório gerado: %s", out)
    return out


def _fallback_report(data: str, log: dict) -> str:
    c = log["contagem_por_tipo"]
    linhas = [f"# 📋 Relatório Diário de Segurança — {data}",
              "", "*(gerado sem LLM — resumo estruturado)*", "",
              "## 🚨 1. Alertas de Segurança e Comportamentos Atípicos"]
    alertas = [e for e in log["eventos"] if e["tipo"] == "alerta_suspeito"]
    linhas += [f"- {e['hora']} — {e['descricao_vlm'] or e['meta']}" for e in alertas] or ["- Nenhum alerta no período."]
    linhas += ["", "## 🏠 2. Controle de Portão e Permanência na Casa"]
    for s in log["sessoes_casa"]:
        linhas.append(f"- {s['person_label_or_description']}: entrada {s['entry_time']}, "
                      f"saída {s['exit_time'] or '—'}, permanência {s['duration_minutes'] or '—'} min ({_status_label(s['status'])})")
    for s in log["ainda_dentro_agora"]:
        linhas.append(f"- ⚠️ {_status_label(s['status'])}: {s['person_label_or_description']} (desde {s['entry_time']})")
    linhas += ["", "## 🚗 3. Veículos e Embarque/Desembarque",
               f"- Passagens de veículos: {c['passagem_veiculo']} | Paradas c/ interação: {c['embarque_desembarque']}",
               f"- Por faixa: {log['eventos_por_faixa_horaria']}",
               "", "## 🐾 4. Animais e Movimentação Geral da Rua",
               f"- Animais: {c['animal']} | Pessoas que só passaram: {c['passagem_pessoa']}"]
    return "\n".join(linhas)
