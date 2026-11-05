# live-frame-pipeline Specification

## Purpose

Garante que detecção e eventos usem frames recentes, com backlog limitado e latência observável.

## Requirements

### Requirement: Processamento do frame mais recente
O sistema SHALL processar o frame mais recente disponível e SHALL descartar frames cuja idade exceda o limite configurado, em vez de processar um backlog acumulado.

#### Scenario: Detecção mais lenta que o stream
- **WHEN** houver frames pendentes e a detecção estiver atrasada
- **THEN** o sistema SHALL processar o frame mais recente e descartar os que excederam a idade máxima, sem aumentar o atraso

#### Scenario: Detecção acompanha o stream
- **WHEN** a detecção conseguir acompanhar a taxa de frames
- **THEN** o sistema SHALL processar os frames amostrados sem descartes por idade

### Requirement: Captura mantém o frame atual durante a inferência
O sistema SHALL manter a captura atualizada enquanto houver detecção em andamento, de modo que o próximo frame analisado seja o mais recente disponível quando a inferência terminar.

#### Scenario: Inferência ocupada
- **WHEN** uma inferência demorar mais que o intervalo de amostragem
- **THEN** a captura SHALL continuar avançando e o próximo frame analisado SHALL ser o mais recente, não o próximo da fila antiga

### Requirement: Horário do evento reflete a observação
O sistema SHALL registrar o horário do evento a partir do instante em que o frame foi observado, não do instante em que foi processado, e SHALL registrar o atraso quando existir.

#### Scenario: Frame observado e processado em instantes diferentes
- **WHEN** um frame observado em T for processado em T+Δ e gerar um evento
- **THEN** o evento SHALL registrar T e o atraso Δ SHALL ficar registrado

#### Scenario: Frame descartado não gera evento
- **WHEN** um frame exceder a idade máxima antes de ser analisado
- **THEN** o sistema MUST NOT gerar evento a partir desse frame

### Requirement: Descarte observável
O sistema SHALL contabilizar os frames descartados por idade e a idade máxima observada, expondo esses valores periodicamente.

#### Scenario: Frame descartado por idade
- **WHEN** um frame for descartado por exceder a idade máxima
- **THEN** o contador de descartes SHALL ser incrementado e SHALL aparecer no log periódico do pipeline

### Requirement: Limite de idade configurável
O sistema SHALL permitir configurar a idade máxima do frame e SHALL aplicar um padrão conservador quando a configuração estiver ausente ou inválida.

#### Scenario: Configuração ausente ou inválida
- **WHEN** a idade máxima não estiver definida ou contiver valor inválido
- **THEN** o sistema SHALL usar o valor padrão e SHALL registrá-lo no início do pipeline
