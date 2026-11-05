# Spec Delta

## Purpose

Garante que a análise semântica dos eventos seja acionada apenas por evidência de objeto relevante, sem acumular fila nem bloquear alertas.

## ADDED Requirements

### Requirement: Análise exige evidência de objeto
O sistema SHALL enfileirar análise semântica somente para eventos com pessoa, animal ou veículo associado acima das áreas e confianças mínimas; variação de cena sozinha MUST NOT disparar análise.

#### Scenario: Vegetação, chuva ou vento
- **WHEN** apenas árvores, chuva ou vento alterarem a cena, sem detecção de pessoa, animal ou veículo
- **THEN** nenhuma análise SHALL ser enfileirada

#### Scenario: Objeto relevante no evento
- **WHEN** o evento tiver pessoa, animal ou veículo com área e confiança mínimas
- **THEN** a análise SHALL ser enfileirada

#### Scenario: Detecção abaixo do mínimo
- **WHEN** a detecção associada ao evento ficar abaixo da área ou da confiança mínima
- **THEN** a análise MUST NOT ser enfileirada

### Requirement: Fila de análise limitada
O sistema SHALL limitar a fila de análise por quantidade e SHALL descartar itens cuja idade exceda o limite, sem bloquear alertas nem o processamento dos próximos eventos.

#### Scenario: Fila cheia
- **WHEN** um novo evento elegível chegar com a fila no limite
- **THEN** o sistema SHALL descartar um item para manter a fila limitada e SHALL manter o alerta imediato do novo evento

#### Scenario: Item obsoleto
- **WHEN** um item da fila exceder a idade máxima
- **THEN** o sistema SHALL descartá-lo sem chamar o provedor de análise

### Requirement: Uma análise por episódio
O sistema SHALL evitar análises repetidas do mesmo track ou episódio dentro da janela de deduplicação.

#### Scenario: Eventos repetidos do mesmo track
- **WHEN** vários eventos do mesmo track entrarem na fila dentro da janela de deduplicação
- **THEN** o sistema SHALL analisar no máximo um deles

### Requirement: Falha de análise isolada
O sistema SHALL conter falhas da análise no worker, mantendo-o ativo, e MUST NOT impedir a identidade nem o alerta de retaguarda do mesmo evento.

#### Scenario: Provedor indisponível
- **WHEN** o provedor de análise falhar para um evento
- **THEN** o worker SHALL continuar processando os próximos itens e o alerta do evento SHALL seguir pelo caminho independente

#### Scenario: Caminho de imagem inválido
- **WHEN** o caminho da imagem do evento não puder ser lido
- **THEN** a análise SHALL registrar a falha daquele evento e MUST NOT derrubar o worker

### Requirement: Caminho da imagem aceito como armazenado
O sistema SHALL executar a análise usando o caminho de imagem persistido no evento, aceitando a representação de caminho usada pelo registro, sem falhar por diferença de tipo.

#### Scenario: Caminho persistido como texto
- **WHEN** o evento trouxer o caminho da imagem como texto
- **THEN** a análise SHALL ler a imagem normalmente e SHALL produzir descrição ou resultado indisponível, sem erro de tipo
