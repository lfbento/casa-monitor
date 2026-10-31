# Plano Técnico — Monitoramento Inteligente + Relatório Diário 22h00

> Status: **AGUARDANDO APROVAÇÃO** (2026-10-31) · Câmera: Xiaomi CW700S (portão + rua) · Fuso: America/Sao_Paulo

---

## 0. Diagnóstico do workspace (Fase 1) — CONCLUÍDO

### O que existe hoje
| Item | Situação |
|---|---|
| **Stack Miloco** (`~/.local/opt/miloco-fix`) | Captura da CW700S **funcional**: P2P Mi Home (região `sg`) → transcoder HEVC→H.264 (ffmpeg) → `WS /ws/video_stream`. Patch PT-BR + player H.264 no ar em `https://localhost:8010` |
| **API do Miloco** | `POST /login` (PIN 6 dígitos → JWT), `WS /ws/video_stream` (frames H.264 Annex-B), `/authorize` + `/login_status` (conta Mi Home) |
| **Projeto de captura standalone** | **Não existe** — nenhum script `.py`/`.sh` de captura no workspace. A "captura inicial funcional" é a própria stack do Miloco |
| **Hardware** | 8 threads, 30 GB RAM, **GTX 1080 8 GB com driver NVIDIA 580** (não é mais nouveau — o README do miloco-fix está desatualizado nesse ponto). CUDA liberado para YOLO |
| **Toolchain** | Python 3.14.7 + uv 0.13.5, Docker (`sg docker -c`) |

### Decisão de integração
A camada de captura **não será tocada**. O novo sistema será um **cliente headless do WebSocket do Miloco** (mesmo stream que o navegador consome): autentica com PIN, inicia o stream e decodifica o H.264 com PyAV. Se a câmera só aceitar 1 sessão P2P simultânea (risco), o fallback é um **frame tap** no `miot_service.py` (que já é nosso, montado por cima da imagem).

---

## 1. Arquitetura de módulos

```
                    ┌─────────────────────── casa-monitor/ ───────────────────────┐
[Miloco WS H.264] ─▶ capture/ws_client.py ─▶ capture/decoder.py (PyAV → ndarray)  │
                    │            │                                                │
                    │            ▼                                                │
                    │  detect/yolo_engine.py  ── YOLO11n + ByteTrack (GTX 1080)   │
                    │  detect/tracker.py      ── tracks persistentes (id, classe, │
                    │                            bbox, velocidade, trajetória)    │
                    │            │                                                │
                    │            ▼                                                │
                    │  zones/roi.py           ── polígonos em config/zones.yaml    │
                    │  zones/calibrate_zones.py ── 1 frame + grade de coordenadas  │
                    │            │                                                │
                    │            ▼                                                │
                    │  events/state_machine.py ── (Camada 2)                      │
                    │    • tripwire ENTRY/EXIT (vetor centroide × LINHA_ACESSO)   │
                    │    • veículo parado > Xs + interação pessoa↔veículo         │
                    │    • aproximação do portão (dwell)                          │
                    │    • loitering (> N s) · horário de risco (00h–05h30)       │
                    │    • sessões de casa (entry/exit correlacionados)           │
                    │            │                                                │
                    │            ▼                                                │
                    │  vlm/describe.py ── 1–3 keyframes → LLM multimodal          │
                    │    (descrição de pessoa/veículo + avaliação de risco)       │
                    │            │                                                │
                    │            ▼                                                │
                    │  db/store.py ── SQLite WAL (monitoring.db) + snapshots/     │
                    │            │          (rotação automática: 7 dias)          │
                    │            ▼                                                │
                    │  report/daily_report.py ── 22h00: SQL → LLM → Markdown      │
                    │  notifier.py ── Telegram / WhatsApp (Evolution) / E-mail    │
                    │  main.py ── daemon + CLI (--generate-report-now, --once)    │
                    └────────────────────────────────────────────────────────────┘
```

**Princípio das duas camadas (CPU/GPU não estoura):**
- **Camada 1 (leve, contínua):** YOLO roda em frames **amostrados** (8–12 FPS do stream de 25 FPS) em resolução 640px. ByteTrack mantém identidade entre detecções. Só essa camada roda em todo tempo real.
- **Camada 2 (eventos):** máquinas de estados por `track_id` — processa **dados** (bbox, velocidade, zona), não pixels. VLM só dispara quando um evento relevante nasce (com throttle de chamadas/hora).

---

## 2. Detalhamento das regras de negócio (Camada 2)

| Evento | Gatilho | Registro |
|---|---|---|
| `passagem_pessoa` / `passagem_veiculo` / `animal` | track entra e sai da `ZONA_RUA` sem tocar nas outras zonas | direção (esq/dir), horário, snapshot |
| `embarque_desembarque` | veículo com velocidade ≈ 0 por > `X` (padrão 20s) + `person` surge/merge no bbox do veículo (IoU/proximidade) | quem saiu/entrou, horário, snapshot |
| `aproximacao_portao` | track entra na `ZONA_PORTAO` e fica > `Y` s (padrão 8s) sem cruzar a linha | descrição VLM, tempo no portão |
| `acesso_casa` (ENTRY/EXIT) | centroide cruza `LINHA_ACESSO_CASA` com vetor de direção | ENTRY: timestamp + crop + embedding de aparência; EXIT: correlaciona com entradas abertas (similaridade visual + VLM) → `house_sessions` com `entry_time`, `exit_time`, `duration_minutes` |
| `alerta_suspeito` (loitering) | pessoa parada/oscilando na `ZONA_PORTAO` ou junto a veículo estacionado > `N` s (padrão 30s) sem entrar | risk_level `alto` se 00h00–05h30 |
| Análise VLM | nascimento de qualquer evento acima | 1–3 keyframes → descrição (roupa, objetos, carro: tipo/cor/modelo, postura suspeita) + `risk_level` |

**Correlação ENTRY→EXIT:** janelas abertas do dia + similaridade de aparência (embedding leve — histograma/cor + recorte normalizado; VLM como desempatador). Fallback por ordem cronológica (FIFO) quando a similaridade for ambígua; caso não correlacione até as 22h → sessão `INSIDE` no relatório.

---

## 3. Banco de dados (SQLite — `data/monitoring.db`, modo WAL)

```sql
CREATE TABLE events (
  id INTEGER PRIMARY KEY,
  timestamp_start TEXT NOT NULL,          -- ISO 8601 -03:00
  timestamp_end TEXT,
  event_type TEXT NOT NULL,               -- passagem_veiculo | passagem_pessoa | animal |
                                          -- embarque_desembarque | aproximacao_portao |
                                          -- acesso_casa | alerta_suspeito
  track_id INTEGER,
  zone TEXT,                              -- ZONA_RUA | ZONA_PORTAO | LINHA_ACESSO_CASA
  vlm_description TEXT,
  risk_level TEXT DEFAULT 'baixo',        -- baixo | medio | alto
  snapshot_path TEXT
);
CREATE TABLE house_sessions (
  id INTEGER PRIMARY KEY,
  person_label_or_description TEXT,
  entry_time TEXT NOT NULL,
  exit_time TEXT,
  duration_minutes INTEGER,
  status TEXT NOT NULL DEFAULT 'INSIDE',  -- INSIDE | COMPLETED
  entry_snapshot TEXT,
  exit_snapshot TEXT
);
CREATE INDEX idx_events_ts ON events(timestamp_start);
CREATE INDEX idx_sessions_entry ON house_sessions(entry_time);
```

- Snapshots em `data/snapshots/YYYY-MM-DD/evento_<id>_<track>.jpg`.
- **Rotação:** job diário apaga snapshots e eventos com > 7 dias (relatórios `.md` são mantidos).

---

## 4. Relatório diário (22h00 — APScheduler, `America/Sao_Paulo`)

1. Janela: 22h00 de ontem → 22h00 de hoje.
2. Consolidação SQL exata (contagens por faixa de horário, sessões com permanência, alertas).
3. Log estruturado → **LLM** sintetiza em Markdown (pt-BR) com as 4 seções obrigatórias:
   - 🚨 **1. Alertas de Segurança e Comportamentos Atípicos** (horários exatos + descrição visual)
   - 🏠 **2. Controle de Portão e Permanência na Casa** (entrada, saída, duração hh:mm; quem está dentro às 22h)
   - 🚗 **3. Veículos e Embarque/Desembarque** (paradas, interações, contagem de passagens por pico)
   - 🐾 **4. Animais e Movimentação Geral da Rua** (avistamentos + fluxo de pedestres)
4. Entrega: `reports/relatorio_YYYY-MM-DD.md` + `notifier.py` (canal configurável no `.env`).
5. CLI: `python main.py --generate-report-now` (gera e envia imediatamente, para teste).

---

## 5. Configuração (`.env` — você preenche as credenciais, nunca vão pro chat)

```ini
MILOCO_BASE_URL=https://localhost:8010
MILOCO_LOGIN_CODE=          # PIN de 6 dígitos (você preenche)
MILOCO_VERIFY_SSL=false     # certificado autoassinado

# Modelos
DETECT_MODEL=yolo11n.pt     # GPU (CUDA) · fallback CPU automático
DETECT_FPS=10               # amostragem dos 25 FPS do stream
VLM_PROVIDER=               # gemini | openai_compatible | ollama
VLM_API_KEY=
VLM_MODEL=
LLM_PROVIDER=               # síntese do relatório
LLM_API_KEY=
LLM_MODEL=

# Regras (ajustáveis)
LOITERING_SECONDS=30
VEHICLE_STOP_SECONDS=20
GATE_DWELL_SECONDS=8
NIGHT_WINDOW=00:00-05:30
SNAPSHOT_RETENTION_DAYS=7

# Entrega do relatório
NOTIFY_CHANNEL=             # telegram | whatsapp_evolution | email | none
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

---

## 6. Riscos e trade-offs

| Risco | Mitigação |
|---|---|
| Câmera aceita só 1 sessão P2P (monitoramento × app conflitam) | Testar E1; se conflitar, frame tap no `miot_service.py` (arquivo já é nosso) e o app passa a consumir do tap |
| Stream 24/7 sobrecarrega câmera/rede | É P2P na LAN, câmera com alimentação fixa; monitorar latência; fallback: sessões intercaladas |
| GTX 1080 8 GB (Pascal) | YOLO11n/11s tranquilo; VLM local fica apertado → VLM preferido via API (Gemini Flash barato) |
| Custo/limite de VLM | Throttle (máx N chamadas/hora), batch de keyframes, fallback descrição só com metadados de detecção |
| Direção de entrada/saída errada | `calibrate_zones.py` + teste com vídeos reais antes de ativar alertas |
| Falsos positivos de embarque/desembarque | Thresholds configuráveis + VLM como juiz nos eventos de veículo |

---

## 7. Etapas de implementação (ordem de execução)

| # | Etapa | Pronto quando… |
|---|---|---|
| E1 | Scaffold (`uv`, `.env`, módulos vazios) + **captura**: `ws_client` + `decoder` | `python main.py --capture-test` salva frames reais da CW700S em disco |
| E2 | `calibrate_zones.py` + `zones.yaml` | frame anotado com as 3 zonas aprovado por você (foto) |
| E3 | Camada 1: YOLO + ByteTrack + `tracks` | `--detect-test` mostra tracks com id estável por > 5 s |
| E4 | Camada 2: state machines + SQLite | eventos de teste (entrada/saída, parada de carro) aparecem em `monitoring.db` |
| E5 | VLM: keyframes → descrição + `risk_level` | evento gera `vlm_description` preenchida no banco |
| E6 | Relatório 22h + APScheduler + `notifier.py` + CLI | `--generate-report-now` entrega relatório Markdown no canal escolhido |
| E7 | End-to-end: rodar 24 h reais | relatório de 22h chega com dados verdadeiros e coerentes |
