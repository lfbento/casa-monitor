# Spec Delta

## ADDED Requirements

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
