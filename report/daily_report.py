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

import httpx

from db.store import Store
from settings import REPORTS_DIR

logger = logging.getLogger(__name__)

LLM_BASE = os.getenv("LLM_BASE_URL", "http://localhost:8421/v1").rstrip("/")
LLM_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "mimo-v2.6-pro")

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


def _llm(messages: list[dict], timeout: float = 120.0) -> str | None:
    headers = {"Authorization": f"Bearer {LLM_KEY}"} if LLM_KEY else {}
    try:
        resp = httpx.post(f"{LLM_BASE}/chat/completions",
                          json={"model": LLM_MODEL, "messages": messages,
                                "temperature": 0.3, "max_tokens": 4096},
                          headers=headers, timeout=timeout)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except Exception as err:  # pylint: disable=broad-exception-caught
        logger.error("LLM indisponível: %s", err)
        return None


def build_structured_log(store: Store, ts_from: str, ts_to: str) -> dict:
    """Consolidação SQL exata — o que a LLM recebe (nada de adivinhação)."""
    events = store.events_between(ts_from, ts_to)
    sessions = store.sessions_between(ts_from, ts_to)
    open_now = store.open_sessions()

    def count(tipo: str) -> int:
        return sum(1 for e in events if e["event_type"] == tipo)

    por_faixa = {"manhã (06-12)": 0, "tarde (12-18)": 0, "noite (18-24)": 0, "madrugada (00-06)": 0}
    for e in events:
        try:
            h = datetime.fromisoformat(e["timestamp_start"]).hour
        except (ValueError, TypeError):
            continue
        if 6 <= h < 12:
            por_faixa["manhã (06-12)"] += 1
        elif 12 <= h < 18:
            por_faixa["tarde (12-18)"] += 1
        elif 18 <= h < 24:
            por_faixa["noite (18-24)"] += 1
        else:
            por_faixa["madrugada (00-06)"] += 1

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


def generate_report(store: Store, now: datetime | None = None) -> Path:
    now = now or datetime.now().astimezone()
    # janela: 22h de ontem até 22h de hoje (às 22h exatas = últimas 24h)
    ts_to = now.isoformat(timespec="seconds")
    ts_from = (now - timedelta(hours=24)).isoformat(timespec="seconds")
    log = build_structured_log(store, ts_from, ts_to)
    data = now.strftime("%Y-%m-%d")

    llm_text = _llm([{"role": "user", "content": _PROMPT.format(
        data=data, log=json.dumps(log, ensure_ascii=False, indent=1))}])

    if llm_text:
        md = llm_text.strip()
        md = re.sub(r"^```(markdown|md)?\s*|\s*```$", "", md, flags=re.M)
    else:
        # fallback: relatório estruturado sem LLM (nunca deixa de entregar)
        md = _fallback_report(data, log)

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
                      f"saída {s['exit_time'] or '—'}, permanência {s['duration_minutes'] or '—'} min ({s['status']})")
    for s in log["ainda_dentro_agora"]:
        linhas.append(f"- ⚠️ ainda dentro: {s['person_label_or_description']} (desde {s['entry_time']})")
    linhas += ["", "## 🚗 3. Veículos e Embarque/Desembarque",
               f"- Passagens de veículos: {c['passagem_veiculo']} | Paradas c/ interação: {c['embarque_desembarque']}",
               f"- Por faixa: {log['eventos_por_faixa_horaria']}",
               "", "## 🐾 4. Animais e Movimentação Geral da Rua",
               f"- Animais: {c['animal']} | Pessoas que só passaram: {c['passagem_pessoa']}"]
    return "\n".join(linhas)
