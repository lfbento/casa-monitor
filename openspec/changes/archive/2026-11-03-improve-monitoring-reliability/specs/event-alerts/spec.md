# Spec Delta

## Purpose

Garante que eventos de segurança gerem alertas imediatos e observáveis, sem perda silenciosa por debounce, ausência de imagem ou falha do canal de entrega.

## ADDED Requirements

### Requirement: Alerta independente de análise posterior
O sistema SHALL iniciar a entrega de alertas de segurança sem aguardar a conclusão ou a disponibilidade da análise VLM.

#### Scenario: Fila de análise ocupada
- **WHEN** um evento de acesso, aproximação do portão ou comportamento suspeito for registrado enquanto a análise VLM estiver ocupada
- **THEN** o alerta imediato SHALL ser processado sem esperar essa análise

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
