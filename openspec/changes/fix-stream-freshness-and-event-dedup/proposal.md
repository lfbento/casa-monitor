# Proposal

## Why

O sistema opera com conteúdo de 5–8 horas atrás (OSD do frame vs `arrival_wall`) enquanto `detect_latency_ms` reporta 47–358 ms e o watchdog de 60 s permanece verde — a métrica de saúde valida o relógio errado. Causa raiz identificada: o patch local do Miloco (`~/.local/opt/miloco-fix`) escreve HEVC no `stdin` do ffmpeg sem `drain` nem limite; o processo Python acumulou **5,1 GB** de backlog (ffmpeg: 396 MB), o equivalente a ~5 h25 de conteúdo a 25 fps — a saída foi fixada em 2560 px, largura em que o encoder deixa de acompanhar a câmera sob disputa de CPU. No mesmo período, fragmentação de tracks produziu 1.352 eventos `passagem_pessoa` em 2 h (contra 137 no dia anterior inteiro), esgotando o limite de VLM/hora e poluindo sessões de presença. O risco é o pior possível para um sistema de segurança: alertas que parecem imediatos descrevem uma cena de horas atrás.

## What Changes

- **Idade de captura como métrica de primeira classe**: medir `capture_age` (instante gravado no frame − `arrival_wall`), propagá-la para evento, alerta, relatório e log periódico; violação de limite vira falha observável.
- **Recuperação de backlog**: ciclo de resync que fecha o WebSocket, aguarda o Miloco destruir o transcoder (só ocorre quando não há outro cliente no stream), reconecta e verifica se a idade caiu; se outro cliente mantiver o fluxo, a falha é reportada em vez de mascarada.
- **Correção companheira no patch local do Miloco** (fora deste repositório, requer aprovação à parte): limitar o buffer do transcoder (descartar frames até o próximo keyframe quando o buffer passar do teto) e reduzir a largura de saída para uma que o encoder acompanhe em tempo real.
- **Cadência efetiva observável**: medir e expor frames processados/s e tempo de ciclo; o `DETECT_FPS` configurado deixa de ser a única referência de throughput.
- **Correção do gate de movimento**: a condição atual cai no detector mesmo sem movimento; corrigir e medir o ganho.
- **Emissão idempotente por episódio**: confirmar um track antes de emitir e agregar fragmentos da mesma passagem (classe + direção + janela) em um único evento.
- **Baseline versionado**: commitar a implementação atual (1.354 linhas não versionadas) antes de tocar no código, para separar o que já está em produção do que muda.
- Novo campo persistido `capture_age_ms` no evento (migração aditiva e idempotente) e novo bloco de diagnóstico no watchdog; nenhuma coluna existente muda de significado.

## Capabilities

### New Capabilities

Nenhuma — os comportamentos afetados já pertencem a capacidades existentes.

### Modified Capabilities

- `live-frame-pipeline`: passa a exigir idade de captura observável, política de catch-up quando o backlog cresce, cadência efetiva medida e gate de movimento efetivo.
- `activity-events`: passa a exigir um evento por episódio de passagem mesmo quando o rastreador fragmenta a mesma pessoa em vários tracks.
- `event-alerts`: passa a exigir que o alerta informe a idade de captura do frame e sinalize explicitamente conteúdo atrasado, sem apresentar a chegada como horário da cena.

## Impact

- Código: `capture/decoder.py`, `capture/ws_client.py`, `pipeline.py`, `settings.py`, `db/store.py`, `detect/tracker.py`, `events/state_machine.py`, `notifier.py`, `report/daily_report.py`, `main.py`.
- Banco: coluna aditiva `capture_age_ms` em `events`; sessões e eventos existentes preservados.
- Configuração: novos limites de frescor (aviso e falha) e parâmetros de agregação de passagem; valores padrão conservadores.
- Operação: alertas passam a exibir a idade do conteúdo; log periódico ganha frescor e cadência; README deixa de afirmar que o OSD está ~1 h atrás; procedimento de resync documentado.
- Dependência externa ao repositório: patch em `~/.local/opt/miloco-fix/deploy/patched/h264_transcoder.py` (buffer limitado e largura de saída) — sem ele, o backlog volta a crescer sob disputa de CPU. A aplicação exige aprovação e reinício do transcode.
- Fora de escopo: correção do relógio da câmera; retreino ou troca do modelo YOLO; reescrever o Miloco upstream.
