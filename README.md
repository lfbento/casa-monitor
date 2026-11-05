# casa-monitor

Monitoramento inteligente por visão computacional para a câmera externa (Xiaomi CW700S), com relatório diário de segurança às 22h00 entregue no Telegram.

## O que faz

- **Captura** o stream H.264 da câmera via WebSocket do [Miloco](https://github.com/XiaoMi/xiaomi-miloco) (sem alterar a stack de captura)
- **Camada 1**: YOLO11n + ByteTrack (GPU) continuamente, com amostragem configurável de FPS
- **Camada 2**: máquina de estados por evento — passagens (pessoa/veículo/animal), aproximação do portão, acesso à casa (entrada/saída + tempo de permanência), embarque/desembarque em veículos parados, loitering e horário de risco (00h–05h30)
- **VLM**: 1–3 keyframes por evento → descrição de pessoa/veículo/postura + `risk_level` (OpenAI-compatible — MiMo)
- **Persistência**: SQLite (`data/monitoring.db`) + snapshots com rotação automática (7 dias)
- **Relatório diário 22h00**: consolidação SQL → LLM sintetiza Markdown (alertas, portão/permanência, veículos, animais) → envia no Telegram (`notifier.py` — também WhatsApp/Evolução e e-mail)

## Operação

```bash
# daemon 24/7 (systemd user service, sobrevive a reboot)
systemctl --user status|restart casa-monitor
journalctl --user -u casa-monitor -f

# testes manuais
python main.py --capture-test            # captura real
python main.py --detect-test --seconds 60 # YOLO + eventos
python main.py --generate-report-now      # gera e envia o relatório
python main.py --generate-report-now --no-send  # só gera
```

## Configuração

Copie `.env.example` para `.env` e preencha (o `.env` **nunca** é versionado):

- `MILOCO_LOGIN_CODE` — PIN de 6 dígitos do Miloco (o login envia `MD5(PIN)`)
- `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` — entrega do relatório
- `VLM_API_KEY` / `LLM_API_KEY` — provedor OpenAI-compatible (VLM + síntese do relatório)

## Latência, frescor do frame e análise

O pipeline separa **leitor** e **processador**: o leitor drena o WebSocket e mantém apenas o frame mais recente; o processador amostra em `DETECT_FPS` e descarta frame com idade acima de `MAX_FRAME_AGE_SECONDS`. Eventos e alertas usam o instante de observação do frame (`observed_at`), não o instante de processamento.

- `observed_at` / `detect_latency_ms` — quando o frame foi observado e quanto tempo passou até o evento ser gravado.
- `ALERT_MAX_LATENCY_SECONDS` — acima disso o alerta inclui `⚠️ Atraso` e o log registra a violação.
- A análise semântica só entra na fila com evidência de pessoa, animal ou veículo acima de `ANALYSIS_MIN_AREA_PX`/`ANALYSIS_MIN_CONF`; variação de cena (vento, chuva, vegetação) não gera análise.
- A fila de análise é limitada (`ANALYSIS_QUEUE_MAX`), descarta itens com mais de `ANALYSIS_MAX_AGE_SECONDS` e nunca atrasa o alerta imediato.

Diagnóstico no log (a cada 60 s):

```text
FILA: análise=0 | prioritária=0 | worker vivo=True | frames=... processados=...
descartados_idade=... substituídos=... idade_max=...ms | análise descartada=...
deduplicada=... | latência_alerta=...ms
```

`substituídos` conta frames que chegaram enquanto a inferência anterior rodava; é esperado que seja alto quando a inferência é mais lenta que o stream. Se `descartados_idade` crescer e `idade_max` ficar perto de `MAX_FRAME_AGE_SECONDS`, reduza `DETECT_FPS` ou aumente `MAX_FRAME_AGE_SECONDS` — aumentar troca frescor por completude. Para conferir o atraso real de um alerta, compare o horário `Observado:` da mensagem com o horário de recebimento no Telegram.

### OSD da câmera

O OSD da CW700S está cerca de 1 h atrás do host (host sincronizado por NTP). O horário oficial do sistema é o do host; corrija o relógio da câmera no app Mi Home se quiser que a imagem e o alerta concordem.

## Calibração das zonas

```bash
python zones/calibrate_zones.py --grid    # frame com grade de coordenadas
python zones/calibrate_zones.py --preview # desenha as zonas de config/zones.yaml
python zones/calibrate_zones.py --click   # define polígonos clicando (precisa de display)
```

Zonas em `config/zones.yaml`: `ZONA_RUA`, `ZONA_PORTAO`, `ZONA_INTERNA`, `LINHA_ACESSO_CASA` (linha virtual + `house_side` — o lado de dentro da casa define ENTRY/EXIT).

## Migração do banco (SQLite)

A migração é aditiva e idempotente: roda automaticamente no startup e apenas adiciona colunas ausentes. Registros existentes são preservados.

```bash
# 1. Backup consistente (inclui estado do WAL)
systemctl --user stop casa-monitor
.venv/bin/python -c "from db.store import DB_PATH; import sqlite3, shutil; src=sqlite3.connect(DB_PATH); dst=sqlite3.connect('data/monitoring-backup.db'); src.backup(dst); dst.close(); src.close()"
```

Para rollback completo: parar o serviço, restaurar `data/monitoring-backup.db` por cima de `data/monitoring.db`, apagar `data/monitoring.db-wal`/`-shm` e retornar à versão anterior do código. As colunas novas não exigem remoção reversa.

Assinaturas de aparência antigas foram gravadas a partir do frame completo (`signature_source='full_frame'`) e passam a ser ignoradas pelo matcher até que a pessoa seja recadastrada a partir de um evento com recorte válido.

## Identidade de pessoas

A identidade usa um **histograma de aparência** (roupa/cor) sobre o recorte da pessoa — **não é reconhecimento facial**. O nome só aparece quando há evidência suficiente:

- `MATCH_THRESHOLD=0.95` — similaridade mínima (cosseno) entre o recorte e a pessoa cadastrada.
- `MATCH_MARGIN=0.05` — separação mínima sobre a segunda melhor correspondência.

Abaixo do limiar o sistema informa **pessoa desconhecida**; sem margem suficiente, **pessoa incerta (correspondência ambígua)**. Ambos são preferíveis a atribuir um nome errado.

## Presença na casa

Cada entrada abre uma sessão; a saída encerra apenas a sessão compatível pela aparência (nunca por ordem de chegada). Estados de sessão:

- `INSIDE` — presença confirmada, sem saída registrada.
- `UNCERTAIN` — presença aberta por evidência fraca (ex.: track entrou sem cruzar a linha).
- `STALE` — sem confirmação por mais de `HOUSE_SESSION_STALE_HOURS=24`; não é apresentada como presença confirmada.
- `COMPLETED` — saída registrada.

## Relatório diário

O relatório das 22h consolida os dados por SQL e envia ao LLM uma projeção limitada por `REPORT_LLM_MAX_CHARS` (passagens rotineiras agregadas; alertas, acessos e sessões preservados). Se a resposta do LLM estiver truncada, incompleta ou indisponível, o sistema usa um **fallback determinístico** com as quatro seções obrigatórias.

Saída da CLI `--generate-report-now`:

- `0` — relatório gerado (e enviado, se solicitado e o canal confirmou).
- `1` — falha na geração ou no envio solicitado.

## Arquitetura

```
[Miloco WS H.264] → capture/ → detect/ (YOLO+ByteTrack) → events/ (máquina de estados)
                                                              │
                    report/ (22h, APScheduler) ← db/ (SQLite) ←┤ → vlm/ (descrição por evento)
                              │                               │
                              └→ notifier.py (Telegram)    snapshots/ (7 dias)
```

Requisitos: Python 3.12, GPU NVIDIA (CUDA) recomendada — fallback CPU automático.
