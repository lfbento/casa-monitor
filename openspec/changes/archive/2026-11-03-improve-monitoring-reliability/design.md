# Design

## Context

A solução atual concentra captura, eventos, persistência e notificações em um único processo Python, com SQLite em WAL e integrações síncronas isoladas em threads quando necessário. A mudança deve preservar essa arquitetura e os dados já coletados. Consulte `proposal.md` para a motivação e as specs delta para os comportamentos obrigatórios.

## Goals / Non-Goals

**Goals:**

- Corrigir as transições críticas sem introduzir outro serviço ou dependência de runtime.
- Tornar os resultados conservadores quando identidade ou correspondência forem insuficientes.
- Manter migração aditiva, backup e rollback do banco operacional.
- Cobrir as regras com testes determinísticos, sem câmera, GPU ou chamadas externas.

**Non-Goals:**

- Introduzir fila persistente de alertas, serviço de mensageria ou nova arquitetura assíncrona.
- Trocar histogramas por embeddings, reconhecimento facial ou outro modelo de identidade.
- Alterar protocolos do Telegram, LLM ou Miloco.
- Otimizar captura, inferência, TLS ou reconexão de vídeo.

## Decisions

### 1. Estado de entrega por evento, separado do debounce

Substituir o conjunto `_alerted` por um estado pequeno por evento: `pending`, `sent`, `suppressed` ou `failed`. O cooldown por tipo será atualizado somente após `send_photo`/`send` confirmar sucesso. Eventos suprimidos não ganham retentativa pela fila VLM; eventos com falha permanecem elegíveis para a retaguarda já existente.

O callback de evento aceitará imagem opcional. Sem snapshot, o evento continuará persistido e o alerta usará texto; a fila VLM somente receberá eventos com imagem válida. Isso remove a dependência atual entre `cv2.imwrite` e a emissão do alerta.

Alternativa considerada: fila persistente com retry automático. Foi adiada por ampliar migração e semântica de entrega; a falha continuará visível em logs e não bloqueará alertas seguintes.

### 2. Relatório com orçamento de entrada e validação estrutural

A consolidação terá duas representações: dados completos para o fallback e uma projeção limitada para o LLM. Passagens rotineiras serão agregadas por tipo e faixa horária. Alertas, acessos e sessões terão prioridade; descrições longas e eventos secundários serão limitados de forma determinística. Um limite configurável de caracteres, inicialmente `REPORT_LLM_MAX_CHARS=80000`, evita prompts sem teto sem adicionar tokenizer.

A chamada ao LLM preservará `content` e `finish_reason`. O texto só será aceito se não indicar truncamento e contiver os quatro cabeçalhos obrigatórios, na ordem esperada. Resposta inválida ou exceção acionará o fallback completo. Contagens oficiais serão renderizadas a partir da consolidação, não calculadas pelo LLM.

O agendador verificará o booleano do notifier. A CLI retornará código diferente de zero para falha de geração ou de envio solicitado.

Alternativa considerada: apenas reduzir `max_tokens` ou aumentar o contexto. Isso não resolve validade, custo nem completude e foi descartado.

### 3. Temporizadores tipados e ciclos de presença

Os timestamps `gate_t:*` e `veh_t:*` sairão de `TrackState.flags` para campos tipados, junto ao veículo atualmente associado e aos marcadores de emissão do episódio. Sair da zona, romper a proximidade ou trocar de veículo limpa o episódio e seu marcador de emissão. Uma nova entrada elegível pode então gerar outro evento.

A verificação de loitering junto a veículo usará o mesmo critério de parada recente e movimento prévio já aplicado ao embarque/desembarque. Não haverá duas políticas de veículo parado.

Alternativa considerada: manter strings em `flags` e remover algumas delas. Foi rejeitada porque preservaria estado implícito e difícil de validar.

### 4. Sessões com evidência de identidade e estado temporal

A tabela `house_sessions` receberá, de forma aditiva, `entry_track_id`, `appearance_hist`, `last_seen_time` e metadados de correspondência. O campo `status` passará a distinguir `INSIDE`, `COMPLETED`, `UNCERTAIN` e `STALE`.

- Cruzamento ENTRY abre presença confirmada; o fallback por `ZONA_INTERNA` abre presença incerta ou associa um track compatível à sessão existente.
- Um novo track somente deduplica uma sessão se a aparência atingir o mesmo critério conservador da identificação.
- EXIT escolhe somente uma correspondência única e suficiente. Sem correspondência, o evento registra `session_id=NULL` e o motivo; nenhuma sessão é fechada por FIFO.
- `last_seen_time` é atualizado por evidência interna. Sessões sem confirmação há mais de `HOUSE_SESSION_STALE_HOURS=24` são expostas como `STALE`, sem apagar seu histórico.
- Consultas de relatório usam sobreposição temporal e recebem `ts_to`; presença histórica não consulta o estado atual.

Alternativa considerada: associar somente por `track_id`. Foi rejeitada porque ByteTrack pode trocar IDs; por isso o track é evidência adicional, não identidade durável.

### 5. Foto contextual e recorte de identidade separados

Eventos de pessoa poderão persistir dois caminhos: a foto contextual atual e um recorte derivado de `TrackState.best_crop`. O SQLite receberá o caminho do recorte de identidade. VLM e alertas continuam usando a foto contextual; cadastro e comparação usam exclusivamente o recorte.

`known_people` receberá `signature_source`. Assinaturas existentes serão marcadas como `full_frame` e ignoradas pelo matcher; novas assinaturas serão `person_crop`. Isso preserva o cadastro histórico sem continuar usando evidência inválida. O usuário poderá recadastrar uma pessoa a partir de um evento com recorte válido.

Além do limiar de similaridade, o matcher exigirá margem sobre a segunda correspondência. Os valores iniciais serão conservadores (`MATCH_THRESHOLD=0.95`, `MATCH_MARGIN=0.05`) e configuráveis; somente dados rotulados poderão justificar seu ajuste. Abaixo do limiar ou sem margem, o resultado será desconhecido/incerto.

Alternativa considerada: reutilizar histogramas existentes e apenas reduzir o limiar. Foi descartada porque o fundo da câmera contaminaria a calibração.

### 6. Migrações explícitas e testes focados

A migração verificará `PRAGMA table_info` e adicionará somente colunas ausentes. Erros diferentes de duplicação não serão engolidos. Não haverá reescrita de eventos nem remoção de tabelas.

`pytest` será adicionado somente ao grupo de desenvolvimento. Testes usarão SQLite temporário, tracks sintéticos e dobras de notifier/LLM. Os casos cobrirão cooldown com eventos contínuos, falha de snapshot, dwell descontínuo, veículo antigo, ordem de saída, troca de track, relatório truncado, orçamento do prompt e falha da CLI.

## Risks / Trade-offs

- [Mais resultados “desconhecido” ou sessões `UNCERTAIN`] → É preferível a identidades e permanências falsas; a foto e os metadados continuam disponíveis para revisão.
- [Histograma de roupa varia com iluminação] → Limiar e margem conservadores; não apresentar o resultado como reconhecimento facial.
- [Agregação reduz detalhes rotineiros no prompt] → Contagens exatas e eventos de segurança permanecem; o fallback sempre usa os dados completos.
- [Debounce em memória zera ao reiniciar] → Pode repetir um alerta após restart, comportamento mais seguro que suprimi-lo; persistência fica fora deste escopo.
- [Migração em banco ativo com WAL] → Implantar com serviço parado e backup consistente antes da alteração.
- [Recadastro necessário para assinaturas antigas] → Preservar nomes e eventos, mas ignorar histogramas `full_frame` até novo cadastro.

## Migration Plan

1. Parar o serviço e criar backup consistente do SQLite pela API de backup, incluindo o estado do WAL.
2. Aplicar as colunas aditivas e marcar assinaturas antigas como `full_frame`; sessões antigas sem evidência passam a ser tratadas conservadoramente.
3. Executar testes e gerar um relatório com `--no-send`; conferir quatro seções, contagens e sessões da janela.
4. Reiniciar o serviço e observar inicialização, alertas e logs de entrega.
5. Para rollback, parar o serviço, restaurar o backup e retornar à versão anterior. As colunas novas não exigem migração destrutiva reversa.

## Open Questions

Os limiares ideais de similaridade e margem dependem de recortes rotulados reais. Os defaults acima são conservadores; uma calibração posterior pode ajustá-los sem alterar specs, arquitetura ou tarefas.
