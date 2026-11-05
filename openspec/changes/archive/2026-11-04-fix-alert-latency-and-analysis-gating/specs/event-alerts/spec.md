# Spec Delta

## MODIFIED Requirements

### Requirement: Alerta independente de análise posterior
O sistema SHALL iniciar a entrega de alertas de segurança imediatamente após a observação do evento, sem aguardar a conclusão, a disponibilidade ou a profundidade da fila de análise VLM.

#### Scenario: Fila de análise ocupada
- **WHEN** um evento de acesso, aproximação do portão ou comportamento suspeito for registrado enquanto a análise VLM estiver ocupada
- **THEN** o alerta imediato SHALL ser processado sem esperar essa análise

#### Scenario: Fila de análise cheia
- **WHEN** um evento de alerta chegar com a fila de análise no limite de capacidade
- **THEN** o alerta SHALL ser entregue sem descarte e sem aguardar espaço na fila

## ADDED Requirements

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
