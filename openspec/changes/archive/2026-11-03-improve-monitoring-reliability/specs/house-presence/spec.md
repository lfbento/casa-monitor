# Spec Delta

## Purpose

Mantém uma representação consistente de quem entrou, saiu e permanece na casa, sem criar sessões duplicadas ou fechar a sessão de outra pessoa.

## ADDED Requirements

### Requirement: Uma sessão ativa por presença identificada
O sistema SHALL deduplicar aberturas de sessão quando tracks diferentes representarem a mesma presença, usando a evidência de identidade disponível.

#### Scenario: Troca de track dentro da casa
- **WHEN** uma entrada já tiver aberto uma sessão e um novo track com aparência compatível surgir na área interna
- **THEN** o sistema SHALL associar o novo track à sessão existente e MUST NOT abrir uma segunda sessão confirmada

### Requirement: Saída associada a sessão compatível
O sistema SHALL encerrar a sessão cuja identidade seja compatível com a pessoa que saiu e MUST NOT selecionar outra sessão apenas por ser a mais antiga.

#### Scenario: Pessoas saem em ordem diferente da entrada
- **WHEN** duas pessoas tiverem sessões abertas e a segunda pessoa identificada sair primeiro
- **THEN** o sistema SHALL encerrar a sessão dessa pessoa e manter a outra sessão aberta

### Requirement: Ausência de correspondência não fecha terceiros
O sistema SHALL registrar uma saída sem correspondência como incerta e MUST NOT encerrar uma sessão incompatível para completar o evento.

#### Scenario: Saída sem sessão compatível
- **WHEN** ocorrer uma saída cuja aparência não corresponda de forma suficiente a nenhuma sessão aberta
- **THEN** o sistema SHALL preservar as sessões abertas e registrar a ausência de correspondência

### Requirement: Sessões antigas não são presença confirmada indefinidamente
O sistema SHALL distinguir sessões recentes sem saída de sessões que excederam o limite configurado sem confirmação.

#### Scenario: Sessão ultrapassa o limite de confirmação
- **WHEN** uma sessão sem saída exceder o tempo máximo configurado para ser considerada presença confirmada
- **THEN** o sistema SHALL marcá-la como incerta e MUST NOT apresentá-la como pessoa confirmadamente dentro da casa

### Requirement: Consulta por sobreposição com a janela
O sistema SHALL incluir no relatório sessões que se sobreponham à janela e avaliar presença aberta no instante final dessa janela.

#### Scenario: Entrada anterior e saída dentro da janela
- **WHEN** uma sessão começar antes da janela e terminar durante ela
- **THEN** a sessão SHALL aparecer na consolidação com sua sobreposição temporal

#### Scenario: Geração de relatório histórico
- **WHEN** um relatório for gerado para uma janela encerrada no passado
- **THEN** a lista de pessoas dentro SHALL refletir o instante final da janela, não o instante atual da geração
