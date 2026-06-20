"""Roda a checagem de lembretes de antibiótico uma vez e termina.

No PythonAnywhere (plano Free), o app web não mantém uma thread de fundo
rodando o tempo todo, então usamos o recurso "Tasks" deles para chamar este
script 1x por dia (é o limite do plano Free). Configure em:
  Dashboard > Tasks > Scheduled tasks
  Comando: python3.10 /home/SEU_USUARIO/sistema_casa_idosos/casa_idosos/tarefa_lembretes.py
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app, init_db, checar_lembretes_antibiotico

if __name__ == '__main__':
    init_db()
    with app.app_context():
        checar_lembretes_antibiotico()
    print('Checagem de lembretes de antibiótico concluída.')
