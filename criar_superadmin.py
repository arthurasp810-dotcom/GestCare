"""Cria o usuário superadmin — quem gerencia a lista de casas (instituições)
que usam o GestCare. Diferente do admin de cada casa: o superadmin não vê
pacientes, só cadastra/desativa casas em /admin/casas.

Execute uma vez: python criar_superadmin.py
"""
import sqlite3, bcrypt, os

base = os.path.dirname(os.path.abspath(__file__))
db = os.path.join(base, 'database', 'casa_idosos.db')
os.makedirs(os.path.dirname(db), exist_ok=True)

conn = sqlite3.connect(db)
conn.execute("PRAGMA foreign_keys = ON")

conn.execute('''
    CREATE TABLE IF NOT EXISTS usuarios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT NOT NULL,
        email TEXT NOT NULL UNIQUE,
        senha TEXT NOT NULL,
        perfil TEXT NOT NULL DEFAULT 'enfermeiro',
        paciente_id INTEGER,
        casa_id INTEGER,
        ativo INTEGER NOT NULL DEFAULT 1,
        criado_em TEXT DEFAULT (datetime('now'))
    )
''')

EMAIL = 'superadmin@gestcare.com'
SENHA = 'superadmin123'

senha_hash = bcrypt.hashpw(SENHA.encode(), bcrypt.gensalt()).decode()

try:
    conn.execute(
        "INSERT INTO usuarios (nome, email, senha, perfil, casa_id) VALUES (?, ?, ?, 'superadmin', NULL)",
        ('Super Admin', EMAIL, senha_hash)
    )
    conn.commit()
    print('=' * 50)
    print('Superadmin criado com sucesso!')
    print(f'Email: {EMAIL}')
    print(f'Senha: {SENHA}')
    print('IMPORTANTE: troque essa senha após o primeiro login.')
    print('=' * 50)
except sqlite3.IntegrityError:
    print('Superadmin já existe no banco de dados.')

conn.close()
