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

## Latência, frescor da captura e análise

O pipeline separa **leitor** e **processador**: o leitor drena o WebSocket e mantém apenas o frame mais recente; o processador amostra em `DETECT_FPS` e descarta frame com idade acima de `MAX_FRAME_AGE_SECONDS`. Eventos e alertas usam o instante de observação do frame (`observed_at`), não o instante de processamento.

- `observed_at` / `detect_latency_ms` — instante de **chegada** do frame e quanto tempo passou até o evento ser gravado. Isso mede processamento, não o atraso da fonte.
- `captured_at` / `capture_age_ms` — instante **gravado no OSD** da imagem e a idade do conteúdo na emissão. É a medida honesta de frescor: um frame pode ter `detect_latency_ms` de 50 ms e `capture_age_ms` de 5 h.
- `CAPTURE_AGE_WARN_SECONDS` / `CAPTURE_AGE_FAIL_SECONDS` — aviso e violação. Acima do limite de falha o alerta ganha `⚠️ Conteúdo atrasado`, o relatório mostra o frescor da janela e o watchdog registra `frescura violada` em `ERROR`.
- `ALERT_MAX_LATENCY_SECONDS` — acima disso o alerta marca atraso de **processamento** e o log registra a violação.
- A análise semântica só entra na fila com evidência de pessoa, animal ou veículo acima de `ANALYSIS_MIN_AREA_PX`/`ANALYSIS_MIN_CONF`; variação de cena (vento, chuva, vegetação) não gera análise.
- A fila de análise é limitada (`ANALYSIS_QUEUE_MAX`), descarta itens com mais de `ANALYSIS_MAX_AGE_SECONDS` e nunca atrasa o alerta imediato.

Diagnóstico no log (a cada 60 s):

```text
FILA: análise=0 | prioritária=0 | worker vivo=True | frames=... processados=...
descartados_idade=... substituídos=... idade_max=...ms | frescor=3s (máx 12s) ocr_falhas=0
| sem_movimento=... sanidade=... | cadência=9.8fps (cfg 10) | ciclo ms p50/p95:
espera=... infer=... step=... | leitura=...ms/frame | análise descartada=...
deduplicada=... | latência_alerta=...ms
```

- `substituídos` conta frames que chegaram enquanto a inferência anterior rodava; é esperado que seja alto quando a inferência é mais lenta que o stream.
- `idade_max` mede decode → processamento. Se `descartados_idade` crescer e `idade_max` ficar perto de `MAX_FRAME_AGE_SECONDS`, reduza `DETECT_FPS` ou aumente `MAX_FRAME_AGE_SECONDS` — aumentar troca frescor por completude.
- `cadência` é a taxa efetiva de frames processados na última janela de 60 s, comparada a `DETECT_FPS` (alerta em `⚠️` quando cai abaixo de `DETECT_MIN_EFFECTIVE_RATIO` e houve movimento).
- `sem_movimento`/`sanidade` contam frames pulados pelo gate e as detecções periódicas de sanidade (`MOTION_SANITY_INTERVAL_SECONDS`).
- `frescor=desconhecida` + `ocr_falhas` crescente significa que o OSD não foi lido; a idade de captura nunca é inventada a partir de `arrival_wall`.

Para conferir o atraso real de um alerta, compare `Capturado:` com `Recebido:` na mensagem.

### OSD da câmera e atraso do stream (Miloco)

O OSD da CW700S é o único relógio de captura em banda; o pipeline o lê por correlação de templates (`capture/osd.py`, geometria de referência 2560×1440 escalada para a resolução recebida). O horário oficial do sistema é o do host: corrija o relógio da câmera no app Mi Home se quiser que a imagem e o horário local concordem — mas isso não muda o atraso do stream.

**Causa raiz do atraso de horas:** o patch local do Miloco (`~/.local/opt/miloco-fix`) escreve HEVC no `stdin` do ffmpeg sem `drain` nem teto. Quando o encoder não acompanha a câmera, o processo Python acumula o excedente na RAM (5,1 GB observados ≈ 5 h de conteúdo a 25 fps) e todo cliente do WS recebe esse passado. O patch agora limita o buffer (`H264_MAX_BUFFER_MB`, padrão 2) descartando até o próximo keyframe, e a saída foi reduzida para 1920 px (`H264_WIDTH`).

**Procedimento de recuperação imediata** (zera o atraso):

```bash
# 1. garanta que nenhum cliente (aba do Miloco) esteja no stream
# 2. pare o monitor; o Miloco encerra o transcode quando a última conexão cai
systemctl --user stop casa-monitor
sleep 15
docker top miloco -o pid,rss,cmd | grep -c ffmpeg   # esperado: 0
docker stats --no-stream --format '{{.MemUsage}}' miloco  # RSS deve cair (~200 MB)
# 3. religue e confira a idade de captura no log (frescor ~segundos)
systemctl --user start casa-monitor
```

Se `ffmpeg` continuar vivo, outro cliente mantém o stream — feche a aba do Miloco e repita. Se a idade continuar alta depois do teardown, o backlog está na instância da câmera no Miloco (ele sobrevive ao restart do raw stream): reinicie o container para recriar a sessão P2P.

```bash
docker restart miloco   # recria a instância da câmera e zera o backlog residual
```

O ciclo de resync automático (`CAPTURE_AGE_RESYNC_SECONDS`, espera `CAPTURE_AGE_RESYNC_WAIT_SECONDS`) faz o teardown/reconexão sozinho; quando o backlog é da instância da câmera ele registra `resync bloqueado` — nesse caso, o restart do container é a intervenção manual (o serviço não executa docker por conta própria).

**Checagem rotineira do buffer:**

```bash
docker top miloco -o pid,rss,cmd | grep -E 'python3 start_server|ffmpeg'
```

O processo Python deve ficar na casa das centenas de MB; crescimento monotônico para GB indica backlog voltando (verifique `H264_MAX_BUFFER_MB` e a carga de CPU).

### Agregação de passagens

`passagem_pessoa`, `passagem_veiculo` e `animal` passam por duas camadas: confirmação mínima (`TRACK_MIN_HITS`/`TRACK_MIN_LIFETIME_S`) e agregação por episódio (`PASSAGE_MERGE_SECONDS`, `PASSAGE_MERGE_DISTANCE_PX`). Requer também deslocamento real (`PASSAGE_MIN_PATH_PX`) ou transição de zona — objeto estático não vira passagem. Fragmentos da mesma passagem geram **um** evento, com `meta.merged_tracks`. Alertas de portão/acesso não passam por essa regra.

### Gate de movimento

Sem movimento acima do limiar o frame não vai ao detector, exceto pela verificação de sanidade a cada `MOTION_SANITY_INTERVAL_SECONDS` (confirma que a máscara continua operante).

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
