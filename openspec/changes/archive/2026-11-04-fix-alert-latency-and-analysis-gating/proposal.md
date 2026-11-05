# Proposal

## Why

O alerta do Telegram chega com até uma hora de atraso em relação à cena que a foto mostra. Em 04/11, o alerta #7602 foi enviado às 07:40:31, mas o OSD da câmera na imagem marca 06:36:34 — o frame processado tinha ~64 minutos. O envio em si levou 2 s; o problema é que o laço de captura/processamento está lendo frames velhos de um backlog crescente (decodifica ~12 fps, processa ~2,7 fps) e carimba o evento com o horário de processamento, escondendo o atraso.

No mesmo caminho, a análise VLM está 100% quebrada desde o restart de 03/11 16:17: `EventImages` entrega caminhos `str`, mas `describe_event` chama `.exists()`/`.read_bytes()` como `Path`. São 120 crashes em 16 h, nenhuma descrição nova, e cada crash aborta identidade e alerta de retaguarda do evento. Além disso, o gatilho de análise aceita variação de cena de qualquer origem — vento, chuva e árvores mexendo aumentam a fila com eventos sem pessoa, animal ou veículo real.

## What Changes

- Processar sempre o frame mais recente e descartar frames que excedam uma idade máxima configurável, em vez de acumular backlog; a captura continua drenando o WebSocket mesmo quando a detecção atrasa.
- Carimbar eventos e alertas com o instante em que o frame foi observado (não com o instante de processamento) e expor a idade do frame e a latência do alerta em log e metadados.
- Manter o alerta imediato independente da análise: falha, fila cheia ou atraso do VLM não podem impedir nem atrasar o envio do alerta.
- Enfileirar análise apenas para eventos com evidência de objeto (pessoa, animal ou veículo) acima de área/confiança mínimas; variação de cena sozinha (vento, chuva, vegetação) não dispara análise.
- Limitar a fila de análise por tamanho e idade, descartar itens obsoletos e evitar análise repetida do mesmo track/episódio.
- Corrigir o contrato de caminhos da análise (aceitar `str` e `Path`) para que o worker não morra a cada evento.
- Registrar contadores de frames descartados, profundidade das filas, idade do frame e latência de alerta para tornar a saúde do pipeline observável.

## Capabilities

### New Capabilities

- `live-frame-pipeline`: garante que detecção e eventos usem frames recentes, com backlog limitado, idade máxima configurável e métricas de atraso observáveis.
- `event-analysis`: garante que a análise semântica dos eventos seja acionada apenas por evidência de objeto relevante, com fila limitada, descarte por idade e isolamento total do caminho de alerta.

### Modified Capabilities

- `event-alerts`: o alerta passa a exigir entrega imediata a partir do instante de observação do frame, com horário da captura e latência explícitos, sem herdar atraso de backlog ou de análise.

## Impact

- Código: `pipeline.py` (laço de captura, filas, worker VLM, alerta), `capture/decoder.py` (timestamp do frame), `events/state_machine.py` (timestamp e metadados do evento, evidência de objeto), `events/identity.py` (uso do recorte), `vlm/describe.py` (contrato de caminhos e tempo limite), `settings.py` (limites de idade/fila), `db/store.py` (metadados de captura/latência).
- Configuração: novas variáveis de ambiente para idade máxima de frame, tamanho da fila de análise e idade máxima de análise.
- Banco: novas colunas de metadados (instante de observação e latência) na tabela de eventos, com migração aditiva e idempotente.
- Testes: cobertura nova para descarte de frame velho, carimbo de horário, gating por objeto, fila limitada, independência alerta/análise e aceitação de `str`/`Path` no VLM.
- Operação: serviço precisa ser reiniciado para aplicar; relatórios e alertas existentes continuam funcionando durante a migração.
