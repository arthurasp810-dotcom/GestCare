"""
Servidor de produção — usa waitress (estável, sem modo debug).
Execute este arquivo para rodar o sistema em produção.
"""
import os, sys
# Garante que o diretório do servidor seja o diretório de trabalho
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from waitress import serve
from app import app, init_db, iniciar_agendador

if __name__ == '__main__':
    init_db()
    iniciar_agendador()
    print("=" * 60)
    print("  GestCare — Sistema de Gestão da Casa de Idosos")
    print("=" * 60)
    print("  Servidor iniciado com sucesso!")
    print("  Acesse pelo navegador: http://localhost:5000")
    print("  Na rede local:         http://<IP-do-computador>:5000")
    print("  Para parar o servidor: feche esta janela")
    print("=" * 60)
    serve(app, host='0.0.0.0', port=5000, threads=4)
