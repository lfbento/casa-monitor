# activity-events Specification

## Purpose

Define eventos de atividade baseados em permanência contínua e interação real com veículos, evitando alertas causados por tempo acumulado ou veículos estacionados habitualmente.

## Requirements

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

### Requirement: Confirmação mínima antes do evento
O sistema SHALL emitir evento de atividade somente após evidência suficiente do track, medida em número de detecções ou tempo de permanência, e MUST NOT emitir evento a partir de uma detecção isolada.

#### Scenario: Detecção isolada
- **WHEN** um track aparecer em um único frame e desaparecer antes do mínimo de confirmação
- **THEN** o sistema MUST NOT emitir evento de atividade para esse track

#### Scenario: Track confirmado
- **WHEN** um track permanecer detectado pelo mínimo de confirmação
- **THEN** o sistema SHALL poder emitir o evento correspondente

### Requirement: Um evento por passagem apesar de fragmentação
O sistema SHALL agrupar tracks fragmentados da mesma passagem, com mesma classe, direção compatível e proximidade dentro da janela de agregação, em um único evento que preserve a primeira observação e a evidência do fragmento mais forte.

#### Scenario: Fragmentos em sequência
- **WHEN** o rastreador criar vários tracks para a mesma pessoa cruzando a cena dentro da janela de agregação
- **THEN** o sistema SHALL emitir no máximo um evento de passagem para o episódio

#### Scenario: Passagens distintas
- **WHEN** duas passagens da mesma classe ocorrerem separadas além da janela de agregação ou sem proximidade espacial
- **THEN** o sistema SHALL emitir um evento para cada passagem

### Requirement: Passagem exige deslocamento
O sistema SHALL emitir passagem somente para tracks com deslocamento ou transição de zona suficientes; detecções estáticas que apenas aparecem e desaparecem no mesmo ponto MUST NOT gerar passagem.

#### Scenario: Objeto estático detectado como pessoa
- **WHEN** um objeto fixo da cena for detectado repetidamente como pessoa sem deslocamento relevante
- **THEN** o sistema MUST NOT emitir `passagem_pessoa`

#### Scenario: Pessoa atravessa a cena
- **WHEN** uma pessoa percorrer distância relevante dentro da zona de rua ou do portão
- **THEN** o sistema SHALL emitir a passagem correspondente
