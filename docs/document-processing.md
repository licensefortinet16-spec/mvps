# Processamento de Documentos

Este documento descreve as regras atuais do backend para uploads, extracao, validacao de confianca e consolidacao financeira.

## Objetivo

O pipeline deve transformar documentos financeiros em dados revisaveis sem depender do navegador para regras criticas. A consolidacao automatica so deve acontecer quando o backend tiver sinais suficientes de qualidade e consistencia.

## Fluxo

1. O usuario envia o arquivo em `/uploads`.
2. O backend valida tipo, extensao, tamanho e hash de deduplicacao.
3. Para imagens, o backend avalia qualidade antes de extrair dados.
4. O backend executa OCR e/ou IA configurada por ambiente.
5. O documento e classificado como holerite, fatura, nota/comprovante ou outro.
6. Os dados extraidos sao normalizados.
7. O backend valida consistencia financeira, principalmente soma de itens versus total.
8. Se a confianca for suficiente, os lancamentos podem ser consolidados automaticamente.
9. Se houver divergencia ou baixa qualidade, o documento fica pendente de revisao/falha com mensagem explicita.

## Qualidade de Imagem

Antes de OCR/IA, imagens `jpg`, `jpeg`, `png` e `webp` passam por avaliacao de:

- resolucao minima;
- brilho minimo e maximo;
- contraste;
- nitidez/foco.

Se a imagem estiver pequena, escura, estourada, sem contraste ou borrada, o documento recebe:

- `status = failed`;
- `confidence = 0`;
- `extracted_data.error_code = poor_image_quality`;
- detalhes em `extracted_data.image_quality`.

Se a imagem passar nas metricas, mas nenhuma informacao financeira util for encontrada, o documento tambem falha com orientacao para reenviar uma foto melhor.

## Cupons, Notas e Descontos

Para notas e comprovantes, cada item pode conter:

- `gross_amount`: valor bruto do item;
- `discount_amount`: desconto positivo aplicado ao item;
- `net_amount`: valor final pago;
- `amount`: alias do valor final usado para criar o lancamento.

O `detected_total` representa o total final pago do documento, depois de descontos.

## Validacao de Totais

O backend soma os valores finais dos itens e compara com `detected_total`.

Se a diferenca for maior que a tolerancia tecnica, o documento recebe aviso `receipt_total_mismatch`, a confianca cai para no maximo `49%` e a consolidacao automatica e bloqueada.

Nesse caso, a tela de revisao mostra:

- soma dos itens;
- total detectado;
- diferenca;
- campos para ajustar valor bruto, desconto e valor final.

## Consolidacao Automatica

O sistema so consolida automaticamente notas/comprovantes quando:

- existe ao menos um item ou total detectado;
- a soma dos itens fecha com o total;
- nao ha aviso de divergencia financeira.

Holerites continuam gerando renda e descontos quando os campos principais sao extraidos com confianca suficiente.

## Testes

Os testes cobrem:

- isolamento multi-tenant;
- permissao por papel;
- deduplicacao de uploads;
- sanitizacao de nomes de arquivo;
- rejeicao de arquivos invalidos;
- bloqueio de revisao/retry/delete entre tenants;
- desconto por item em cupom;
- bloqueio de total divergente;
- falha por imagem ruim;
- rejeicao de extracao sem dados uteis.
