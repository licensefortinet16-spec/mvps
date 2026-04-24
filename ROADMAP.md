# Roadmap - Sistema de Controle Financeiro

## Fase 0 - Alinhamento e Base Tecnica

Objetivo: estabelecer a fundacao para evolucao segura.

- Definir arquitetura monolitica inicial com `FastAPI`, `PostgreSQL` e frontend `Tailwind CSS`.
- Desenhar modelo multi-tenant com isolamento forte.
- Definir padrao de autenticacao, autorizacao e auditoria.
- Definir estrategia de upload e armazenamento de arquivos.
- Definir formato dos eventos de extracao e classificacao.
- Estruturar Docker unico e deploy base para Railway.

Entrega esperada:

- Documento de arquitetura.
- Modelo de dados inicial.
- Contrato das principais rotas da API.
- Estrutura de projeto pronta para implementacao.

## Fase 1 - MVP de Conta e Dashboard Manual

Objetivo: permitir uso manual antes da automacao de documentos.

- Cadastro manual e login com Google.
- Criacao de tenant e isolamento por usuario.
- Insercao manual de despesas e rendimentos.
- Cadastro de categorias.
- Cadastro de contas parceladas e financiamentos.
- Dashboard inicial com graficos basicos:
  - Fluxo mensal.
  - Despesas por categoria.
  - Renda x despesa.
  - Parcelamentos futuros.

Entrega esperada:

- Usuario consegue operar o sistema sem upload.
- Dados persistem por tenant.
- Admin ve apenas metricas agregadas.

## Fase 2 - Upload de Documentos e Extracao Basica

Objetivo: automatizar a leitura de holerites e despesas.

- Upload de holerites em PDF e imagem.
- Upload de faturas, extratos e notas.
- Pipeline de extracao para valores, datas, nomes e categorias.
- Validacao de qualidade de fotos antes de OCR/IA.
- Validacao de consistencia entre itens, descontos e total pago.
- Interface de revisao manual dos dados extraidos.
- Cadastro automatico de rendimentos e despesas a partir da extracao.

Entrega esperada:

- Documento enviado vira lancamento com revisao.
- Usuario consegue corrigir dados antes de salvar definitivamente.
- Documento com foto ruim ou total inconsistente nao vira lancamento automatico.

## Fase 3 - Parcelamentos, Financiamentos e Recorrencias

Objetivo: capturar compromissos futuros com mais precisao.

- Identificacao automatica de compras parceladas.
- Criacao automatica de parcelas futuras.
- Tela dedicada para parcelamentos e financiamentos.
- Identificacao de despesas recorrentes.
- Consolidacao de compromissos futuros no saldo projetado.

Entrega esperada:

- O sistema reconhece compras 12x, 10x etc. e agenda parcelas.
- Usuario acompanha passivos futuros em uma visao unica.

## Fase 4 - Previsoes e Inteligencia Financeira

Objetivo: gerar estimativas utilitarias para planejamento.

- Projecao de renda e despesa por meses futuros.
- Cenarios conservador, base e estressado.
- Consideracao de recorrencias, parcelas e historico recente.
- Indicacao de confianca da previsao.
- Alertas simples para meses com risco de saldo negativo.

Entrega esperada:

- Dashboard exibe estimativas compreensiveis.
- O usuario entende o impacto dos compromissos futuros.

## Fase 5 - Dashboard Administrativo e Observabilidade

Objetivo: suportar operacao da plataforma sem expor dados de clientes.

- Dashboard admin com metricas agregadas.
- Monitoramento de uploads, falhas e latencia.
- Auditoria de acessos e eventos relevantes.
- Visao de saude do sistema e uso geral.

Entrega esperada:

- Admin monitora a plataforma sem acessar dados sensiveis de clientes.

## Fase 6 - Preparacao para App Android

Objetivo: deixar o produto pronto para consumo mobile.

- Padronizar contratos de API.
- Garantir autenticacao tokenizada.
- Revisar responsividade total da interface.
- Documentar endpoints para app Android futuro.

Entrega esperada:

- Frontend responsivo pronto.
- Backend preparado para mobile sem refatoracao grande.

## Fase 7 - Hardenizacao e Escala

Objetivo: aumentar confiabilidade, seguranca e qualidade dos dados.

- Testes automatizados de seguranca multi-tenant.
- Testes de integridade de extracao.
- Testes para baixa qualidade de imagem, descontos em cupons e divergencia de totais.
- Controle de rate limit e upload.
- Otimizacao de performance e consultas.
- Plano de migracao caso o Railway free fique insuficiente.

Entrega esperada:

- Base pronta para crescimento sem comprometer isolamento.

## Priorizacao Recomendada para o MVP

1. Autenticacao e multi-tenant.
2. Lancamentos manuais.
3. Dashboard basico.
4. Upload e extracao de documentos.
5. Parcelamentos e financiamentos.
6. Previsoes.
7. Dashboard admin.

## Riscos Principais

- OCR inconsistente em documentos de baixa qualidade.
- Fotos com qualidade limiar podem exigir ajuste fino de thresholds de brilho, contraste e nitidez.
- Classificacao incorreta de categorias sem validacao manual.
- Limites do Railway free para processamento de arquivos.
- Vazamento de dados por falha de filtro tenant se a arquitetura nao for rigorosa.
- Escopo grande demais para uma primeira entrega sem MVP claro.

## Recomendacao Pratica

Comecar com um monolito bem estruturado, interfaces simples e processamento assincrono. O maior risco nao e tecnologia de frontend ou dashboard; e o controle de seguranca, extracao confiavel e isolamento real entre tenants.
