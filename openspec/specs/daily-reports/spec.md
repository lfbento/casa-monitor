# daily-reports Specification

## Purpose

Assegura que o relatório diário seja completo, derivado dos dados registrados e entregue com estado de sucesso ou falha representado corretamente.

## Requirements

### Requirement: Relatório completo mesmo com LLM inválido
O sistema SHALL produzir as quatro seções documentadas do relatório e SHALL usar o fallback determinístico quando a resposta do LLM estiver incompleta, truncada, malformada ou indisponível.

#### Scenario: Resposta contém apenas a primeira seção
- **WHEN** o LLM retornar texto não vazio sem todas as quatro seções obrigatórias
- **THEN** o sistema MUST NOT aceitar esse texto como relatório final e SHALL gerar o fallback com as quatro seções

#### Scenario: Resposta excede o limite de geração
- **WHEN** o provedor indicar que a resposta terminou por limite de tokens
- **THEN** o sistema SHALL descartar a resposta incompleta e usar o fallback determinístico

### Requirement: Entrada do LLM limitada sem perda de fatos críticos
O sistema SHALL agregar eventos rotineiros e limitar detalhes enviados ao LLM, preservando contagens exatas e os detalhes de alertas, acessos e sessões.

#### Scenario: Dia com milhares de passagens de veículos
- **WHEN** a janela contiver um volume de eventos que exceda o orçamento de entrada
- **THEN** o sistema SHALL enviar passagens rotineiras de forma agregada e SHALL manter os dados de segurança necessários ao relatório

### Requirement: Consolidação determinística dos totais
O sistema SHALL calcular contagens e totais a partir dos registros persistidos, sem depender do LLM para produzir esses valores.

#### Scenario: Relatório gerado com síntese do LLM
- **WHEN** o LLM produzir uma síntese textual aceita
- **THEN** os totais apresentados SHALL corresponder à consolidação dos registros da janela

### Requirement: Estado de geração e entrega observável
O sistema SHALL registrar sucesso de entrega somente após confirmação do canal e a CLI SHALL retornar falha quando a geração ou o envio solicitado falhar.

#### Scenario: Falha no envio agendado
- **WHEN** o canal recusar o relatório agendado
- **THEN** o sistema SHALL registrar erro de entrega e MUST NOT registrar que o relatório foi enviado

#### Scenario: Falha no envio pela CLI
- **WHEN** o usuário solicitar geração com envio e o canal retornar falha
- **THEN** a CLI SHALL informar a falha e retornar código diferente de zero
