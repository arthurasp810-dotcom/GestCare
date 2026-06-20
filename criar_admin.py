import sqlite3, bcrypt, os, sys

# Caminho absoluto baseado na localizacao do script
base = os.path.dirname(os.path.abspath(__file__))
db = os.path.join(base, 'database', 'casa_idosos.db')

os.makedirs(os.path.dirname(db), exist_ok=True)

conn = sqlite3.connect(db)
conn.execute("PRAGMA foreign_keys = ON")

# Cria tabela se nao existir
conn.execute('''
    CREATE TABLE IF NOT EXISTS usuarios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT NOT NULL,
        email TEXT NOT NULL UNIQUE,
        senha TEXT NOT NULL,
        perfil TEXT NOT NULL DEFAULT 'enfermeiro',
        paciente_id INTEGER,
        ativo INTEGER NOT NULL DEFAULT 1,
        criado_em TEXT DEFAULT (datetime('now'))
    )
''')

senha = bcrypt.hashpw('admin123'.encode(), bcrypt.gensalt()).decode()

try:
    conn.execute(
        "INSERT INTO usuarios (nome, email, senha, perfil) VALUES (?, ?, ?, ?)",
        ('Administrador', 'admin@laridosos.com', senha, 'admin')
    )
    conn.commit()
    print('=' * 40)
    print('Admin criado com sucesso!')
    print('Email: admin@laridosos.com')
    print('Senha: admin123')
    print('=' * 40)
except sqlite3.IntegrityError:
    print('Admin ja existe no banco de dados.')

conn.close()
