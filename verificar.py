import sqlite3, os

db = os.path.join('database', 'casa_idosos.db')
conn = sqlite3.connect(db)

tabelas = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
print("Tabelas no banco:", [t[0] for t in tabelas])

try:
    usuarios = conn.execute("SELECT id, nome, email, perfil FROM usuarios").fetchall()
    print("Usuários encontrados:", len(usuarios))
    for u in usuarios:
        print(f"  - {u[1]} | {u[2]} | {u[3]}")
except Exception as e:
    print("Erro:", e)

conn.close()