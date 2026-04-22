# Financa

MVP inicial de um sistema financeiro multi-tenant com:

- Cadastro manual e login com Google configuravel por ambiente.
- Isolamento por tenant no backend.
- Lancamentos manuais.
- Upload de documentos com processamento no backend.
- Parcelamentos e previsoes basicas.
- Dashboard responsivo.
- Area admin com metricas agregadas, sem dados de clientes.

## Stack

- `FastAPI`
- `PostgreSQL`
- `Tailwind CSS` via CDN
- `Docker` + `docker compose`

## Subir localmente

```bash
docker compose up --build
```

Aplicacao:

- `http://localhost:8000`

## Credenciais admin padrao

- E-mail: `admin@local.test`
- Senha: `admin123`

## Fluxos para testar

1. Criar uma conta em `/register`.
2. Inserir um lancamento manual em `/entries/new`.
3. Criar um parcelamento em `/entries/plans/new`.
4. Enviar um documento em `/uploads`.
5. Validar dashboard em `/`.
6. Entrar como admin e abrir `/admin`.

## Uploads testaveis agora

- PDF com texto extraivel.
- Imagem suportada pelo `Pillow`.
- Arquivos `txt` e `csv`.

## Google Login

Para habilitar login com Google, preencher no `.env`:

- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`

## Railway

Para subir no Railway:

1. Conectar o repositorio GitHub ao projeto.
2. Criar um servico `PostgreSQL` no mesmo projeto.
3. Criar um volume e montar em `/data/uploads`.
4. Configurar as variaveis:
   - `APP_ENV=production`
   - `SECRET_KEY=<valor forte>`
   - `DATABASE_URL=<fornecida pelo Railway>`
   - `GOOGLE_CLIENT_ID=<oauth client id>`
   - `GOOGLE_CLIENT_SECRET=<oauth client secret>`
   - `ADMIN_EMAIL=<admin>`
   - `ADMIN_PASSWORD=<senha forte>`
   - `ADMIN_NAME=<nome>`
   - `UPLOAD_DIR=/data/uploads`
5. Depois de gerar o dominio publico do Railway, cadastrar no Google OAuth:
   - `https://SEU-DOMINIO/auth/google/callback`

## Observacoes

- O processamento de documentos acontece apenas no backend.
- O admin nao acessa dados financeiros por tenant.
- O parser atual e MVP e usa heuristicas simples para holerites e parcelamentos.
