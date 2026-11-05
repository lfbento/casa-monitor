# Spec Delta

## Purpose

Define eventos de atividade baseados em permanência contínua e interação real com veículos, evitando alertas causados por tempo acumulado ou veículos estacionados habitualmente.

## ADDED Requirements

### Requirement: Permanência contígua no portão
O sistema SHALL calcular a permanência no portão somente durante presença contínua na zona e SHALL reiniciar a medição quando a pessoa sair.

#### Scenario: Pessoa sai e retorna ao portão
- **WHEN** uma pessoa permanecer menos que o limiar, sair da zona e retornar posteriormente
- **THEN** o sistema MUST NOT somar os dois períodos e SHALL iniciar uma nova contagem no retorno

### Requirement: Proximidade contínua de veículo
O sistema SHALL calcular o tempo junto a um veículo somente enquanto a proximidade exigida for mantida e SHALL reiniciar a medição quando ela for interrompida.

#### Scenario: Pessoa se afasta antes do limiar
- **WHEN** uma pessoa ficar próxima de um veículo, afastar-se além da margem e depois retornar
- **THEN** o sistema SHALL descartar o período anterior e iniciar nova contagem de proximidade

### Requirement: Veículo com parada recente para loitering
O sistema SHALL exigir histórico de movimento e parada dentro da janela configurada antes de considerar um veículo em alerta de loitering.

#### Scenario: Veículo estacionado habitualmente
- **WHEN** uma pessoa permanecer junto a um veículo já estacionado antes do início do acompanhamento e sem parada recente elegível
- **THEN** o sistema MUST NOT gerar alerta de loitering somente por essa proximidade

#### Scenario: Veículo parou recentemente
- **WHEN** um veículo com movimento prévio parar dentro da janela elegível e uma pessoa permanecer continuamente próxima pelo limiar configurado
- **THEN** o sistema SHALL gerar o evento de loitering correspondente

### Requirement: Um evento por episódio elegível
O sistema SHALL emitir no máximo um evento de cada tipo por episódio contínuo, permitindo novo evento somente após uma nova transição elegível.

#### Scenario: Permanência além do limiar
- **WHEN** a mesma pessoa continuar no portão depois de o evento ter sido emitido
- **THEN** o sistema MUST NOT emitir eventos repetidos em cada frame para o mesmo episódio
