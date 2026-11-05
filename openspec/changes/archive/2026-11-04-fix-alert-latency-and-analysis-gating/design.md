# Design

## Context

Ver `proposal.md` para a motivação. O laço atual (`pipeline.py:277-355`) lê o WebSocket e processa cada frame decodificado de forma síncrona: cálculo de movimento, YOLO/ByteTrack e máquina de estados rodam no mesmo `async for`. Quando a inferência demora mais que o intervalo de amostragem, a leitura para e o backlog cresce na origem.

Medições de 04/11 (serviço com 16 h):

- `frames` avança ~12 fps; `processados` avança ~2,7 fps — o laço consome menos da metade dos frames que decodifica.
- O alerta #7602 foi emitido às 07:40:29 e entregue às 07:40:31 (2 s), mas o OSD do frame marca 06:36:34 — o frame analisado tinha ~64 min.
- O OSD da câmera está entre 63 e 67 min atrás do host (host sincronizado por NTP), então o OSD não serve como relógio confiável; o horário do sistema é o host.
- `EventImages` carrega caminhos como `str` (`events/state_machine.py:39-42`), mas `describe_event` espera `Path` (`vlm/describe.py:49-55`): 120 crashes de worker em 16 h.

Restrições: não alterar a stack Miloco; GPU GTX 1080 com um único processo de inferência; SQLite com migração aditiva e idempotente; alertas não podem depender da análise.

## Goals / Non-Goals

**Goals:**

- Limitar a idade do frame processado e manter a captura drenada durante a inferência.
- Carimbar eventos e alertas com o instante de observação do frame.
- Enfileirar análise apenas com evidência de objeto e com fila limitada por tamanho e idade.
- Isolar o caminho de alerta de qualquer fila, falha ou atraso da análise.
- Tornar idade de frame, descartes e latência de alerta observáveis.

**Non-Goals:**

- Corrigir o relógio da câmera (o offset do OSD é configuração do equipamento).
- Resolver identidade de pessoas ou vazamento de sessões de casa.
- Alterar a geração do relatório diário ou o provedor de LLM.
- Detecção de vegetação por segmentação; o filtro de objeto atua nos eventos, não substitui o YOLO.

## Decisions

### 1. Leitor e processador desacoplados, com slot de frame mais recente

Um único laço bloqueante não consegue drenar o stream enquanto infere. A captura passa a ter duas tarefas:

```text
WS --> leitor: decodifica + gray 320x180 + motion --> slot "latest" (sobrescreve)
                                                        |
                          processador (DETECT_FPS) --> pega o mais novo --> YOLO
                                                        |
                                          EventEngine.step -> evento/alerta
```

- O leitor mantém um slot de tamanho 1 (sobrescrita), guardando `frame`, `gray`, `motion`, `changed`, `arrival_mono`, `arrival_wall`.
- O processador roda em intervalo de `1/DETECT_FPS`, pega o slot e limpa; a inferência roda via `asyncio.to_thread` para não bloquear o leitor.
- Se o slot estiver vazio ou o frame exceder `MAX_FRAME_AGE_SECONDS`, o processador dorme e tenta de novo; o frame velho é descartado e contabilizado.
- Alternativa descartada: continuar em laço único e pular frames velhos depois de decodificar — o próprio `decoder.feed` já bloqueia a leitura, então o backlog continuaria na origem.

O movimento continua sendo calculado no leitor (custo ~1 ms por frame), preservando o gatilho por mudança de pixel sem acoplar ao YOLO.

### 2. Idade máxima e carimbo de observação

- `Frame` ganha `arrival_mono` e `arrival_wall` no momento em que o chunk é decodificado.
- `MAX_FRAME_AGE_SECONDS` (padrão 2,0 s) define o descarte. O valor cobre ~2 intervalos de amostragem a 10 FPS mais a latência de uma inferência; é conservador e ajustável por `.env`.
- `EventEngine.step` recebe o instante de observação e o usa em `_emit`, em vez de `_now_iso()` no momento do processamento.
- O evento guarda `observed_at` e `detect_latency_ms`; o alerta usa `observed_at` no texto e anexa a latência quando exceder `ALERT_MAX_LATENCY_SECONDS` (padrão 5 s).
- O OSD da câmera não é usado: está ~64 min atrás e varia entre medições; usar o host mantém coerência com o banco, o relatório e o Telegram.

### 3. Gatilho de análise por evidência de objeto

O gate atual (`pipeline.py:63-77`) decide pela variação de cena do frame inteiro. Vento, chuva e vegetação mudam >2,5% dos pixels e derrotam o filtro. A decisão passa a ser:

1. O evento só entra na fila se tiver detecção associada de `person`, `animal` ou veículo com área de bbox e confiança acima de mínimos (`ANALYSIS_MIN_AREA_PX`, `ANALYSIS_MIN_CONF`).
2. O gate de cena continua como filtro adicional de redundância, nunca como único critério.
3. Passagens rotineiras de veículo continuam fora da fila (orçamento de VLM preservado).
4. Movimento sem objeto confirmado não gera evento nem análise.

A evidência (classe, área, confiança, track) é calculada no `_emit`, onde `st.cls_name` e `st.bboxes[-1]` já estão disponíveis, e anexada ao `meta` do evento.

Limitação assumida: o YOLO ainda roda em frames com vento que passam pelo filtro de blob; o filtro de objeto reduz a análise e o ruído de eventos, não o custo de inferência. Ajuste de área mínima do blob e futura zona de vegetação ficam como calibração.

### 4. Fila de análise com limite e descarte

- `asyncio.Queue(maxsize=ANALYSIS_QUEUE_MAX)` (padrão 8); ao encher, remove o item mais antigo antes de inserir, sem tocar no alerta.
- Itens com idade > `ANALYSIS_MAX_AGE_SECONDS` (padrão 180 s) são descartados sem chamar o provedor.
- Deduplicação por `(track_id, event_type)` em janela `ANALYSIS_DEDUP_SECONDS` (padrão 60 s).
- Fila prioritária de alertas com capacidade própria e sempre na frente.
- O worker continua sequencial (uma chamada por vez, sem concorrência no provedor) e com `timeout` de 30 s.
- Alternativa descartada: várias chamadas concorrentes — o provedor é o gargalo e a concorrência só aumenta o custo e a variância.

### 5. Alerta fora do executor do VLM

`_send_alert_now` continua como tarefa criada no evento, mas a chamada ao VLM passa a usar um `ThreadPoolExecutor(max_workers=1)` dedicado. Assim uma chamada longa do provedor não ocupa thread do executor padrão usado por `send_photo`/identidade. O alerta é emitido antes de qualquer trabalho de análise e não lê a fila de análise.

### 6. Contrato de caminhos tolerante na análise

`describe_event` aceita `str | Path` e normaliza para `Path` na entrada; `_encode_image` idem. O pipeline pode continuar passando `imgs.context`/`imgs.person_crop` como texto. Teste unitário cobre caminho `str` existente, `str` inexistente e `Path`.

### 7. Migração aditiva do banco

`events` ganha `observed_at TEXT` e `detect_latency_ms INTEGER` via `_ensure_columns`, no mesmo padrão idempotente já usado. `meta` continua carregando evidência de objeto em JSON. Nada é removido ou reescrito; rollback é reverter o código (colunas extras são inertes).

### 8. Observabilidade

O watchdog de 60 s passa a registrar: frames, processados, descartados por idade, idade máxima observada, profundidade das duas filas, descartes de análise e latência do último alerta. Violações de latência entram como `WARNING` com `event_id`.

## Risks / Trade-offs

- Descartar frames pode perder um evento curto → o gatilho de movimento continua rodando em todos os frames e o slot mantém o frame mais recente; `DETECT_FPS` e `MAX_FRAME_AGE_SECONDS` são ajustáveis.
- ByteTrack entre frames com gap → a velocidade usa o `dt` real entre inferências; gaps grandes precisam de proteção para não gerar picos falsos de velocidade.
- O filtro de objeto não elimina inferência causada por vegetação → custo de GPU permanece; calibrar blob mínimo e, se necessário, criar zona de vegetação em iteração futura.
- O OSD da câmera continuará ~1 h atrás do alerta → documentar no README que o horário oficial é o do host; corrigir o relógio da câmera no app Mi Home é ação do usuário.
- Telegram/API pode exceder o limite de 5 s mesmo com o pipeline saudável → a violação é registrada e sinalizada no alerta, sem mascarar.
- Duas tarefas novas aumentam a complexidade do laço → cobrir com testes de unidade do slot/fila e um teste de fumaça com stream real curto.

## Migration Plan

1. Parar o serviço e gerar backup consistente do SQLite (procedimento já documentado no README).
2. Aplicar as mudanças de código e configuração; iniciar o serviço. A migração de colunas roda no startup, de forma aditiva.
3. Validar com `python main.py --detect-test --seconds 60`: conferir no log a idade do frame, descartes e que nenhum alerta depende da fila de análise.
4. Em caso de problema: parar o serviço, restaurar o backup e voltar ao código anterior. As colunas novas não exigem remoção.

## Open Questions

Nenhuma pendência que altere specs, abordagem ou tarefas. Os valores padrão de idade, fila e latência serão confirmados na medição do apply.
