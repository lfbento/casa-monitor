# Spec Delta

## Purpose

Fornece identificação visual conservadora baseada na aparência da pessoa detectada, evitando que o cenário fixo da câmera seja confundido com identidade.

## ADDED Requirements

### Requirement: Assinatura extraída da pessoa
O sistema SHALL calcular assinaturas de aparência somente sobre o recorte da pessoa associada ao evento, tanto no cadastro quanto na comparação.

#### Scenario: Alerta contém foto contextual completa
- **WHEN** um evento possuir uma foto completa para contexto e um recorte da pessoa
- **THEN** o sistema SHALL usar somente o recorte para identidade e SHALL preservar a foto completa para análise humana

### Requirement: Cenário não é evidência de identidade
O sistema MUST NOT atribuir uma identidade com base em semelhança proveniente apenas do fundo, iluminação ou enquadramento da cena.

#### Scenario: Pessoas diferentes na mesma câmera
- **WHEN** uma pessoa desconhecida aparecer no mesmo cenário de uma pessoa cadastrada, mas com recorte de aparência distinto
- **THEN** o sistema MUST NOT identificá-la como a pessoa cadastrada somente pela semelhança do cenário

### Requirement: Correspondência suficiente e sem ambiguidade
O sistema SHALL apresentar um nome somente quando a melhor correspondência atingir o limiar calibrado e tiver margem suficiente sobre as alternativas.

#### Scenario: Similaridade abaixo do limiar
- **WHEN** nenhuma pessoa cadastrada atingir a similaridade mínima
- **THEN** o sistema SHALL informar que a pessoa é desconhecida e MUST NOT exibir um nome

#### Scenario: Duas correspondências próximas
- **WHEN** a melhor e a segunda melhor correspondência não tiverem separação suficiente
- **THEN** o sistema SHALL informar resultado incerto e MUST NOT escolher um nome automaticamente

### Requirement: Cadastro manual usa a mesma evidência
O sistema SHALL cadastrar a identidade confirmada pelo usuário usando o recorte da pessoa do evento selecionado.

#### Scenario: Usuário identifica um evento
- **WHEN** o usuário associar um nome a um evento com recorte válido
- **THEN** o sistema SHALL armazenar a assinatura desse recorte para comparações futuras
