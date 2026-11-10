# event-alerts Specification

## Purpose

Garante que eventos de segurança gerem alertas imediatos e observáveis, sem perda silenciosa por debounce, ausência de imagem ou falha do canal de entrega.

## Requirements

### Requirement: Alerta independente de análise posterior
O sistema SHALL iniciar a entrega de alertas de segurança imediatamente após a observação do evento, sem aguardar a conclusão, a disponibilidade ou a profundidade da fila de análise VLM.

#### Scenario: Fila de análise ocupada
- **WHEN** um evento de acesso, aproximação do portão ou comportamento suspeito for registrado enquanto a análise VLM estiver ocupada
- **THEN** o alerta imediato SHALL ser processado sem esperar essa análise

#### Scenario: Fila de análise cheia
- **WHEN** um evento de alerta chegar com a fila de análise no limite de capacidade
- **THEN** o alerta SHALL ser entregue sem descarte e sem aguardar espaço na fila

### Requirement: Alerta mesmo sem snapshot
O sistema SHALL enviar um alerta textual quando o evento de segurança for persistido, mas seu snapshot estiver indisponível.

#### Scenario: Falha ao salvar imagem
- **WHEN** o armazenamento do snapshot falhar para um evento de segurança
- **THEN** o sistema SHALL enviar os dados disponíveis do evento em texto e registrar a ausência da imagem

### Requirement: Debounce baseado no último alerta entregue
O sistema SHALL calcular o cooldown por tipo de evento a partir do último alerta efetivamente entregue, sem reiniciá-lo para eventos suprimidos ou malsucedidos.

#### Scenario: Sequência contínua de eventos
- **WHEN** eventos do mesmo tipo ocorrerem antes e depois do término do cooldown desde a última entrega bem-sucedida
- **THEN** o sistema SHALL suprimir somente os eventos dentro do cooldown e SHALL permitir o primeiro evento elegível após seu término

#### Scenario: Tipos de alerta independentes
- **WHEN** dois tipos diferentes de alerta ocorrerem dentro do mesmo intervalo
- **THEN** cada tipo SHALL manter seu próprio estado de debounce

### Requirement: Resultado de entrega verdadeiro
O sistema MUST NOT marcar um alerta como entregue antes da confirmação do canal e SHALL tornar falhas de entrega observáveis para tratamento posterior.

#### Scenario: Canal recusa ou não confirma o envio
- **WHEN** o canal retornar falha definitiva ou não confirmar a entrega
- **THEN** o sistema SHALL registrar o resultado sem sucesso e MUST NOT usar esse evento como último alerta entregue no debounce

### Requirement: Alerta informa o horário da observação
O sistema SHALL informar no alerta o instante em que o frame do evento foi observado e SHALL registrar a latência entre observação e entrega, sem apresentar o horário de processamento como se fosse o da cena.

#### Scenario: Frame processado com atraso
- **WHEN** o evento for observado em T e entregue em T+Δ
- **THEN** o alerta SHALL informar T e a latência Δ SHALL ficar registrada

#### Scenario: Atraso acima do limite
- **WHEN** a latência de entrega exceder o limite configurado
- **THEN** o alerta SHALL indicar explicitamente o atraso observado

### Requirement: Latência de alerta limitada
O sistema SHALL entregar alertas dentro do limite de latência configurado, contado a partir da observação do frame, e SHALL registrar a violação do limite como falha observável quando o canal confirmar a entrega.

#### Scenario: Entrega dentro do limite
- **WHEN** o alerta for entregue e confirmado dentro do limite configurado
- **THEN** a latência SHALL ser registrada como sucesso

#### Scenario: Entrega acima do limite
- **WHEN** o alerta for entregue e confirmado após o limite configurado
- **THEN** o sistema SHALL registrar a violação de latência e SHALL informar o atraso no alerta

### Requirement: Alerta informa a idade de captura
O sistema SHALL informar no alerta a idade do conteúdo desde o instante gravado no frame até a entrega, separada da latência de processamento.

#### Scenario: Conteúdo de horas atrás
- **WHEN** um alerta for entregue para um frame cujo instante gravado estiver 5 h antes da entrega e cuja latência de processamento for de milissegundos
- **THEN** o alerta SHALL exibir a idade de captura de aproximadamente 5 h e SHALL manter a latência de processamento identificada separadamente

#### Scenario: Idade de captura desconhecida
- **WHEN** o instante gravado no frame não puder ser determinado
- **THEN** o alerta SHALL indicar idade de captura desconhecida, MUST NOT apresentar o conteúdo como recente

### Requirement: Conteúdo atrasado é sinalizado
O sistema SHALL marcar explicitamente o alerta quando a idade de captura exceder o limite configurado, mantendo a entrega e sem apresentá-lo como ocorrência recente.

#### Scenario: Idade acima do limite
- **WHEN** a idade de captura de um alerta exceder o limite configurado
- **THEN** o sistema SHALL entregar o alerta marcado como conteúdo atrasado e SHALL registrar a violação de frescor

#### Scenario: Idade dentro do limite
- **WHEN** a idade de captura estiver dentro do limite configurado
- **THEN** o alerta MUST NOT exibir marcação de atraso de conteúdo
