# Design

## Context

Pipeline atual (ver `proposal.md` para motivação):

```
WS H.264 → H264StreamDecoder.feed() → leitor (gray + máscara de movimento, slot "mais recente")
        → processador (DETECT_FPS, descarta por MAX_FRAME_AGE_SECONDS)
        → YoloEngine.track() via asyncio.to_thread → EventEngine.step → Store/Notifier
```

Fatos observados em produção que restringem o desenho:

- `arrival_wall` é atribuído no `to_ndarray`; `observed_at` e `detect_latency_ms` medem apenas decode → gravação (47–358 ms). Nada no pipeline conhece o instante de captura.
- O instante gravado no frame (OSD) ficou 5–8 h atrás do host. Causa raiz medida: o patch local do Miloco mantém **5,10 GB no processo Python** e 396 MB no ffmpeg; `h264_transcoder.py` escreve no `stdin` do ffmpeg sem `drain` nem teto, e o encoder em 2560 px não acompanha a câmera (2560×1440@25fps) sob disputa de CPU. O excedente vira atraso (~5 h de conteúdo). Sondas independentes do WS recebem o mesmo atraso, provando que não é fila do nosso cliente.
- O atraso cresce quando o encoder perde para a entrada e cai quando o buffer é descartado (fim do transcode); o processo ffmpeg atual já dura 23 h e o buffer está estável em 5h25 de atraso.
- Cadência efetiva de 1,9 fps (9.494 frames em 5.072 s) contra `DETECT_FPS=10`; `YoloEngine.track` isolado mede 11–19 ms e o decode 11,3 ms/frame.
- Em 9 min o ByteTrack chegou ao id 1.448 (~2,7 ids/s) mantendo ~2 tracks vivos; `_finalize` emite `passagem_pessoa` para qualquer track que viu `ZONA_RUA`, gerando 1.352 eventos em 2 h.
- O gate de movimento cai no detector quando `wait <= 0` (o `continue` está dentro de `if wait > 0`), o que é sempre verdadeiro na cadência real.
- `Store._migrate()` já aplica colunas aditivas por mapa (`observed_at`, `detect_latency_ms`), com `add_event` recebendo os novos campos opcionais.

## Goals / Non-Goals

**Goals:**

- Tornar `capture_age` uma medida verificável, propagada a evento, alerta, relatório e watchdog.
- Detectar backlog e recuperar para o ponto mais recente do stream, sem inventar frescor.
- Medir a cadência efetiva e explicar o fator 10× antes de mudar limiares de rastreamento.
- Produzir no máximo um evento por episódio de passagem, mesmo com tracks fragmentados, e não emitir passagem para objeto estático.
- Corrigir o gate de movimento mantendo uma verificação de sanidade periódica.

**Non-Goals:**

- Corrigir o relógio da câmera; reescrever o Miloco upstream (a correção de buffer é um patch local, aplicado à parte e fora deste repositório).
- Trocar o modelo YOLO ou retreinar detector.
- Reescrever o histórico: eventos antigos ficam com `captured_at`/`capture_age_ms` nulos.
- Suprimir alertas por conteúdo atrasado (o alerta é entregue e marcado).
- Alterar a janela do relatório diário para tempo de captura nesta mudança.

## Decisions

### D1 — Idade de captura lida do watermark do frame (OSD)

O único instante de captura em banda é o OSD. O leitor recorta a faixa fixa do OSD (topo-esquerdo do frame 2560×1440), segmenta as células de caractere por passo fixo e classifica cada célula por correlação com templates de `0–9`, `/` e `:` derivados do próprio frame, sem dependência nova de OCR. Com o texto `YYYY/MM/DD HH:MM:SS`, calcula `capture_age = arrival_wall − osd_time`.

- Cadência de leitura: `CAPTURE_AGE_OCR_INTERVAL_SECONDS` (padrão 30 s); o valor é reutilizado entre leituras e suavizado por mediana de 3 amostras.
- `captured_at` = instante do OSD convertido para o fuso local; `capture_age_ms` = idade no momento da emissão.
- OSD ilegível ou resolução inesperada → `capture_age` desconhecida: contador `ocr_falhas` no watchdog, estado `desconhecido` no alerta. Nunca derivar de `arrival_wall` (é exatamente o erro atual).
- Alternativas descartadas: biblioteca de OCR (`pytesseract` exige binário do sistema; `rapidocr`/`easyocr` somam dependência e download de modelo para ler 19 caracteres de fonte fixa); relógio da câmera via API (não exposto no cliente atual; fica como investigação); PTS do H.264 (não tem tempo absoluto); `arrival_wall` (não vê backlog).

### D2 — Backlog: causa raiz no patch do Miloco e recuperação forçada

Evidência medida em 05/11:

- Processo Python do Miloco com RSS de 5,10 GB; ffmpeg com 396 MB; container em 5,27 GiB. O backlog está no processo Python, não no encoder.
- `h264_transcoder.on_frame` faz `proc.stdin.write(data)` **sem `drain()` e sem teto**; o comentário do patch admite que o backpressure faria a lib MIoT descartar frames, então o buffer cresce em vez de bloquear.
- A câmera entrega HEVC 2560×1440@25fps e o patch fixa `H264_WIDTH=2560` — largura em que o x264 ultrafast deixa de acompanhar sob disputa de CPU. 5,1 GB ≈ 5 h de conteúdo: é o próprio atraso.
- Duas conexões WS independentes recebem o mesmo atraso (~5h25), então não é fila do nosso cliente; descartar no nosso socket não rejuvenesce a fonte.

Sinais: `capture_age` (D1) como medida absoluta, `Δpts/Δwall` como sinal rápido de reprodução fora de 1× e RSS do Miloco como sintoma operacional do buffer.

Recuperação em duas camadas:

1. **Resync pelo nosso lado**: ao exceder `CAPTURE_AGE_RESYNC_SECONDS`, fechar o WS e aguardar `CAPTURE_AGE_RESYNC_WAIT_SECONDS` (padrão 10 s) para o Miloco destruir o transcoder, reconectar e medir a idade. O teardown só acontece quando não há nenhum cliente no stream; se outra aba do Miloco mantiver o fluxo, registrar `resync bloqueado por outro cliente` e manter a violação observável.
2. **Correção na fonte (patch local do Miloco, fora deste repositório)**: limitar o buffer do transcoder — ao passar de um teto (ex.: 2 MB), descartar frames até o próximo keyframe e re-sincronizar (`_synced=False`) em vez de acumular; e reduzir `H264_WIDTH` para uma largura que o encoder acompanhe (1920 ou 1280). Aplicação exige aprovação, reinício do transcode e revalidação da idade.

O intervalo descartado nunca gera evento. Se nem o resync nem o patch reduzirem a idade, a falha persistente é registrada com o valor medido, sem retry silencioso.

### D3 — Throughput: perfilar antes de decidir

Instrumentar o ciclo do processador em quatro trechos: espera por frame, `to_ndarray`/conversão, inferência (`to_thread`) e `engine.step`. Expor média/p95 no log de 60 s.

- Hipóteses a medir: contenção de GIL pelo decode síncrono no leitor; custo de chamada CUDA a partir de thread; escrita de JPEG por evento; volume de log durante rajadas.
- Decisão de cadência só depois do profile: subir a cadência efetiva em direção a `DETECT_FPS` (necessário para o ByteTrack associar) **ou** reduzir `DETECT_FPS` para a taxa sustentável e depender da agregação (D4). As duas coisas juntas escondem a causa.
- Se a conversão for o gargalo, mover `to_ndarray` + gray/resize para thread, preservando o contrato do slot mais recente. Se a inferência em thread for o gargalo, manter um worker dedicado em vez de `to_thread` por chamada.

### D4 — Emissão: confirmação + agregação de episódio

Duas camadas, porque só a confirmação não resolve fragmentos confirmados:

1. **Confirmação** (`TRACK_MIN_HITS`, `TRACK_MIN_LIFETIME_S`): track precisa de 3 detecções ou 1,5 s antes de ser elegível a `passagem_*`/`animal`. Eventos de alerta (`acesso_casa`, `aproximacao_portao`, `alerta_suspeito`) mantêm a lógica contígua já existente.
2. **Agregação** (`PassageAggregator` no `EventEngine`): ao finalizar um track, se existir episódio pendente com mesma classe, direção compatível, zona compatível e centroide a menos de `PASSAGE_MERGE_DISTANCE_PX` (padrão 200 px, ~8% da largura) dentro de `PASSAGE_MERGE_SECONDS` (padrão 5 s), funde em vez de emitir. O episódio preserva: primeiro `captured_at`, maior `path_px`, melhor `conf`/`area`, e `meta.merged_tracks`.

Guarda contra fantasma estático: passagem exige `total_path_px >= PASSAGE_MIN_PATH_PX` (padrão 40) ou transição de zona. É o que impede saco de lixo/cadeira de virarem `passagem_pessoa`.

Alternativas descartadas: deduplicar por identidade (indisponível para desconhecidos e deliberadamente conservadora); confirmação sozinha (fragmentos a 2 fps acumulam 3 hits em 1 s).

### D5 — Gate de movimento com sanidade

Corrigir a condição para `continue` incondicional quando não há movimento. Manter `MOTION_SANITY_INTERVAL_SECONDS` (padrão 10 s): sem movimento, uma detecção periódica confirma que a máscara não quebrou. Contadores `sem_movimento` e `sanidade` no log.

### D6 — Persistência e configuração

- Migração aditiva: `events.captured_at TEXT`, `events.capture_age_ms INTEGER` no mapa de `_migrate()`; `add_event` ganha kwargs opcionais.
- Novos limites em `PipelineLimits`: frescor (aviso/violação/intervalo OCR/resync), cadência mínima, sanidade de movimento, confirmação e agregação. Todos com padrão conservador e validação via helpers existentes (`_positive_float`, `_positive_int`).
- `.env.example` e README atualizados com os nomes e o significado.

### D7 — Alerta e diagnóstico

- Alerta: `Capturado: <captured_at> (<idade> atrás)`; `Recebido: <observed_at>`; processamento continua em campo separado. Idade desconhecida é dita explicitamente. Acima de `CAPTURE_AGE_FAIL_SECONDS`, prefixo `⚠️ Conteúdo atrasado`.
- Watchdog de 60 s: `frescura=...s (máx ...) ocr_falhas=N cadência=...fps (cfg ...) t_infer=...ms`. Violação de frescor é `logging.error` com origem e valor.
- Relatório: linha de frescor (mín/máx de `capture_age_ms` na janela) e nenhuma mudança de janela.

## Risks / Trade-offs

- OCR custa CPU e pode errar → rodar a cada 30 s, recorte pequeno, mediana de 3, contador de falhas; nunca fabricar idade.
- Região do OSD muda com resolução/config da câmera → validar dimensões conhecidas (2560×1440) e falhar explicitamente em tamanho inesperado.
- Agregação pode fundir duas pessoas distintas → exigir direção compatível + distância máxima + janela curta; métricas de fusões e teste com duas pessoas próximas.
- Confirmação atrasa eventos → limitar a 1,5 s; alertas de portão/acesso não passam por ela.
- Reancorar via resync pode pular um evento → registrar o intervalo descartado e o valor de frescor antes/depois; é preferível a entregar horas de passado como presente.
- Outro cliente do Miloco manter o stream aberto → o teardown não ocorre e o buffer permanece; o sistema registra `resync bloqueado por outro cliente` e a violação continua visível.
- Buffer do Miloco volta a crescer se o encoder perder para a entrada → além do patch, alertar quando o RSS do Miloco crescer de forma monotônica e manter a largura de saída dentro do que a CPU sustenta.
- Aplicar o patch do Miloco exige reinício do transcode e aprovação fora deste repositório → planejar janela curta e validar a idade depois; sem o patch, o resync continua sendo a mitigação.
- Ocultar falha de câmera com retry → violação persistente de frescor continua sendo erro observável, sem retry silencioso.
- Perfil pode revelar que o problema é o decode em 1440p → resolver pode exigir mudar resolução de detecção; fica como decisão no apply, guiada pela medição, não por palpite.
- Alerta atrasado é entregue → decisão explícita: marcar, não suprimir (suprimir esconderia a única evidência de um evento real).

## Migration Plan

1. Commitar a árvore atual (1.354 linhas não versionadas) como baseline — pré-requisito para separar produção do que muda.
2. Backup consistente do SQLite (`src.backup(dst)`) com o serviço parado.
3. Recuperação imediata do atraso: garantir zero clientes no stream (fechar a aba do Miloco), parar o `casa-monitor` por ≥10 s, confirmar que o ffmpeg do transcode morreu e que o RSS do Miloco caiu, religar e medir a idade de captura (~0 s).
4. Subir com migração aditiva: colunas novas nulas em eventos antigos; nenhum campo existente muda de significado.
5. Aplicar o patch do Miloco (buffer limitado + largura sustentável) em janela curta, com aprovação, e revalidar a idade por 30 min.
6. Validação em produção: frescor e cadência no log por 30 min, um alerta com idade de captura correta.
7. Rollback: parar serviço, restaurar backup, voltar ao commit anterior e ao patch anterior. Colunas novas não exigem remoção reversa.

## Open Questions

- Qual trecho consome os ~500 ms por ciclo? O profile de D3 responde e pode reordenar as tarefas de throughput.
- A máscara de movimento atual cobre a área de interesse o suficiente para não cegar a rua? Medir `sem_movimento` vs eventos reais após a correção de D5.
