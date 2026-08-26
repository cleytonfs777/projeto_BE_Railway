"""Ponto de entrada legado — use a API FastAPI via Docker ou uvicorn."""

from app.services.gravacao import registrar_gravacao

if __name__ == "__main__":
    data_hora_inicial = "09/07/2026 11:15"
    data_hora_final = "09/07/2026 12:00"
    duracao_segundos = 600

    for msg in registrar_gravacao(data_hora_inicial, data_hora_final, duracao_segundos):
        print(msg)
