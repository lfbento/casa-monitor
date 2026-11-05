# Tasks

## 1. Baseline e proteção de dados

- [ ] 1.1 Commitar a árvore atual (implementação das duas mudanças arquivadas + `openspec/` + `tests/`) como baseline e confirmar `git status --porcelain` vazio antes de qualquer mudança nova.
- [ ] 1.2 Fazer backup consistente do SQLite com o serviço parado, reabrir a cópia e conferir que as contagens de `events` e `house_sessions` coincidem com o original (verificação: script imprime contagens iguais).

## 2. Frescor da captura de ponta a ponta

- [ ] 2.1 Adicionar em `settings.PipelineLimits` os limites `CAPTURE_AGE_WARN_SECONDS`, `CAPTURE_AGE_FAIL_SECONDS`, `CAPTURE_AGE_OCR_INTERVAL_SECONDS`, `CAPTURE_AGE_RESYNC_SECONDS`, `CAPTURE_AGE_RESYNC_WAIT_SECONDS`, `DETECT_MIN_EFFECTIVE_RATIO` e `MOTION_SANITY_INTERVAL_SECONDS`, com padrões conservadores e validação pelos helpers existentes; refletir em `.env.example` e no log de início (verificação: teste carrega o módulo sem `.env` e confere os padrões e um valor inválido caindo no padrão).
- [ ] 2.2 Criar `capture/osd.py` com leitura do OSD por recorte da faixa fixa, segmentação por passo e correlação com templates, retornando `datetime | None` e um motivo de falha (verificação: teste com crops-fixture de snapshots reais lê três horários distintos e rejeita um crop adulterado).
- [ ] 2.3 Propagar `captured_at` e `capture_age_s` do `Frame` ao `FrameEnvelope`, calculados na leitura do OSD e atualizados na chegada (verificação: teste constrói um frame e confere que a idade acompanha o relógio injetado e que OSD ilegível resulta em `None`, não em zero).
- [ ] 2.4 Adicionar as colunas `captured_at TEXT` e `capture_age_ms INTEGER` em `events` pela migração aditiva existente, e aceitar os campos opcionais em `add_event` (verificação: teste de migração em cópia do banco existente e leitura de evento antigo sem erro).
- [ ] 2.5 Gravar `captured_at` e `capture_age_ms` no `_emit` e incluir frescor (última/máxima/desconhecidas) no watchdog de 60 s, com `logging.error` na violação (verificação: teste do formatador do watchdog e de um evento com idade de 5 h confere os campos persistidos).
- [ ] 2.6 Atualizar o texto de alerta para `Capturado: <captured_at> (<idade> atrás)`, separar a latência de processamento e marcar `⚠️ Conteúdo atrasado` acima do limite, mantendo a entrega quando a idade é desconhecida (verificação: teste com relógio controlado cobre idade de 5 h, idade dentro do limite e idade desconhecida).
- [ ] 2.7 Incluir no relatório diário a linha de frescor (mín/máx de `capture_age_ms` na janela) sem alterar a janela de consolidação (verificação: teste de relatório confere a linha e o fallback determinístico continua com as seções obrigatórias).

## 3. Backlog e recuperação para conteúdo recente

- [ ] 3.1 Documentar a causa raiz medida (processo Python do Miloco com 5,10 GB de buffer, `stdin` do ffmpeg sem `drain`/teto, saída em 2560 px sem acompanhar 25 fps) e o procedimento de recuperação imediata: fechar todos os clientes do stream, parar o `casa-monitor` por ≥10 s, confirmar a morte do ffmpeg e a queda do RSS, religar e medir a idade (verificação: procedimento executado uma vez, idade de captura volta ao normal e o comando `docker top miloco` confere o RSS).
- [ ] 3.2 Expor no decoder a taxa `Δpts/Δwall` e sinalizar reprodução fora de 1× sem depender do OSD (verificação: teste com sequência sintética de PTS em 1× e em 1,1× classifica corretamente).
- [ ] 3.3 Implementar o ciclo de resync no cliente: ao exceder `CAPTURE_AGE_RESYNC_SECONDS`, fechar o WS, aguardar `CAPTURE_AGE_RESYNC_WAIT_SECONDS`, reconectar, medir a idade antes/depois e registrar `recuperado` ou `bloqueado por outro cliente`; conteúdo do intervalo MUST NOT gerar evento (verificação: testes com cliente simulado cobrem teardown bem-sucedido, teardown impedido e ausência de eventos do intervalo).
- [ ] 3.4 Aplicar o patch do Miloco em `~/.local/opt/miloco-fix/deploy/patched/h264_transcoder.py`: teto de buffer com descarte até o próximo keyframe e largura de saída sustentável (1920 ou 1280), mediante aprovação e reinício do transcode (verificação: após 30 min, RSS do Miloco estável abaixo do teto, idade de captura dentro do limite e stream sem queda).
- [ ] 3.5 Incluir na rotina de operação a checagem do buffer do Miloco (RSS do processo Python e do ffmpeg) e o limiar de intervenção (verificação: comando documentado roda como escrito e produz os dois valores).

## 4. Throughput e cadência efetiva

- [ ] 4.1 Instrumentar o ciclo do processador (espera, conversão, inferência, `engine.step`) e a cadência efetiva em janela de 60 s (verificação: teste do formatador e uma execução curta real em que os campos aparecem preenchidos).
- [ ] 4.2 Executar o profile por 10 min em produção e registrar o trecho dominante dos ~500 ms por ciclo (verificação: medição com p50/p95 por trecho, comparada com os 11–19 ms da inferência isolada).
- [ ] 4.3 Aplicar a correção indicada pelo profile — mover decodificação/conversão para fora do event loop ou usar worker dedicado — preservando o contrato do slot mais recente (verificação: cadência efetiva medida sobe e a idade de frame de processamento não piora em execução de 10 min).
- [ ] 4.4 Recalibrar `DETECT_FPS` e os parâmetros de rastreamento conforme a cadência sustentável, sem mascará-la com limiares (verificação: em execução real, o número de novos ids por objeto visível para de crescer a cada frame e a cadência efetiva fica estável).

## 5. Gate de movimento

- [ ] 5.1 Corrigir a condição do gate para `continue` incondicional sem movimento, adicionar a verificação periódica de sanidade e os contadores `sem_movimento`/`sanidade` (verificação: teste com `yolo.track` espionado confirma zero chamadas em frames sem movimento e uma chamada ao atingir o intervalo de sanidade).
- [ ] 5.2 Medir chamadas ao detector e CPU antes/depois em 10 min de cena sem movimento (verificação: contagem de chamadas e uso de CPU registrados; ganho documentado).

## 6. Emissão idempotente por episódio

- [ ] 6.1 Adicionar em `settings` `TRACK_MIN_HITS`, `TRACK_MIN_LIFETIME_S`, `PASSAGE_MERGE_SECONDS`, `PASSAGE_MERGE_DISTANCE_PX` e `PASSAGE_MIN_PATH_PX`, com validação e `.env.example` (verificação: teste de padrões e valores inválidos).
- [ ] 6.2 Aplicar confirmação mínima a `passagem_*` e `animal` no `EventEngine`, mantendo alertas de portão/acesso fora dessa regra (verificação: teste de uma detecção isolada não emite; track com o mínimo emite).
- [ ] 6.3 Exigir deslocamento ou transição de zona para passagem, impedindo objeto estático de gerar `passagem_pessoa` (verificação: teste com detecções estáticas repetidas não emite; pessoa atravessando emite).
- [ ] 6.4 Implementar o `PassageAggregator` fundindo fragmentos por classe, direção, zona e distância dentro da janela, preservando primeira captura e melhor evidência, com `meta.merged_tracks` (verificação: testes de fragmentos → um evento, duas pessoas próximas mas distintas → dois eventos, e passagens separadas pela janela → dois eventos).
- [ ] 6.5 Replay offline dos eventos reais da rajada de 05/11 00:29–05:37 (fixture exportada com track, instante, direção, zona e deslocamento) através do agregador e registro da redução obtida (verificação: teste de regressão com a fixture confirma no máximo um evento por episódio e documenta a contagem antes/depois).

## 7. Integração e operação

- [ ] 7.1 Rodar `.venv/bin/python -m pytest -q` completo e corrigir regressões (verificação: suíte verde, incluindo os testes novos).
- [ ] 7.2 Executar `python main.py --detect-test --seconds 60` e conferir idade de captura exibida, cadência efetiva e ausência de eventos a partir de conteúdo descartado (verificação: saída do comando e linhas do log).
- [ ] 7.3 Atualizar `README.md` com frescor de captura, limites, resync, cadência efetiva, gate de movimento e agregação de passagem; corrigir a afirmação de OSD ~1 h atrás (verificação: comandos e nomes de configuração documentados conferem com o código).
- [ ] 7.4 Reiniciar o serviço e acompanhar 30 min de produção: frescor dentro do limite após a recuperação, watchdog com os novos campos, nenhum alerta apresentando conteúdo atrasado como recente (verificação: linhas de log e um alerta real conferidos).

## Workflow follow-up

- Arquivar a mudança após revisão e validação em produção.
