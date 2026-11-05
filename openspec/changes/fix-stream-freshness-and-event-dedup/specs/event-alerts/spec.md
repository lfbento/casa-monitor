# Spec Delta

## ADDED Requirements

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
