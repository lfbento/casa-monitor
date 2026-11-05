# Proposal

## Why

O monitoramento está operacional, mas falhas confirmadas podem suprimir alertas, entregar relatórios incompletos e associar entradas e saídas às pessoas erradas. A mudança é necessária agora porque esses comportamentos afetam diretamente a confiabilidade das informações de segurança apresentadas ao usuário.

## What Changes

- Corrigir o debounce e o estado de entrega dos alertas imediatos, incluindo fallback textual quando não houver snapshot e tratamento explícito de falha de envio.
- Limitar e priorizar os dados enviados ao LLM, validar a completude do relatório e usar o fallback determinístico quando a resposta estiver truncada ou inválida.
- Registrar corretamente sucesso ou falha na entrega do relatório agendado e no retorno da CLI.
- Medir permanência no portão e proximidade de veículos por intervalos contínuos, reiniciando temporizadores quando a condição deixar de existir.
- Deduplicar sessões da casa após troca de track, correlacionar EXIT por identidade compatível, tratar sessões incertas e incluir sessões sobrepostas à janela do relatório.
- Calcular assinaturas de aparência sobre o recorte da pessoa, separadamente da foto contextual, e representar correspondências insuficientes como desconhecidas.
- Adicionar testes de regressão para as transições e falhas corrigidas.

## Capabilities

### New Capabilities

- `event-alerts`: entrega imediata e observável de alertas, com debounce correto, fallback sem imagem e tratamento de falhas.
- `daily-reports`: geração completa do relatório diário, entrada limitada para o LLM, fallback seguro e estado de entrega verdadeiro.
- `activity-events`: detecção de permanência contínua no portão e junto a veículos com parada recente.
- `house-presence`: abertura, correlação, encerramento e consulta consistente das sessões de presença na casa.
- `person-identification`: comparação de aparência baseada no recorte da pessoa e classificação conservadora de correspondências.

### Modified Capabilities

Nenhuma; o projeto ainda não possui especificações duráveis.

## Impact

- Código afetado: `pipeline.py`, `events/state_machine.py`, `events/identity.py`, `detect/tracker.py`, `db/store.py`, `report/daily_report.py`, `main.py` e `notifier.py`.
- Dados: migração incremental do SQLite para metadados de sessão, aparência e estados necessários à confiabilidade; preservação dos registros existentes.
- Interfaces externas: Telegram e provedor OpenAI-compatible permanecem compatíveis; nenhuma mudança breaking planejada.
- Verificação: nova cobertura automatizada focada nas regras críticas e smoke tests dos fluxos alterados.
- Fora do escopo: otimização do pipeline de vídeo, alterações de TLS, reconexão do Miloco, novos canais de notificação e novos tipos de evento.
