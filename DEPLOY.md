# Deploy no Railway — Projeto BE

## O que sobe para produção

API FastAPI (`app/`) com Selenium (Chromium) + ffmpeg, via `Dockerfile`.

Scripts locais (`baixa_img.py`, `baixa_audio.py`, `move_mouse.py`) **não** entram na imagem Docker. Use-os só na máquina local.

---

## Checklist antes do GitHub (segurança)

Nunca versionar:

| Item | Motivo |
|------|--------|
| `.env` | Credenciais BE, `SECRET_KEY`, senha admin |
| `downloads/` | Áudios/imagens baixados (~centenas de MB) |
| `app.db` / `*.db` | Banco com hash de usuários |
| `venv/` | Ambiente local |
| `NOVOSISTEMA/`, `backup_sistem/` | Cópias com `.env` e dados |
| `*.zip` | Pacotes gerados |

Arquivos seguros para subir:

- `app/`, `Dockerfile`, `docker-compose.yml`, `railway.toml`
- `requirements.txt`, `.env.example`, `.gitignore`, `.dockerignore`
- `DEPLOY.md`, `index.py` (legado)

Confira localmente (na pasta do projeto):

```bash
# Deve listar .env e downloads como ignorados
git status --ignored

# Nunca deve aparecer valor real de senha
git check-ignore -v .env downloads app.db
```

---

## Variáveis de ambiente no Railway

Configure em **Variables** (não committe valores reais):

| Variável | Obrigatória | Descrição |
|----------|-------------|-----------|
| `USER_BE` | sim | Usuário do sistema BE |
| `PASSWORD_BE` | sim | Senha do sistema BE |
| `URL_BE` | sim | URL de login do BE |
| `SECRET_KEY` | sim | Chave da sessão FastAPI (aleatória longa) |
| `ADMIN_USERNAME` | sim | Admin da 1ª inicialização |
| `ADMIN_PASSWORD` | sim | Senha do admin (forte) |
| `DB_PATH` | recomendado | Padrão na imagem: `/data/app.db` |
| `CHROME_BIN` | já no Dockerfile | `/usr/bin/chromium` |
| `CHROMEDRIVER_PATH` | já no Dockerfile | `/usr/bin/chromedriver` |
| `PORT` | Railway define | Não precisa setar manualmente |

Gerar `SECRET_KEY`:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Modelo local: copie `.env.example` → `.env` e preencha.

---

## Passo a passo Railway

1. Crie um repositório Git **só** com o conteúdo de `4.ProjetoBE` (esta pasta como raiz).
2. Confirme que `.env` e `downloads/` não entram no primeiro commit.
3. No [Railway](https://railway.app): **New Project** → **Deploy from GitHub**.
4. Selecione o repo. O `railway.toml` aponta para o `Dockerfile`.
5. Em **Variables**, cole as variáveis da tabela acima.
6. Crie um **Volume** e monte em `/data` (persiste o SQLite).
7. Opcional: segundo volume em `/app/downloads` (persiste zips/áudios entre deploys).
8. Em **Settings → Networking**, gere um domínio público.
9. Abra `https://seu-dominio/login` e entre com `ADMIN_USERNAME` / `ADMIN_PASSWORD`.

### Memória / Chromium

Selenium + Chromium pedem RAM. Se o container morrer com OOM:

- aumente o plano / memória do serviço;
- o código já usa `--disable-dev-shm-usage` e headless.

---

## Docker local (teste antes do Railway)

```bash
cp .env.example .env   # se ainda não tiver .env
# edite .env com valores reais

docker compose up --build
# http://localhost:8002/login
```

---

## Persistência importante

Sem volume no Railway, **`/data/app.db` e `downloads/` somem a cada redeploy**.

- Volume em `/data` → usuários/login persistem  
- Volume em `/app/downloads` → arquivos baixados persistem  

---

## Depois do primeiro deploy

1. Troque a senha admin se usou valor temporário.
2. Teste `/` (gravação) e `/baixar-audios`.
3. Não faça commit de `.env` nem de dumps do volume.
