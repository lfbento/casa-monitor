# Spec Delta

## ADDED Requirements

### Requirement: Idade de captura observável
O sistema SHALL medir a idade de captura de cada frame a partir do instante gravado no próprio frame (watermark/OSD) e do instante de chegada, e SHALL expor essa idade periodicamente, mesmo quando a latência de processamento estiver baixa.

#### Scenario: Frame antigo com processamento rápido
- **WHEN** um frame chegar em T e o instante gravado nele for T−5h
- **THEN** a idade de captura SHALL ser de aproximadamente 5h e SHALL aparecer no log periódico, independentemente de a latência de processamento ser baixa

#### Scenario: Instante gravado ilegível
- **WHEN** o instante gravado no frame não puder ser lido
- **THEN** o sistema SHALL registrar a idade de captura como desconhecida e SHALL sinalizar a ausência de evidência de frescor

### Requirement: Violação de frescor é falha observável
O sistema SHALL comparar a idade de captura com limites configurados e SHALL registrar a violação como falha observável, sem interromper a captura nem o processamento.

#### Scenario: Idade acima do limite
- **WHEN** a idade de captura exceder o limite de violação configurado
- **THEN** o sistema SHALL registrar a violação com o valor medido, a origem e o instante, e SHALL continuar processando

#### Scenario: Idade dentro do limite
- **WHEN** a idade de captura permanecer dentro do limite de aviso
- **THEN** o sistema MUST NOT registrar violação de frescor

### Requirement: Recuperação de conteúdo atrasado
O sistema SHALL detectar quando a idade de captura crescer de forma persistente e SHALL executar uma recuperação que force a fonte a fornecer conteúdo recente, em vez de processar horas de atraso; o conteúdo descartado MUST NOT gerar evento e o resultado da recuperação SHALL ser registrado como recuperado ou bloqueado.

#### Scenario: Idade de captura crescente
- **WHEN** a idade de captura exceder o limite de recuperação configurado
- **THEN** o sistema SHALL executar o ciclo de recuperação e SHALL registrar a idade antes e depois

#### Scenario: Recuperação bloqueada por outro cliente
- **WHEN** a fonte mantiver o conteúdo atrasado porque outro cliente impede o encerramento do stream
- **THEN** o sistema SHALL registrar que a recuperação foi bloqueada e SHALL manter a violação de frescor observável, sem retry silencioso

#### Scenario: Reconexão após queda
- **WHEN** o stream reconectar após uma queda
- **THEN** o sistema SHALL retomar do ponto mais recente disponível e MUST NOT reproduzir o intervalo acumulado durante a queda

### Requirement: Cadência efetiva medida
O sistema SHALL medir e expor a taxa efetiva de frames processados e o tempo de ciclo da detecção, distinguindo-a da taxa configurada.

#### Scenario: Cadência abaixo do configurado
- **WHEN** a taxa efetiva ficar abaixo da fração mínima da taxa configurada durante a janela de observação
- **THEN** o sistema SHALL registrar a diferença entre taxa configurada e efetiva como condição observável

### Requirement: Gate de movimento efetivo
O sistema SHALL evitar a inferência quando o frame não apresentar movimento suficiente, permitindo apenas uma verificação periódica de sanidade, sem que o caminho sem movimento execute a detecção a cada frame.

#### Scenario: Cena sem movimento
- **WHEN** o frame mais recente não apresentar movimento acima do limiar
- **THEN** o sistema MUST NOT executar detecção nesse frame, exceto na verificação periódica de sanidade configurada, e SHALL contabilizar a decisão no diagnóstico

#### Scenario: Verificação de sanidade
- **WHEN** o intervalo desde a última verificação de sanidade for atingido sem movimento
- **THEN** o sistema SHALL executar uma detecção para confirmar que o gate de movimento continua operante

#### Scenario: Movimento detectado
- **WHEN** houver movimento acima do limiar
- **THEN** o sistema SHALL executar a detecção normalmente

### Requirement: Limites de frescor configuráveis
O sistema SHALL permitir configurar os limites de aviso e de violação da idade de captura, aplicando padrões conservadores e registrando-os quando a configuração estiver ausente ou inválida.

#### Scenario: Configuração ausente ou inválida
- **WHEN** os limites de frescor não estiverem definidos ou contiverem valores inválidos
- **THEN** o sistema SHALL usar os valores padrão e SHALL registrá-los no início do pipeline
