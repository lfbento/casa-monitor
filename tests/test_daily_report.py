from __future__ import annotations

import report.daily_report as dr


def _log(n_routine=2000, with_alert=True):
    eventos = []
    for i in range(n_routine):
        eventos.append({"hora": "2026-11-02T10:00:00-03:00", "tipo": "passagem_veiculo",
                        "zona": "ZONA_RUA", "risco": "baixo", "descricao_vlm": "x" * 400,
                        "meta": "{}"})
    if with_alert:
        eventos.append({"hora": "2026-11-02T22:10:00-03:00", "tipo": "alerta_suspeito",
                        "zona": "ZONA_PORTAO", "risco": "alto", "descricao_vlm": "ALERTA_CRITICO",
                        "meta": "{}"})
        eventos.append({"hora": "2026-11-02T22:11:00-03:00", "tipo": "acesso_casa",
                        "zona": "LINHA_ACESSO_CASA", "risco": "baixo", "descricao_vlm": "ACESSO_CASA",
                        "meta": "{}"})
    return {
        "janela": {"de": "a", "ate": "b"},
        "contagem_por_tipo": {t: n_routine for t in
                              ("passagem_pessoa", "passagem_veiculo", "animal",
                               "embarque_desembarque", "aproximacao_portao",
                               "acesso_casa", "alerta_suspeito")},
        "eventos_por_faixa_horaria": {f: 0 for f in dr._FAIXAS},
        "eventos": eventos,
        "sessoes_casa": [],
        "ainda_dentro_agora": [],
    }


def test_projection_respects_budget_and_keeps_security_events():
    log = _log()
    projected = dr._project_log_for_llm(log, max_chars=5000)
    assert len(projected) <= 5000
    assert "ALERTA_CRITICO" in projected
    assert "ACESSO_CASA" in projected


def test_valid_llm_response_is_used():
    resp = {"content": "\n".join(dr._REQUIRED_SECTIONS), "finish_reason": "stop"}
    assert dr._is_valid_report(resp)


def test_truncated_llm_response_is_rejected():
    resp = {"content": "\n".join(dr._REQUIRED_SECTIONS[:1]), "finish_reason": "length"}
    assert not dr._is_valid_report(resp)


def test_missing_section_is_rejected():
    resp = {"content": "\n".join(dr._REQUIRED_SECTIONS[:3]), "finish_reason": "stop"}
    assert not dr._is_valid_report(resp)


def test_fallback_distinguishes_session_states():
    log = _log(n_routine=0, with_alert=False)
    log["sessoes_casa"] = [
        {"person_label_or_description": "a", "entry_time": "t", "exit_time": "t2",
         "duration_minutes": 5, "status": "COMPLETED"},
    ]
    log["ainda_dentro_agora"] = [
        {"person_label_or_description": "b", "entry_time": "t", "status": "INSIDE"},
        {"person_label_or_description": "c", "entry_time": "t", "status": "UNCERTAIN"},
        {"person_label_or_description": "d", "entry_time": "t", "status": "STALE"},
    ]
    md = dr._fallback_report("2026-11-02", log)
    assert "dentro: b" in md
    assert "presença incerta: c" in md
    assert "sem confirmação de saída (antiga): d" in md


def test_generate_report_uses_fallback_when_llm_invalid(monkeypatch, tmp_path):
    import json
    from db.store import Store

    monkeypatch.setattr(dr, "REPORTS_DIR", tmp_path)
    monkeypatch.setattr(dr, "_llm", lambda msgs, timeout=120.0:
                        {"content": "parcial", "finish_reason": "length"})
    store = Store(tmp_path / "db.sqlite")
    store.add_event("alerta_suspeito", 1, "ZONA_PORTAO",
                    ts_start="2026-11-02T22:10:00-03:00", meta="{}")
    out = dr.generate_report(store)
    text = out.read_text()
    assert "Resumo do período (consolidação)" in text
    assert all(s in text for s in dr._REQUIRED_SECTIONS)
    store.close()
