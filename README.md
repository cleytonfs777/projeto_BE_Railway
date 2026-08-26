# Projeto BE 2.0

API FastAPI para registro de gravação e download de áudios/imagens via Selenium no sistema BE.

## Início rápido (local)

```bash
cp .env.example .env
# preencha USER_BE, PASSWORD_BE, URL_BE, SECRET_KEY, ADMIN_USERNAME, ADMIN_PASSWORD

python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Ou com Docker:

```bash
docker compose up --build
# http://localhost:8002/login
```

## Produção (Railway)

Siga o guia completo em [DEPLOY.md](./DEPLOY.md).

## Scripts locais (fora do Docker)

- `baixa_img.py` / `baixa_audio.py` — automação standalone
- `move_mouse.py` — evita bloqueio de tela (só desktop local)
