# SDD - Software Design Document

## 1. Objetivo

Descrever a solucao tecnica do sistema financeiro com extracao de documentos, multi-tenant, dashboards, previsoes e controle de parcelas, priorizando seguranca, isolamento de dados e evolucao futura para app Android.

## 2. Regra Principal do Sistema

A regra principal e: cada cliente so pode acessar e processar seus proprios dados, e toda decisao de negocio deve ser executada no backend.

Consequencias dessa regra:

- Nenhum processamento financeiro acontece no frontend.
- Nenhuma regra de extracao, classificacao, previsao ou consolidacao deve depender do navegador.
- Toda validacao de tenant, permissao e persistencia ocorre no backend.
- O frontend apenas exibe dados, coleta input e envia arquivos ou formularios.

## 3. Principios de Arquitetura

- Backend como fonte unica de verdade.
- Multi-tenant com isolamento rigoroso por usuario/tenant.
- Processamento assincrono para documentos pesados.
- Frontend responsivo, com foco em desktop e mobile.
- Estrutura monolitica inicial, com modulos internos bem separados.
- Preparacao para consumo futuro por app Android via API.

## 4. Stack Tecnologica

- Backend: `FastAPI`
- Banco de dados: `PostgreSQL`
- Frontend: `Tailwind CSS`
- Deploy: `Docker` unico
- Hospedagem inicial: `Railway` gratuito

## 5. Arquitetura de Alto Nivel

### 5.1 Camadas

- Apresentacao: telas responsivas, formulários, dashboards e revisao manual.
- API: autenticacao, autorizacao, regras de negocio, upload e consultas.
- Dominio: lancamentos, documentos, parcelas, previsoes e classificacao.
- Persistencia: modelos, consultas e isolamento por tenant.
- Processamento assicrono: OCR, extracao, classificacao e conciliacao.

### 5.2 Fluxo Geral

1. Usuario envia documento ou cria lancamento manualmente.
2. Frontend apenas transmite a requisicao.
3. Backend valida permissao, tenant e formato.
4. Arquivo e registrado para processamento.
5. Job de backend extrai e classifica os dados.
6. Usuario revisa resultado se necessario.
7. Dados consolidados alimentam graficos e previsoes.

## 6. Modulos Funcionais

### 6.1 Autenticacao e Acesso

- Cadastro manual.
- Login com Google.
- Recuperacao de senha.
- Controle de sessao/token.
- Controle de perfil: usuario e administrador.

### 6.2 Tenant e Isolamento

- Todo registro pertence a um tenant.
- Consultas sempre filtram tenant autenticado.
- Admin nao acessa dados financeiros de clientes.
- Logs e auditoria nao expõem conteudo sensivel.

### 6.3 Lancamentos Manuais

- Receita manual.
- Despesa manual.
- Parcelamento manual.
- Financiamento manual.
- Edicao, exclusao e reclassificacao.

### 6.4 Upload e Extracao

- Upload de holerite.
- Upload de fatura de cartao.
- Upload de notas e comprovantes.
- Extracao de valores, datas, descontos, categorias e parcelamentos.
- Revisao manual quando a confianca for baixa.

### 6.5 Parcelamentos e Financiamentos

- Identificacao automatica de compras parceladas.
- Criacao de parcelas futuras.
- Controle de status pago, pendente e vencido.
- Consolidacao no fluxo de caixa mensal.

### 6.6 Dashboards

- Resumo mensal.
- Renda x despesa.
- Despesas por categoria.
- Descontos do holerite.
- Parcelamentos futuros.
- Tendencia historica.

### 6.7 Previsoes

- Estimativa dos proximos meses.
- Uso de media historica, recorrencias e parcelas futuras.
- Indicador de confianca.
- Cenarios base, conservador e estressado.

### 6.8 Admin da Plataforma

- Metricas agregadas de uso.
- Volume de uploads.
- Tempo de processamento.
- Falhas de extracao.
- Saude do sistema.
- Sem acesso a dados de clientes.

## 7. Requisito de Processamento Somente no Backend

Todo processamento de negocio e documento deve ocorrer no backend.

Inclui:

- Validacao de arquivos.
- Leitura e parsing de documentos.
- OCR, quando necessario.
- Extracao de campos.
- Classificacao de categorias.
- Detecao de parcelamento.
- Calculo de totais.
- Consolidacao mensal.
- Projecao de previsoes.

Nao inclui no frontend:

- OCR no navegador.
- Regras de classificacao local.
- Calculo de previsoes.
- Persistencia direta sem validacao.
- Logica de segregacao de tenant.

## 8. Requisito de Responsividade Total

O sistema deve ser totalmente responsivo.

Definicao:

- Interface deve funcionar em desktop, tablet e mobile.
- Nao pode quebrar layout em resolucoes pequenas.
- Tabelas devem ter estrategias de scroll, cards ou colapso em mobile.
- Formularios devem ser usaveis por toque.
- Dashboards devem manter legibilidade em telas pequenas.
- Fluxos principais devem ser concluiveis sem depender de desktop.

## 9. Modelo de Dados Sugerido

- `User`
- `Tenant`
- `Document`
- `ExtractionResult`
- `FinancialEntry`
- `Category`
- `RecurringExpense`
- `InstallmentPlan`
- `Installment`
- `Forecast`
- `AuditEvent`
- `PlatformMetric`

## 10. Processamento de Documentos

### 10.1 Pipeline

- Upload.
- Registro do documento.
- Validacao tecnica do arquivo.
- Enfileiramento para processamento.
- Deduplicacao por hash para impedir multiplos registros quando o usuario clica repetidamente no envio.
- Extracao de texto e campos.
- Classificacao e normalizacao.
- Persistencia dos resultados.
- Revisao humana opcional.

### 10.2 Regras

- Se a extracao falhar, o documento permanece revisavel.
- Se a confianca for baixa, o sistema nao consolida automaticamente sem confirmacao.
- Se o documento sugerir parcelamento, o backend cria ou sugere o plano correspondente.

## 11. Regras de Seguranca

- Isolamento por tenant em todas as queries.
- Validacao de permissao em toda rota sensivel.
- Storage separado por tenant ou por prefixo fortemente segregado.
- Logs sem dados financeiros brutos.
- Auditoria de acesso e mutacao.
- Admin restrito a metricas agregadas.

## 12. API e Integracao Futura

- API desenhada para consumo por frontend web e futuro app Android.
- Contratos previsiveis e versionados.
- Respostas consistentes para extracao, dashboard e previsao.
- Frontend nunca executa regras criticas, apenas renderiza e coleta dados.

## 13. Deploy e Operacao

- Aplicacao empacotada em um Docker unico.
- Banco PostgreSQL externo ao container da aplicacao.
- Processamento assincrono com estrategia leve adequada ao Railway free.
- Observabilidade basica com logs e metricas agregadas.

## 14. Entregaveis do MVP

- Autenticacao manual e Google.
- Multi-tenant funcional.
- Lancamentos manuais.
- Upload e extracao inicial de documentos.
- Dashboards responsivos.
- Parcelamentos e financiamentos.
- Previsoes basicas.
- Admin apenas com metricas agregadas.

## 15. Critério de Aceite

O sistema so pode ser considerado pronto quando:

- Toda regra de negocio estiver no backend.
- Nenhum dado de um cliente aparecer para outro.
- O frontend estiver responsivo em telas pequenas e grandes.
- Uploads e extracoes funcionarem sem processamento no navegador.
- O admin conseguir ver apenas metricas operacionais.
