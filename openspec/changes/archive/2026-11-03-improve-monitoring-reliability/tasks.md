# Tasks

## 1. Base de testes e migração

- [x] 1.1 Adicionar `pytest` ao grupo de desenvolvimento e criar fixtures para SQLite temporário, tracks sintéticos e dobras de notifier/LLM; verificar que os testes são coletados sem câmera, GPU ou rede.
- [x] 1.2 Implementar migrações aditivas para recortes de evento, metadados de sessão e origem das assinaturas, sem engolir erros inesperados; verificar migração de um banco no schema antigo com registros e integridade preservados.
- [x] 1.3 Marcar assinaturas legadas como `full_frame` e documentar backup, migração e rollback no README; verificar o procedimento contra uma cópia temporária do banco.

## 2. Identidade baseada no recorte

- [x] 2.1 Persistir separadamente foto contextual e recorte da pessoa, atualizar os chamadores de VLM/alertas e o cadastro manual; verificar com teste que cada fluxo usa o caminho correto.
- [x] 2.2 Aplicar limiar e margem no matcher, ignorar assinaturas `full_frame` e retornar desconhecido/incerto nos casos insuficientes; verificar correspondências abaixo do limiar, ambíguas, legadas e válidas em testes unitários.
- [x] 2.3 Documentar os parâmetros de identidade e sua limitação como aparência, não reconhecimento facial; verificar que `.env.example` e README contêm os mesmos defaults.

## 3. Permanência e atividade

- [x] 3.1 Substituir timestamps em `flags` por estado tipado de episódio, reiniciando portão e proximidade ao perder a condição; verificar com tracks sintéticos que períodos separados não se somam e que um novo episódio pode emitir evento.
- [x] 3.2 Unificar a política de veículo com parada recente para embarque e loitering; verificar que veículo habitual não gera alerta, veículo recente elegível gera e episódios contínuos não duplicam eventos por frame.

## 4. Presença na casa

- [x] 4.1 Implementar abertura confirmada/incerta, associação de troca de track e encerramento somente por correspondência única; verificar duas pessoas saindo em ordem inversa, troca de track e EXIT sem correspondência sem fechamento FIFO.
- [x] 4.2 Implementar `last_seen_time`, exposição de sessões `STALE` após o limite configurado e consultas por sobreposição/`ts_to`; verificar sessão antiga, entrada anterior com saída na janela e relatório histórico sem uso do estado atual.
- [x] 4.3 Documentar os estados de sessão e `HOUSE_SESSION_STALE_HOURS`; verificar que a saída do relatório distingue presença confirmada, incerta e antiga.

## 5. Entrega de alertas

- [x] 5.1 Implementar estados `pending`, `sent`, `suppressed` e `failed`, atualizar o cooldown somente após sucesso e liberar a retaguarda para falhas sem reter estados indefinidamente; verificar sequência contínua após o cooldown, tipos independentes e `send=False`.
- [x] 5.2 Permitir callback sem snapshot, enviar fallback textual e impedir enfileiramento VLM sem imagem; verificar que falha de escrita não elimina o alerta e que a entrega imediata não aguarda VLM.

## 6. Relatórios confiáveis

- [x] 6.1 Criar projeção limitada para o LLM com agregação de rotina, prioridade de segurança e contagens determinísticas; verificar com milhares de eventos sintéticos que `REPORT_LLM_MAX_CHARS` é respeitado e alertas/acessos permanecem representados.
- [x] 6.2 Propagar `finish_reason`, validar os quatro cabeçalhos na ordem e acionar fallback para resposta truncada, incompleta ou indisponível; verificar esses três casos e um relatório válido em testes.
- [x] 6.3 Propagar falha de entrega no agendador e na CLI; verificar log sem falso sucesso no job e código diferente de zero quando geração ou envio solicitado falhar.
- [x] 6.4 Documentar o orçamento do LLM, fallback e comportamento de saída da CLI; verificar que os exemplos do README continuam executáveis.

## 7. Integração e implantação

- [x] 7.1 Executar toda a suíte e gerar um relatório real com `--generate-report-now --no-send`; verificar quatro seções, totais, sessões da janela e ausência de chamadas externas de entrega.
- [x] 7.2 Criar backup consistente do banco, aplicar a migração com o serviço parado e reiniciar; verificar `PRAGMA integrity_check`, inicialização do pipeline e logs sem falso sucesso ou exceções de migração.
