# Tasks

## 1. Configuração e modelo de tempo

- [x] 1.1 Adicionar em `settings.py` os limites `MAX_FRAME_AGE_SECONDS`, `ALERT_MAX_LATENCY_SECONDS`, `ANALYSIS_QUEUE_MAX`, `ANALYSIS_MAX_AGE_SECONDS`, `ANALYSIS_DEDUP_SECONDS`, `ANALYSIS_MIN_AREA_PX` e `ANALYSIS_MIN_CONF`, com padrões seguros e validação de valores inválidos; refletir em `.env.example` (verificação: teste que carrega o módulo sem `.env` e confere os padrões).
- [x] 1.2 Estender `Frame` em `capture/decoder.py` com `arrival_mono` e `arrival_wall`, preenchidos no momento da decodificação (verificação: teste que decodifica um chunk e confere que ambos avançam e que `arrival_wall` acompanha o relógio).
- [x] 1.3 Adicionar as colunas `observed_at` e `detect_latency_ms` em `events` via migração aditiva idempotente em `db/store.py`, aceitando banco existente (verificação: teste de migração em cópia do banco e leitura de um evento antigo sem erro).
- [x] 1.4 Fazer `EventEngine.step` e `_emit` usarem o instante de observação recebido do pipeline, gravando `observed_at` e `detect_latency_ms` (verificação: teste que passa um instante de observação antigo e confere os campos do evento).

## 2. Pipeline de frame recente

- [x] 2.1 Separar leitura/decodificação e processamento no `pipeline.py`: leitor mantém um slot com o frame mais recente (imagem, gray, movimento, instantes) e o processador amostra a `DETECT_FPS`, rodando o YOLO via `asyncio.to_thread` (verificação: teste com stream sintético lento confirma que o processador consome sempre o último frame, não a fila).
- [x] 2.2 Descartar frames com idade acima de `MAX_FRAME_AGE_SECONDS` antes da inferência, contabilizando descartes e idade máxima no log periódico (verificação: teste com frames envelhecidos confirma descarte, contador e ausência de evento a partir do frame velho).
- [x] 2.3 Manter o gatilho de movimento no leitor, comparando frames consecutivos recebidos, sem bloquear a leitura durante a inferência (verificação: teste que injeta rajada de frames e confere que a leitura continua durante uma inferência lenta simulada).
- [x] 2.4 Ajustar o cálculo de velocidade/rastreamento para gaps entre inferências, limitando o `dt` usado e descartando tracks após gap longo (verificação: teste de `TrackState.update` com gap grande não produz pico de velocidade).

## 3. Alerta independente e com horário verdadeiro

- [x] 3.1 Incluir no alerta o horário de observação do frame e a latência calculada, sinalizando quando exceder `ALERT_MAX_LATENCY_SECONDS`, sem apresentar o horário de processamento como o da cena (verificação: teste de `_send_alert_now` com relógio controlado confere o texto e a latência).
- [x] 3.2 Mover as chamadas de análise para um `ThreadPoolExecutor(max_workers=1)` dedicado, mantendo `send_photo` e identidade no executor padrão (verificação: teste com análise lenta simulada confirma que o alerta é entregue antes e sem esperar a análise).
- [x] 3.3 Registrar no watchdog de 60 s frames, processados, descartes, idade máxima, profundidade das filas e latência do último alerta (verificação: teste unitário do formatador do log e confirmação da linha em execução curta).

## 4. Análise seletiva e resiliente

- [x] 4.1 Calcular a evidência de objeto no `_emit` (classe, área, confiança, track) e só enfileirar análise quando houver pessoa, animal ou veículo acima de `ANALYSIS_MIN_AREA_PX`/`ANALYSIS_MIN_CONF`, mantendo o gate de cena apenas como filtro adicional (verificação: testes de vegetação/vento sem objeto não enfileiram, e pessoa/animal acima do mínimo enfileiram).
- [x] 4.2 Limitar a fila de análise por `ANALYSIS_QUEUE_MAX`, descartar itens acima de `ANALYSIS_MAX_AGE_SECONDS` sem chamar o provedor e deduplicar `(track_id, event_type)` por `ANALYSIS_DEDUP_SECONDS` (verificação: testes de fila cheia, item obsoleto e dois eventos do mesmo track resultam em no máximo uma análise).
- [x] 4.3 Corrigir o contrato de caminhos de `describe_event`/`_encode_image` para aceitar `str | Path` e normalizar na entrada (verificação: testes com `str` existente, `str` inexistente e `Path`, sem `AttributeError`).
- [x] 4.4 Garantir que falha de análise (provedor, imagem, contrato) não derruba o worker nem impede identidade e alerta de retaguarda do mesmo evento (verificação: teste com `describe_event` levantando exceção confirma worker vivo e alerta enviado).

## 5. Integração, documentação e operação

- [x] 5.1 Atualizar `README.md` com os novos limites, o significado de `observed_at`/latência, o offset do OSD da câmera e o procedimento de diagnóstico de backlog (verificação: comandos documentados executam como escritos).
- [x] 5.2 Rodar a suíte completa com `.venv/bin/python -m pytest -q` e corrigir regressões (verificação: suíte verde, incluindo os testes novos).
- [x] 5.3 Executar `python main.py --detect-test --seconds 60` e conferir idade de frame baixa, nenhum crash de VLM e um evento com `observed_at` coerente (verificação: saída do comando e linhas do log).
- [x] 5.4 Fazer backup consistente do SQLite, reiniciar o serviço e acompanhar 10 min de log em produção (verificação: serviço ativo, migração aplicada, watchdog sem violação de latência e sem `vlm_worker travou`).

## Workflow follow-up

- Arquivar a mudança após a revisão e a validação em produção.
