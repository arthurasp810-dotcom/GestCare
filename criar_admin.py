import sqlite3, bcrypt, os

db = os.path.join('database', 'casa_idosos.db')
senha = bcrypt.hashpw('admin123'.encode(), bcrypt.gensalt()).decode()

conn = sqlite3.connect(db)
conn.execute(
    "INSERT INTO usuarios (nome, email, senha, perfil) VALUES (?, ?, ?, ?)",
    ('Administrador', 'admin@laridosos.com', senha, 'admin')
)
conn.commit()
conn.close()
print('Admin criado com sucesso!')