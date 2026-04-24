# PRD - Sistema de Controle Financeiro com Extração de Documentos

## 1. Visao do Produto

Construir uma plataforma financeira multi-tenant para uso individual e futuro consumo via app Android, capaz de:

- Receber upload de holerites, faturas de cartao, notas fiscais e outros comprovantes.
- Extrair valores, descontos, rendimentos, categorias de despesas e informacoes de parcelamento.
- Permitir lancamentos manuais de despesas e rendimentos.
- Consolidar dados em dashboards com graficos de renda, gastos, descontos, parcelamentos e previsoes.
- Gerar estimativas de meses futuros com base no historico.
- Separar completamente os dados por cliente, sem risco de vazamento entre tenants.
- Expor uma area administrativa apenas para metricas de uso da plataforma, sem acesso a dados financeiros de clientes.

## 2. Objetivos

- Automatizar a leitura de documentos financeiros.
- Reduzir entrada manual de dados.
- Centralizar fluxo de caixa, despesas recorrentes, parcelamentos e previsao financeira.
- Disponibilizar base tecnica segura e pronta para evoluir para app Android.
- Ser implantavel em Docker unico e viavel para Railway gratuito no inicio.

## 3. Problema a Resolver

Hoje o usuario precisa acompanhar manualmente holerites, faturas, notas, despesas e compromissos futuros. Isso gera:

- Falta de visibilidade do saldo real mensal.
- Erros de digitacao e esquecimento de parcelas.
- Dificuldade de prever meses com aperto financeiro.
- Baixa capacidade de consolidar dados vindos de fontes diferentes.

## 4. Publico-Alvo

- Usuario final pessoa fisica que quer controlar renda e despesas.
- Usuario com renda fixa, variavel ou mista.
- Usuario com cartao de credito, parcelamentos e contas recorrentes.
- Administrador da plataforma, com acesso apenas a metricas operacionais.

## 5. Escopo Funcional

### 5.1 Cadastro e Autenticacao

- Cadastro manual por e-mail e senha.
- Login com Google.
- Recuperacao de senha.
- Sessao segura com refresh token ou equivalente.
- Estrutura pronta para multiplos usuarios e tenants.

### 5.2 Gestao de Tenant e Privacidade

- Cada cliente acessa apenas seus proprios dados.
- Isolamento por tenant em todas as camadas: API, banco, consultas, armazenamento e logs.
- Administrador visualiza apenas metricas agregadas de uso, como numero de uploads, numero de usuarios ativos, tempo de processamento e volume de documentos, sem acesso a conteudo financeiro individual.

### 5.3 Upload e Processamento de Documentos

- Upload de holerite em PDF, imagem ou formatos suportados.
- Upload de fatura de cartao, extrato, notas e comprovantes.
- Validacao de qualidade para fotos antes de OCR ou IA, incluindo nitidez, brilho, contraste e resolucao.
- Processamento automatico para extrair:
  - Rendimentos brutos e liquidos.
  - Descontos obrigatorios e opcionais.
  - Beneficios.
  - Despesas por categoria.
  - Nome do estabelecimento.
  - Data da compra.
  - Valor bruto, desconto e valor final por item quando o documento apresentar promocao ou abatimento.
  - Valor total final pago.
  - Quantidade de parcelas e valor por parcela.
  - Referencia de competencia ou fechamento, quando existir.
- Confirmacao e revisao manual antes da consolidacao quando houver baixa confianca, foto ruim ou divergencia entre soma dos itens e total pago.

### 5.4 Classificacao de Lancamentos

- Classificacao automatica em categorias como:
  - Alimentacao.
  - Mercado.
  - Transporte.
  - Moradia.
  - Saude.
  - Lazer.
  - Educacao.
  - Assinaturas.
  - Impostos.
  - Cartao de credito.
  - Parcelamentos.
  - Renda.
- Possibilidade de reclassificacao manual.
- Aprendizado por regra local do usuario, sem compartilhar dados entre tenants.

### 5.5 Lancamentos Manuais

- Criacao manual de rendimentos.
- Criacao manual de despesas.
- Criacao manual de contas parceladas e financiamentos.
- Edição, exclusão e ajuste retroativo de lançamentos.

### 5.6 Parcelamentos e Financiamentos

- Tela propria para acompanhar compras parceladas e financiamentos.
- Identificacao automatica de parcelamento no upload da fatura, por exemplo 12x.
- Criacao automatica do compromisso futuro com parcelas previstas.
- Controle de parcelas pagas, pendentes e vencimento.
- Consolidacao dessas parcelas no fluxo mensal.

### 5.7 Dashboards e Graficos

- Graficos de renda mensal.
- Graficos de despesas por categoria.
- Graficos de descontos do holerite.
- Graficos de fluxo de caixa.
- Graficos de cartao de credito.
- Graficos de parcelamentos futuros.
- Resumo do mes atual e comparativo com meses anteriores.

### 5.8 Estimativas e Previsoes

- Projecao de gastos e saldo para os proximos meses.
- Estimativas baseadas em:
  - Historico de renda.
  - Historico de despesas.
  - Despesas recorrentes.
  - Parcelamentos ativos.
  - Sazonalidade simples.
- Indicar cenarios:
  - Conservador.
  - Base.
  - Estressado.

### 5.9 Admin da Plataforma

- Dashboard administrativo com:
  - Numero de usuarios.
  - Numero de tenants.
  - Volume de uploads.
  - Processamentos por tipo de documento.
  - Falhas de extracao.
  - Tempo medio de processamento.
  - Status do sistema.
- Proibido acesso a dados financeiros individualizados.

### 5.10 Preparacao para App Android

- API com contrato consistente.
- Autenticacao tokenizada.
- Endpoints prontos para consumo por mobile.
- UI responsiva em desktop e mobile.

## 6. Requisitos Nao Funcionais

- Backend em `FastAPI`.
- Banco de dados `PostgreSQL`.
- Frontend em `Tailwind CSS`.
- Empacotamento em `Docker` unico.
- Sistema responsivo.
- Logs estruturados e auditaveis.
- Controle de acesso por perfil e por tenant.
- Alta confiabilidade para evitar vazamento de dados.
- Processamento assincrono para arquivos pesados.
- Tolerancia a falhas no pipeline de extracao.

## 7. Regras de Seguranca e Isolamento

- Toda consulta deve filtrar pelo tenant autenticado.
- Admin nao pode consultar tabelas de negocio de clientes.
- Documentos enviados devem ser armazenados com chaves separadas por tenant.
- Logs nao devem expor dados sensiveis.
- Os modelos de extracao nao devem ser treinados com dados de um cliente para outro sem consentimento e sem um pipeline aprovado.
- Todos os eventos de acesso a dados devem ser auditaveis.

## 8. Fluxo de Usuario

1. Usuario cria conta manualmente ou entra com Google.
2. Usuario acessa a dashboard.
3. Usuario faz upload de holerite, fatura ou comprovante.
4. Sistema extrai e sugere os dados encontrados.
5. Usuario confirma ou corrige a classificacao.
6. Dados entram na base consolidada.
7. Dashboard e previsoes sao atualizadas.
8. Usuario pode adicionar despesas e rendimentos manualmente a qualquer momento.

## 9. Modelos de Dados Principais

- Usuario.
- Tenant.
- Documento.
- Extracao.
- LancamentoFinanceiro.
- CategoriaFinanceira.
- DespesaRecorrente.
- Parcela.
- Financiamento.
- CompetenciaMensal.
- EventoDeAuditoria.
- MetricasDaPlataforma.

## 10. Requisitos de Extracao

### Holerite

- Nome do colaborador.
- Empresa.
- Competencia.
- Salario bruto.
- Descontos.
- INSS.
- IRRF.
- VT.
- VR.
- Outros descontos.
- Salario liquido.

### Fatura e Extrato

- Estabelecimento.
- Data.
- Valor final pago.
- Valor bruto e desconto por item quando disponivel.
- Categoria sugerida.
- Numero de parcelas.
- Valor de cada parcela.
- Bandeira ou identificador do cartao, se houver.

### Notas e Comprovantes

- Valor.
- Data.
- Categoria.
- Fornecedor.
- Indicio de recorrencia, quando aplicavel.

## 11. Requisitos de Previsao

- Base minima de previsao com historico de 3 a 6 meses.
- Se houver pouco dado, o sistema deve informar baixa confianca.
- O calculo deve considerar:
  - Media movel.
  - Recorrencias.
  - Parcelamentos futuros.
  - Sazonalidade simples.

## 12. Fora de Escopo Inicial

- Integracao bancaria Open Finance.
- Sincronizacao automatica com conta corrente.
- OCR offline no dispositivo.
- App Android nativo na primeira entrega.
- Multi-empresa com estrutura contabil completa.
- Inteligencia financeira avançada com recomendacao de investimento.

## 13. Premissas e Restrições

- O Railway free pode impor limites de CPU, memoria e execucao continua, entao o MVP deve priorizar:
  - Processamento assincrono leve.
  - Arquitetura simples.
  - Baixo custo operacional.
- A extracao de documentos pode exigir OCR e parsing hibrido.
- Parte da classificacao inicial pode depender de regra + ML leve.

## 14. Indicadores de Sucesso

- Percentual de documentos extraidos com sucesso.
- Percentual de documentos rejeitados por baixa qualidade de imagem.
- Percentual de documentos bloqueados por divergencia entre soma de itens e total.
- Percentual de extracoes corrigidas manualmente.
- Tempo medio para processar um documento.
- Numero de usuarios ativos mensais.
- Taxa de uso do upload.
- Precisao da classificacao de categorias.
- Quantidade de usuarios que acompanham previsoes mensalmente.

## 15. Critérios de Aceite do MVP

- Usuario consegue se cadastrar manualmente e com Google.
- Usuario consegue enviar holerite, fatura e nota.
- Sistema extrai valores principais e cria lancamentos.
- Sistema nao consolida automaticamente nota/comprovante quando os totais nao fecham.
- Sistema informa quando uma foto esta ruim demais para extracao confiavel.
- Usuario consegue incluir despesas e rendimentos manualmente.
- Dashboard exibe graficos basicos e resumo mensal.
- Sistema identifica parcelas e cria compromissos futuros.
- Sistema gera previsao simples para os proximos meses.
- Dados de um tenant nao aparecem para outro.
- Admin ve apenas metricas agregadas da plataforma.
- Aplicacao sobe via Docker unico com `FastAPI`, `PostgreSQL` e frontend responsivo.

## 16. Observacoes de Produto

O produto deve ser desenhado com priorizacao forte em privacidade e confiabilidade. Se houver qualquer duvida sobre isolamento entre clientes, a funcionalidade deve falhar fechado e nunca expor dados.
