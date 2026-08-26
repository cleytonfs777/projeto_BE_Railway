"""
Move o mouse em intervalos regulares para evitar bloqueio de tela.

Uso:
    python move_mouse.py
    python move_mouse.py --intervalo 30
    python move_mouse.py --intervalo 90 --pixels 20

Pare com Ctrl+C ou levando o cursor para um canto da tela (failsafe).
"""

import argparse
import time
from datetime import datetime

import pyautogui


pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.1


def mover_mouse(pixels: int) -> None:
    x, y = pyautogui.position()
    pyautogui.moveRel(pixels, 0, duration=0.2)
    pyautogui.moveRel(-pixels, 0, duration=0.2)
    print(
        f"[{datetime.now().strftime('%H:%M:%S')}] "
        f"Mouse movido em ({x}, {y})"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Move o mouse periodicamente para manter a sessão ativa."
    )
    parser.add_argument(
        "--intervalo",
        type=float,
        default=60,
        help="Segundos entre cada movimento (padrão: 60)",
    )
    parser.add_argument(
        "--pixels",
        type=int,
        default=8,
        help="Quantos pixels deslocar e voltar (padrão: 8)",
    )
    args = parser.parse_args()

    if args.intervalo <= 0:
        parser.error("--intervalo deve ser maior que 0")
    if args.pixels <= 0:
        parser.error("--pixels deve ser maior que 0")

    print(
        f"Movendo o mouse a cada {args.intervalo:.0f}s "
        f"({args.pixels} px). Ctrl+C para sair."
    )
    try:
        while True:
            mover_mouse(args.pixels)
            time.sleep(args.intervalo)
    except KeyboardInterrupt:
        print("\nEncerrado.")
    except pyautogui.FailSafeException:
        print("\nFailsafe: cursor no canto da tela. Encerrado.")


if __name__ == "__main__":
    main()
