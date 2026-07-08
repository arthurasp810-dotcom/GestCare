import bcrypt
import uuid
import json
import base64
import atexit
import re
import unicodedata
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, session, send_from_directory, jsonify
from werkzeug.utils import secure_filename
import sqlite3
import os
from datetime import datetime, date, timedelta
from contextlib import contextmanager
from py_vapid import Vapid01 as Vapid
from pywebpush import webpush, WebPushException
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)
app.secret_key = os.environ.get('GESTCARE_SECRET_KEY', 'casa-idosos-sistema-2026-troque-em-producao')


@app.context_processor
def injetar_versao_assets():
    """Versão baseada no mtime do CSS, usada como ?v= para invalidar cache do navegador
    sempre que o arquivo mudar (evita que usuários fiquem com estilo antigo em cache)."""
    try:
        css_path = os.path.join(app.static_folder, 'css', 'style.css')
        versao_assets = str(int(os.path.getmtime(css_path)))
    except OSError:
        versao_assets = '1'
    return dict(versao_assets=versao_assets)

DB_PATH = os.path.join(os.path.dirname(__file__), 'database', 'casa_idosos.db')
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'static', 'uploads', 'exames')
EXTENSOES_PERMITIDAS = {'pdf', 'png', 'jpg', 'jpeg', 'webp'}

# ============================================================================
# NOTIFICAÇÕES PUSH (lembrete de antibiótico no celular das enfermeiras)
# ============================================================================
VAPID_KEY_PATH = os.path.join(os.path.dirname(__file__), 'vapid_private_key.pem')
VAPID_CLAIMS_SUB = 'mailto:contato@gestcare.local'


def _b64url(dados_bytes):
    return base64.urlsafe_b64encode(dados_bytes).rstrip(b'=').decode('ascii')


def _obter_chave_publica_vapid():
    vapid = Vapid.from_file(VAPID_KEY_PATH)
    raw = vapid.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    return _b64url(raw)


VAPID_PUBLIC_KEY = _obter_chave_publica_vapid()


def enviar_push_para_equipe(casa_id, titulo, corpo, url='/'):
    """Envia uma notificação push para a equipe (enfermeiros/admin) de uma casa específica."""
    payload = json.dumps({'title': titulo, 'body': corpo, 'url': url})
    with get_db() as conn:
        inscricoes = conn.execute('''
            SELECT ps.id, ps.endpoint, ps.p256dh, ps.auth
            FROM push_subscriptions ps
            JOIN usuarios u ON ps.usuario_id = u.id
            WHERE u.ativo = 1 AND u.perfil IN ('enfermeiro', 'admin') AND u.casa_id = ?
        ''', (casa_id,)).fetchall()
        for inscricao in inscricoes:
            try:
                webpush(
                    subscription_info={
                        'endpoint': inscricao['endpoint'],
                        'keys': {'p256dh': inscricao['p256dh'], 'auth': inscricao['auth']}
                    },
                    data=payload,
                    vapid_private_key=VAPID_KEY_PATH,
                    vapid_claims={'sub': VAPID_CLAIMS_SUB}
                )
            except WebPushException as exc:
                status = exc.response.status_code if exc.response is not None else None
                if status in (404, 410):
                    conn.execute('DELETE FROM push_subscriptions WHERE id = ?', (inscricao['id'],))


def checar_lembretes_antibiotico():
    """Job periódico: avisa a equipe quando um tratamento com antibiótico
    está terminando (em até 2 dias) ou já terminou, uma vez por dia."""
    hoje = date.today().isoformat()
    with app.app_context(), get_db() as conn:
        medicamentos = conn.execute('''
            SELECT m.id, m.nome, m.data_fim_tratamento, m.ultimo_alerta_enviado,
                   p.id as paciente_id, p.nome as paciente_nome, p.casa_id
            FROM medicamentos m
            JOIN pacientes p ON m.paciente_id = p.id
            WHERE m.ativo = 1 AND p.ativo = 1 AND m.eh_antibiotico = 1
              AND m.data_fim_tratamento IS NOT NULL
              AND date(m.data_fim_tratamento) <= date('now', 'localtime', '+2 days')
        ''').fetchall()
        for m in medicamentos:
            if m['ultimo_alerta_enviado'] == hoje:
                continue
            try:
                fim = datetime.strptime(m['data_fim_tratamento'], '%Y-%m-%d').date()
            except ValueError:
                continue
            dias = (fim - date.today()).days
            if dias < 0:
                corpo = f"Tratamento com {m['nome']} encerrado há {-dias} dia(s)."
            elif dias == 0:
                corpo = f"Tratamento com {m['nome']} termina hoje."
            else:
                corpo = f"Tratamento com {m['nome']} termina em {dias} dia(s)."
            enviar_push_para_equipe(
                m['casa_id'],
                f"💊 Antibiótico — {m['paciente_nome']}",
                corpo,
                url_for('detalhe_paciente', paciente_id=m['paciente_id'])
            )
            conn.execute(
                'UPDATE medicamentos SET ultimo_alerta_enviado = ? WHERE id = ?',
                (hoje, m['id'])
            )


_agendador = None


def iniciar_agendador():
    """Inicia o agendador em thread de fundo (a cada 30 min).
    No PythonAnywhere isso é desnecessário/instável — lá usamos o recurso
    'Scheduled Tasks' deles chamando tarefa_lembretes.py (veja esse arquivo)."""
    global _agendador
    if _agendador is not None:
        return
    if os.environ.get('PYTHONANYWHERE_DOMAIN'):
        return
    _agendador = BackgroundScheduler(daemon=True)
    _agendador.add_job(checar_lembretes_antibiotico, 'interval', minutes=30,
                        next_run_time=datetime.now())
    _agendador.start()
    atexit.register(lambda: _agendador.shutdown(wait=False))

@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    with get_db() as conn:
        cursor = conn.cursor()

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS casas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                slug TEXT,
                ativo INTEGER DEFAULT 1,
                data_cadastro TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # Migration: add slug column if it doesn't exist yet (without UNIQUE — SQLite limitation)
        colunas = [r[1] for r in cursor.execute("PRAGMA table_info(casas)").fetchall()]
        if 'slug' not in colunas:
            cursor.execute('ALTER TABLE casas ADD COLUMN slug TEXT')
        # Populate slugs for existing casas that have none
        sem_slug = conn.execute('SELECT id, nome FROM casas WHERE slug IS NULL OR slug = ""').fetchall()
        for row in sem_slug:
            base = gerar_slug(row['nome'])
            slug = slug_unico(conn, base)
            conn.execute('UPDATE casas SET slug = ? WHERE id = ?', (slug, row['id']))

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS pacientes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                data_nascimento DATE NOT NULL,
                sexo TEXT, cpf TEXT, rg TEXT, telefone TEXT, quarto TEXT,
                contato_emergencia_nome TEXT, contato_emergencia_telefone TEXT,
                contato_emergencia_parentesco TEXT, observacoes TEXT,
                data_cadastro TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                ativo INTEGER DEFAULT 1
            )
        ''')

        try:
            cursor.execute('ALTER TABLE pacientes ADD COLUMN casa_id INTEGER REFERENCES casas(id)')
        except Exception:
            pass

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS condicoes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                paciente_id INTEGER NOT NULL, nome TEXT NOT NULL,
                descricao TEXT, gravidade TEXT, data_diagnostico DATE,
                data_cadastro TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (paciente_id) REFERENCES pacientes(id) ON DELETE CASCADE
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS medicamentos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                paciente_id INTEGER NOT NULL, nome TEXT NOT NULL,
                dosagem TEXT, via_administracao TEXT, instrucoes TEXT,
                ativo INTEGER DEFAULT 1,
                data_cadastro TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (paciente_id) REFERENCES pacientes(id) ON DELETE CASCADE
            )
        ''')

        try:
            cursor.execute('ALTER TABLE medicamentos ADD COLUMN eh_antibiotico INTEGER DEFAULT 0')
        except Exception:
            pass
        try:
            cursor.execute('ALTER TABLE medicamentos ADD COLUMN data_fim_tratamento DATE')
        except Exception:
            pass
        try:
            cursor.execute('ALTER TABLE medicamentos ADD COLUMN ultimo_alerta_enviado DATE')
        except Exception:
            pass

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS push_subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario_id INTEGER NOT NULL,
                endpoint TEXT NOT NULL UNIQUE,
                p256dh TEXT NOT NULL,
                auth TEXT NOT NULL,
                criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (usuario_id) REFERENCES usuarios(id) ON DELETE CASCADE
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS horarios_medicamento (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                medicamento_id INTEGER NOT NULL, horario TIME NOT NULL,
                FOREIGN KEY (medicamento_id) REFERENCES medicamentos(id) ON DELETE CASCADE
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS doses_administradas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                medicamento_id INTEGER NOT NULL,
                horario_previsto TIMESTAMP NOT NULL,
                horario_administrado TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                administrado_por TEXT, observacoes TEXT,
                FOREIGN KEY (medicamento_id) REFERENCES medicamentos(id) ON DELETE CASCADE
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS usuarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL, email TEXT NOT NULL UNIQUE,
                senha TEXT NOT NULL, perfil TEXT NOT NULL DEFAULT 'enfermeiro',
                paciente_id INTEGER,
                ativo INTEGER NOT NULL DEFAULT 1,
                criado_em TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (paciente_id) REFERENCES pacientes(id)
            )
        ''')

        # Adiciona coluna paciente_id se a tabela já existia sem ela
        try:
            cursor.execute('ALTER TABLE usuarios ADD COLUMN paciente_id INTEGER')
        except Exception:
            pass
        try:
            cursor.execute('ALTER TABLE usuarios ADD COLUMN cpf TEXT')
        except Exception:
            pass
        try:
            cursor.execute('ALTER TABLE usuarios ADD COLUMN casa_id INTEGER REFERENCES casas(id)')
        except Exception:
            pass

        # Migração: se já existem pacientes/usuários sem casa (banco anterior ao
        # multi-tenant), cria uma "Casa Padrão" e vincula todos os registros a ela.
        sem_casa = cursor.execute(
            "SELECT COUNT(*) as total FROM pacientes WHERE casa_id IS NULL"
        ).fetchone()['total']
        sem_casa += cursor.execute(
            "SELECT COUNT(*) as total FROM usuarios WHERE casa_id IS NULL AND perfil != 'superadmin'"
        ).fetchone()['total']
        if sem_casa > 0:
            casa_padrao = cursor.execute(
                "SELECT id FROM casas ORDER BY id LIMIT 1"
            ).fetchone()
            if casa_padrao:
                casa_padrao_id = casa_padrao['id']
            else:
                casa_padrao_id = cursor.execute(
                    "INSERT INTO casas (nome) VALUES ('Casa Padrão')"
                ).lastrowid
            cursor.execute(
                'UPDATE pacientes SET casa_id = ? WHERE casa_id IS NULL', (casa_padrao_id,)
            )
            cursor.execute(
                "UPDATE usuarios SET casa_id = ? WHERE casa_id IS NULL AND perfil != 'superadmin'",
                (casa_padrao_id,)
            )

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ficha_psicologica (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                paciente_id INTEGER NOT NULL UNIQUE,
                antecedentes TEXT, diagnosticos_mentais TEXT,
                medicacao_psiquiatrica TEXT, comportamento TEXT, observacoes TEXT,
                atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (paciente_id) REFERENCES pacientes(id) ON DELETE CASCADE
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS profissionais_consultados (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                paciente_id INTEGER NOT NULL, nome TEXT NOT NULL,
                especialidade TEXT, data_consulta DATE, observacoes TEXT,
                data_cadastro TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (paciente_id) REFERENCES pacientes(id) ON DELETE CASCADE
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS comorbidades_dieta (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                paciente_id INTEGER NOT NULL UNIQUE,
                peso REAL, altura REAL, comorbidades TEXT,
                restricoes_alimentares TEXT, dieta_especifica TEXT,
                consistencia_alimento TEXT, alergias_alimentares TEXT,
                observacoes_nutricao TEXT,
                atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (paciente_id) REFERENCES pacientes(id) ON DELETE CASCADE
            )
        ''')

        try:
            cursor.execute('ALTER TABLE comorbidades_dieta ADD COLUMN peso REAL')
        except Exception:
            pass
        try:
            cursor.execute('ALTER TABLE comorbidades_dieta ADD COLUMN altura REAL')
        except Exception:
            pass

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS visitas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                paciente_id INTEGER NOT NULL, visitante_nome TEXT NOT NULL,
                visitante_parentesco TEXT, data_visita DATE NOT NULL,
                hora_entrada TEXT, hora_saida TEXT, observacoes TEXT,
                registrado_por TEXT,
                data_cadastro TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (paciente_id) REFERENCES pacientes(id) ON DELETE CASCADE
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS evolucoes_medicas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                paciente_id INTEGER NOT NULL,
                data_evolucao DATE NOT NULL,
                profissional TEXT, especialidade TEXT,
                descricao TEXT NOT NULL,
                data_cadastro TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (paciente_id) REFERENCES pacientes(id) ON DELETE CASCADE
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS exames (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                paciente_id INTEGER NOT NULL, tipo_exame TEXT NOT NULL,
                data_exame DATE NOT NULL, laboratorio TEXT,
                medico_solicitante TEXT, resultado TEXT,
                valores_referencia TEXT, arquivo_nome TEXT, observacoes TEXT,
                data_cadastro TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (paciente_id) REFERENCES pacientes(id) ON DELETE CASCADE
            )
        ''')

        conn.commit()


def calcular_idade(data_nasc_str):
    if not data_nasc_str:
        return None
    try:
        data_nasc = datetime.strptime(data_nasc_str, '%Y-%m-%d').date()
        hoje = date.today()
        return hoje.year - data_nasc.year - (
            (hoje.month, hoje.day) < (data_nasc.month, data_nasc.day)
        )
    except ValueError:
        return None


def _normalizar_documento(valor):
    return ''.join(c for c in (valor or '') if c.isdigit())


def extensao_permitida(nome_arquivo):
    return '.' in nome_arquivo and nome_arquivo.rsplit('.', 1)[1].lower() in EXTENSOES_PERMITIDAS


def salvar_arquivo_exame(arquivo):
    """Salva o arquivo anexado a um exame (se houver) e retorna o nome salvo."""
    if not arquivo or not arquivo.filename:
        return None
    if not extensao_permitida(arquivo.filename):
        return None
    nome_seguro = secure_filename(arquivo.filename)
    ext = nome_seguro.rsplit('.', 1)[1].lower()
    arquivo_nome = f"{uuid.uuid4().hex}.{ext}"
    arquivo.save(os.path.join(UPLOAD_FOLDER, arquivo_nome))
    return arquivo_nome


def hash_senha(senha):
    return bcrypt.hashpw(senha.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def verificar_senha(senha, hash_armazenado):
    return bcrypt.checkpw(senha.encode('utf-8'), hash_armazenado.encode('utf-8'))


def login_obrigatorio(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'usuario_id' not in session:
            flash('Faça login para acessar esta página.', 'erro')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def apenas_admin(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('usuario_perfil') != 'admin':
            flash('Acesso restrito a administradores.', 'erro')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated

def apenas_equipe(f):
    """Bloqueia responsáveis e o superadmin de acessar páginas de uma casa."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('usuario_perfil') == 'responsavel':
            return redirect(url_for('painel_responsavel'))
        if session.get('usuario_perfil') == 'superadmin':
            return redirect(url_for('listar_casas'))
        return f(*args, **kwargs)
    return decorated

def apenas_superadmin(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('usuario_perfil') != 'superadmin':
            flash('Acesso restrito.', 'erro')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated


def casa_id_atual():
    """Casa (instituição) do usuário logado. None para superadmin."""
    return session.get('casa_id')


def buscar_paciente_da_casa(conn, paciente_id):
    """Busca um paciente garantindo que ele pertence à casa do usuário logado."""
    return conn.execute(
        'SELECT * FROM pacientes WHERE id = ? AND casa_id = ?',
        (paciente_id, casa_id_atual())
    ).fetchone()


def gerar_slug(nome):
    """Gera slug URL-seguro a partir do nome da casa."""
    nome_norm = unicodedata.normalize('NFD', nome)
    nome_ascii = nome_norm.encode('ascii', 'ignore').decode('ascii')
    slug = nome_ascii.lower()
    slug = re.sub(r'[^a-z0-9]+', '-', slug)
    slug = slug.strip('-')
    return slug or 'casa'


def slug_unico(conn, base_slug, excluir_id=None):
    """Garante unicidade do slug adicionando sufixo numérico se necessário."""
    slug = base_slug
    contador = 1
    while True:
        query = 'SELECT id FROM casas WHERE slug = ?'
        params = [slug]
        if excluir_id:
            query += ' AND id != ?'
            params.append(excluir_id)
        if not conn.execute(query, params).fetchone():
            return slug
        slug = f'{base_slug}-{contador}'
        contador += 1


# ============================================================================
# GESTÃO DE CASAS (multi-instituição — restrito ao superadmin)
# ============================================================================

@app.route('/admin/casas')
@login_obrigatorio
@apenas_superadmin
def listar_casas():
    with get_db() as conn:
        casas = conn.execute('''
            SELECT c.*,
                   (SELECT COUNT(*) FROM pacientes WHERE casa_id = c.id AND ativo = 1) as total_pacientes,
                   (SELECT COUNT(*) FROM usuarios WHERE casa_id = c.id AND ativo = 1) as total_usuarios
            FROM casas c ORDER BY c.nome
        ''').fetchall()
    return render_template('casas.html', casas=casas)


@app.route('/admin/casas/nova', methods=['GET', 'POST'])
@login_obrigatorio
@apenas_superadmin
def nova_casa():
    if request.method == 'POST':
        dados = request.form
        nome_casa     = dados.get('nome_casa', '').strip()
        admin_nome    = dados.get('admin_nome', '').strip()
        admin_email   = dados.get('admin_email', '').strip().lower()
        admin_senha   = dados.get('admin_senha', '')

        if not nome_casa or not admin_nome or not admin_email or not admin_senha:
            flash('Preencha todos os campos obrigatórios.', 'erro')
            return render_template('casa_form.html')
        if len(admin_senha) < 6:
            flash('A senha do administrador deve ter pelo menos 6 caracteres.', 'erro')
            return render_template('casa_form.html')

        try:
            with get_db() as conn:
                base = gerar_slug(nome_casa)
                slug = slug_unico(conn, base)
                casa_id = conn.execute(
                    'INSERT INTO casas (nome, slug) VALUES (?, ?)', (nome_casa, slug)
                ).lastrowid
                conn.execute('''
                    INSERT INTO usuarios (nome, email, senha, perfil, casa_id)
                    VALUES (?, ?, ?, 'admin', ?)
                ''', (admin_nome, admin_email, hash_senha(admin_senha), casa_id))
        except sqlite3.IntegrityError:
            flash('Este e-mail já está cadastrado para outro usuário.', 'erro')
            return render_template('casa_form.html')

        flash(f'Casa "{nome_casa}" criada! Administrador {admin_nome} pode logar com {admin_email}.', 'sucesso')
        return redirect(url_for('listar_casas'))

    return render_template('casa_form.html')


@app.route('/admin/casas/<int:casa_id>/editar', methods=['GET', 'POST'])
@login_obrigatorio
@apenas_superadmin
def editar_casa(casa_id):
    with get_db() as conn:
        casa = conn.execute('SELECT * FROM casas WHERE id = ?', (casa_id,)).fetchone()
        if not casa:
            flash('Casa não encontrada.', 'erro')
            return redirect(url_for('listar_casas'))

        if request.method == 'POST':
            nome = request.form.get('nome', '').strip()
            if not nome:
                flash('Informe o nome da casa.', 'erro')
                return render_template('casa_editar.html', casa=casa)
            base = gerar_slug(nome)
            slug = slug_unico(conn, base, excluir_id=casa_id)
            conn.execute('UPDATE casas SET nome = ?, slug = ? WHERE id = ?', (nome, slug, casa_id))
            flash('Nome da casa atualizado!', 'sucesso')
            return redirect(url_for('listar_casas'))

    return render_template('casa_editar.html', casa=casa)


@app.route('/admin/casas/<int:casa_id>/toggle', methods=['POST'])
@login_obrigatorio
@apenas_superadmin
def toggle_casa(casa_id):
    with get_db() as conn:
        casa = conn.execute('SELECT ativo, nome FROM casas WHERE id = ?', (casa_id,)).fetchone()
        if casa:
            novo_status = 0 if casa['ativo'] else 1
            conn.execute('UPDATE casas SET ativo = ? WHERE id = ?', (novo_status, casa_id))
            acao = 'ativada' if novo_status else 'desativada'
            flash(f'Casa "{casa["nome"]}" {acao}.', 'sucesso')
    return redirect(url_for('listar_casas'))


# ============================================================================
# AUTENTICAÇÃO
# ============================================================================

@app.route('/entrar/<slug>', methods=['GET', 'POST'])
def login_casa(slug):
    if 'usuario_id' in session:
        if session.get('usuario_perfil') == 'responsavel':
            return redirect(url_for('painel_responsavel'))
        if session.get('usuario_perfil') == 'superadmin':
            return redirect(url_for('listar_casas'))
        return redirect(url_for('index'))

    with get_db() as conn:
        casa_pre = conn.execute('SELECT * FROM casas WHERE slug = ? AND ativo = 1', (slug,)).fetchone()
    if not casa_pre:
        flash('Link inválido ou instituição desativada.', 'erro')
        return redirect(url_for('login'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        senha = request.form.get('senha', '')
        casa_id_escolhida = str(casa_pre['id'])

        with get_db() as conn:
            usuario = conn.execute(
                'SELECT * FROM usuarios WHERE LOWER(email) = LOWER(?) AND ativo = 1',
                (email,)
            ).fetchone()
            casa = None
            if usuario and usuario['casa_id']:
                casa = conn.execute('SELECT * FROM casas WHERE id = ?', (usuario['casa_id'],)).fetchone()

        if (usuario and usuario['casa_id']
                and str(usuario['casa_id']) != casa_id_escolhida):
            flash('Este e-mail não pertence a esta instituição.', 'erro')
            return render_template('login.html', casa_preselecionar=casa_pre)

        if usuario and usuario['casa_id'] and (not casa or not casa['ativo']):
            flash('O acesso da sua instituição está temporariamente desativado. Contate o suporte.', 'erro')
            return render_template('login.html', casa_preselecionar=casa_pre)

        if usuario and verificar_senha(senha, usuario['senha']):
            session.permanent = False
            session['usuario_id']     = usuario['id']
            session['usuario_nome']   = usuario['nome']
            session['usuario_perfil'] = usuario['perfil']
            session['paciente_id']    = usuario['paciente_id']
            session['casa_id']        = usuario['casa_id']
            session['casa_nome']      = casa['nome'] if casa else None
            session['casa_slug']      = casa_pre['slug']
            flash(f'Bem-vindo(a), {usuario["nome"]}!', 'sucesso')
            if usuario['perfil'] == 'superadmin':
                return redirect(url_for('listar_casas'))
            if usuario['perfil'] == 'responsavel':
                if not usuario['paciente_id']:
                    return redirect(url_for('vincular_idoso'))
                return redirect(url_for('painel_responsavel'))
            return redirect(url_for('index'))
        else:
            flash('E-mail ou senha incorretos.', 'erro')
            return render_template('login.html', casa_preselecionar=casa_pre)

    return render_template('login.html', casa_preselecionar=casa_pre)


@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'usuario_id' in session:
        if session.get('usuario_perfil') == 'responsavel':
            return redirect(url_for('painel_responsavel'))
        if session.get('usuario_perfil') == 'superadmin':
            return redirect(url_for('listar_casas'))
        return redirect(url_for('index'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        senha = request.form.get('senha', '')

        with get_db() as conn:
            usuario = conn.execute(
                'SELECT * FROM usuarios WHERE LOWER(email) = LOWER(?) AND ativo = 1',
                (email,)
            ).fetchone()
            casa = None
            if usuario and usuario['casa_id']:
                casa = conn.execute(
                    'SELECT * FROM casas WHERE id = ?', (usuario['casa_id'],)
                ).fetchone()

        if usuario and usuario['casa_id'] and (not casa or not casa['ativo']):
            flash('O acesso da sua instituição está temporariamente desativado. Contate o suporte.', 'erro')
            return render_template('login.html')

        if usuario and verificar_senha(senha, usuario['senha']):
            session.permanent = False
            session['usuario_id']     = usuario['id']
            session['usuario_nome']   = usuario['nome']
            session['usuario_perfil'] = usuario['perfil']
            session['paciente_id']    = usuario['paciente_id']
            session['casa_id']        = usuario['casa_id']
            session['casa_nome']      = casa['nome'] if casa else None
            session['casa_slug']      = casa['slug'] if casa else None
            flash(f'Bem-vindo(a), {usuario["nome"]}!', 'sucesso')
            if usuario['perfil'] == 'superadmin':
                return redirect(url_for('listar_casas'))
            if usuario['perfil'] == 'responsavel':
                if not usuario['paciente_id']:
                    return redirect(url_for('vincular_idoso'))
                return redirect(url_for('painel_responsavel'))
            return redirect(url_for('index'))
        else:
            flash('E-mail ou senha incorretos.', 'erro')
            return render_template('login.html')
    return render_template('login.html')


@app.route('/entrar/<slug>/cadastro', methods=['GET', 'POST'])
def cadastro_responsavel_casa(slug):
    if 'usuario_id' in session:
        return redirect(url_for('index'))
    with get_db() as conn:
        casa_pre = conn.execute('SELECT * FROM casas WHERE slug = ? AND ativo = 1', (slug,)).fetchone()
    if not casa_pre:
        flash('Link inválido ou instituição desativada.', 'erro')
        return redirect(url_for('login'))
    return _processar_cadastro_responsavel(casa_preselecionar=casa_pre)


@app.route('/responsavel/cadastro', methods=['GET', 'POST'])
def cadastro_responsavel():
    if 'usuario_id' in session:
        return redirect(url_for('index'))
    with get_db() as conn:
        casas = conn.execute('SELECT id, nome FROM casas WHERE ativo = 1 ORDER BY nome').fetchall()
    return _processar_cadastro_responsavel(casas=casas)


def _processar_cadastro_responsavel(casas=None, casa_preselecionar=None):
    if request.method == 'POST':
        dados = request.form
        nome    = dados.get('nome', '').strip()
        email   = dados.get('email', '').strip().lower()
        cpf     = dados.get('cpf', '').strip()
        senha   = dados.get('senha', '')
        senha2  = dados.get('confirmar_senha', '')
        casa_id = str(casa_preselecionar['id']) if casa_preselecionar else (dados.get('casa_id') or None)

        if not nome or not email or not cpf or not senha or not casa_id:
            flash('Preencha todos os campos obrigatórios.', 'erro')
            return render_template('responsavel_cadastro.html', casas=casas, casa_preselecionar=casa_preselecionar)
        if len(senha) < 6:
            flash('A senha deve ter pelo menos 6 caracteres.', 'erro')
            return render_template('responsavel_cadastro.html', casas=casas, casa_preselecionar=casa_preselecionar)
        if senha != senha2:
            flash('As senhas não coincidem.', 'erro')
            return render_template('responsavel_cadastro.html', casas=casas, casa_preselecionar=casa_preselecionar)

        with get_db() as conn:
            casa = conn.execute(
                'SELECT * FROM casas WHERE id = ? AND ativo = 1', (casa_id,)
            ).fetchone()
        if not casa:
            flash('Instituição inválida. Selecione novamente.', 'erro')
            return render_template('responsavel_cadastro.html', casas=casas, casa_preselecionar=casa_preselecionar)

        try:
            with get_db() as conn:
                cursor = conn.execute('''
                    INSERT INTO usuarios (nome, email, senha, perfil, cpf, casa_id)
                    VALUES (?, ?, ?, 'responsavel', ?, ?)
                ''', (nome, email, hash_senha(senha), cpf, casa_id))
                usuario_id = cursor.lastrowid
        except sqlite3.IntegrityError:
            flash('Este e-mail já está cadastrado.', 'erro')
            return render_template('responsavel_cadastro.html', casas=casas, casa_preselecionar=casa_preselecionar)

        session.permanent = False
        session['usuario_id']     = usuario_id
        session['usuario_nome']   = nome
        session['usuario_perfil'] = 'responsavel'
        session['paciente_id']    = None
        session['casa_id']        = casa['id']
        session['casa_nome']      = casa['nome']
        session['casa_slug']      = casa['slug']
        flash(f'Conta criada com sucesso, {nome}! Agora informe o CPF/RG do idoso para continuar.', 'sucesso')
        return redirect(url_for('vincular_idoso'))

    return render_template('responsavel_cadastro.html', casas=casas, casa_preselecionar=casa_preselecionar)


@app.route('/responsavel/vincular-idoso', methods=['GET', 'POST'])
@login_obrigatorio
def vincular_idoso():
    if session.get('usuario_perfil') != 'responsavel':
        flash('Acesso não autorizado.', 'erro')
        return redirect(url_for('index'))

    if request.method == 'POST':
        documento = _normalizar_documento(request.form.get('cpf_rg', ''))
        if not documento:
            flash('Informe o CPF ou RG do idoso.', 'erro')
            return render_template('responsavel_vincular.html')

        with get_db() as conn:
            paciente = conn.execute('''
                SELECT * FROM pacientes WHERE ativo = 1 AND casa_id = ?
                  AND (REPLACE(REPLACE(REPLACE(cpf,'.',''),'-',''),' ','') = ?
                       OR REPLACE(REPLACE(REPLACE(rg,'.',''),'-',''),' ','') = ?)
            ''', (casa_id_atual(), documento, documento)).fetchone()

            if not paciente:
                flash('Nenhum idoso encontrado com este CPF/RG. Confira o documento e tente novamente.', 'erro')
                return render_template('responsavel_vincular.html')

            conn.execute(
                'UPDATE usuarios SET paciente_id = ? WHERE id = ?',
                (paciente['id'], session['usuario_id'])
            )
        session['paciente_id'] = paciente['id']
        flash(f'Vinculado(a) com sucesso a {paciente["nome"]}!', 'sucesso')
        return redirect(url_for('painel_responsavel'))

    return render_template('responsavel_vincular.html')


@app.route('/responsavel/desvincular', methods=['POST'])
@login_obrigatorio
def desvincular_idoso():
    if session.get('usuario_perfil') != 'responsavel':
        flash('Acesso não autorizado.', 'erro')
        return redirect(url_for('index'))
    with get_db() as conn:
        conn.execute(
            'UPDATE usuarios SET paciente_id = NULL WHERE id = ?',
            (session['usuario_id'],)
        )
    session['paciente_id'] = None
    flash('Você pode vincular outro idoso agora.', 'sucesso')
    return redirect(url_for('vincular_idoso'))


@app.route('/meu-perfil', methods=['GET', 'POST'])
@login_obrigatorio
def meu_perfil():
    usuario_id = session['usuario_id']
    eh_admin = session.get('usuario_perfil') == 'admin'
    with get_db() as conn:
        usuario = conn.execute('SELECT * FROM usuarios WHERE id = ?', (usuario_id,)).fetchone()
        casa = None
        if eh_admin:
            casa = conn.execute('SELECT * FROM casas WHERE id = ?', (casa_id_atual(),)).fetchone()

        if request.method == 'POST':
            dados = request.form
            nome        = dados.get('nome', '').strip()
            email       = dados.get('email', '').strip().lower()
            senha_atual = dados.get('senha_atual', '')
            senha_nova  = dados.get('senha_nova', '')
            senha_nova2 = dados.get('confirmar_senha_nova', '')
            nome_casa   = dados.get('nome_casa', '').strip()

            if not nome or not email:
                flash('Nome e e-mail são obrigatórios.', 'erro')
                return render_template('meu_perfil.html', usuario=usuario, casa=casa)
            if eh_admin and not nome_casa:
                flash('Informe o nome da casa.', 'erro')
                return render_template('meu_perfil.html', usuario=usuario, casa=casa)

            quer_trocar_senha = bool(senha_nova or senha_nova2)
            email_mudou = email.lower() != usuario['email'].lower()

            if (quer_trocar_senha or email_mudou) and not senha_atual:
                flash('Informe sua senha atual para confirmar a alteração de e-mail ou senha.', 'erro')
                return render_template('meu_perfil.html', usuario=usuario, casa=casa)

            if (quer_trocar_senha or email_mudou) and not verificar_senha(senha_atual, usuario['senha']):
                flash('Senha atual incorreta.', 'erro')
                return render_template('meu_perfil.html', usuario=usuario, casa=casa)

            if quer_trocar_senha:
                if len(senha_nova) < 6:
                    flash('A nova senha deve ter pelo menos 6 caracteres.', 'erro')
                    return render_template('meu_perfil.html', usuario=usuario, casa=casa)
                if senha_nova != senha_nova2:
                    flash('As senhas novas não coincidem.', 'erro')
                    return render_template('meu_perfil.html', usuario=usuario, casa=casa)

            try:
                if quer_trocar_senha:
                    conn.execute(
                        'UPDATE usuarios SET nome = ?, email = ?, senha = ? WHERE id = ?',
                        (nome, email, hash_senha(senha_nova), usuario_id)
                    )
                else:
                    conn.execute(
                        'UPDATE usuarios SET nome = ?, email = ? WHERE id = ?',
                        (nome, email, usuario_id)
                    )
            except sqlite3.IntegrityError:
                flash('Este e-mail já está em uso por outra conta.', 'erro')
                return render_template('meu_perfil.html', usuario=usuario, casa=casa)

            if eh_admin:
                conn.execute('UPDATE casas SET nome = ? WHERE id = ?', (nome_casa, casa_id_atual()))
                session['casa_nome'] = nome_casa

            session['usuario_nome'] = nome
            flash('Perfil atualizado com sucesso!', 'sucesso')
            return redirect(url_for('meu_perfil'))

    return render_template('meu_perfil.html', usuario=usuario, casa=casa)


@app.route('/logout')
def logout():
    nome = session.get('usuario_nome', '')
    casa_slug = session.get('casa_slug')
    session.clear()
    flash(f'Até logo, {nome}!', 'sucesso')
    if casa_slug:
        return redirect(url_for('login_casa', slug=casa_slug))
    return redirect(url_for('login'))


# ============================================================================
# PAINEL DO RESPONSÁVEL (somente leitura)
# ============================================================================

@app.route('/meu-paciente')
@login_obrigatorio
def painel_responsavel():
    if session.get('usuario_perfil') != 'responsavel':
        flash('Acesso não autorizado.', 'erro')
        return redirect(url_for('login'))
    paciente_id = session.get('paciente_id')
    if not paciente_id:
        return redirect(url_for('vincular_idoso'))

    with get_db() as conn:
        paciente = buscar_paciente_da_casa(conn, paciente_id)
        if not paciente:
            flash('Paciente não encontrado.', 'erro')
            return redirect(url_for('login'))

        paciente_dict = dict(paciente)
        paciente_dict['idade'] = calcular_idade(paciente_dict['data_nascimento'])

        condicoes = conn.execute(
            'SELECT * FROM condicoes WHERE paciente_id = ? ORDER BY data_diagnostico DESC',
            (paciente_id,)
        ).fetchall()

        medicamentos_raw = conn.execute(
            'SELECT * FROM medicamentos WHERE paciente_id = ? AND ativo = 1 ORDER BY nome',
            (paciente_id,)
        ).fetchall()
        medicamentos = []
        for m in medicamentos_raw:
            med_dict = dict(m)
            horarios = conn.execute(
                'SELECT horario FROM horarios_medicamento WHERE medicamento_id = ? ORDER BY horario',
                (m['id'],)
            ).fetchall()
            med_dict['horarios'] = [h['horario'] for h in horarios]
            ultimas = conn.execute(
                'SELECT * FROM doses_administradas WHERE medicamento_id = ? ORDER BY horario_administrado DESC LIMIT 5',
                (m['id'],)
            ).fetchall()
            med_dict['ultimas_doses'] = [dict(d) for d in ultimas]
            medicamentos.append(med_dict)

        ficha_psico = conn.execute(
            'SELECT * FROM ficha_psicologica WHERE paciente_id = ?', (paciente_id,)
        ).fetchone()

        profissionais = conn.execute(
            'SELECT * FROM profissionais_consultados WHERE paciente_id = ? ORDER BY data_consulta DESC',
            (paciente_id,)
        ).fetchall()

        dieta = conn.execute(
            'SELECT * FROM comorbidades_dieta WHERE paciente_id = ?', (paciente_id,)
        ).fetchone()

        visitas = conn.execute(
            'SELECT * FROM visitas WHERE paciente_id = ? ORDER BY data_visita DESC LIMIT 10',
            (paciente_id,)
        ).fetchall()

        exames = conn.execute(
            'SELECT * FROM exames WHERE paciente_id = ? ORDER BY data_exame DESC',
            (paciente_id,)
        ).fetchall()

        evolucoes = conn.execute(
            'SELECT * FROM evolucoes_medicas WHERE paciente_id = ? ORDER BY data_evolucao DESC',
            (paciente_id,)
        ).fetchall()

    return render_template('pacienteresponsavel.html',
        paciente=paciente_dict, condicoes=condicoes, medicamentos=medicamentos,
        ficha_psico=ficha_psico, profissionais=profissionais, dieta=dieta,
        visitas=visitas, exames=exames, evolucoes=evolucoes)


# ============================================================================
# USUÁRIOS
# ============================================================================

@app.route('/usuarios')
@login_obrigatorio
@apenas_admin
def listar_usuarios():
    with get_db() as conn:
        usuarios = conn.execute('''
            SELECT u.id, u.nome, u.email, u.perfil, u.ativo, u.criado_em,
                   u.paciente_id, p.nome as paciente_nome
            FROM usuarios u
            LEFT JOIN pacientes p ON u.paciente_id = p.id
            WHERE u.casa_id = ?
            ORDER BY u.nome
        ''', (casa_id_atual(),)).fetchall()
    return render_template('usuarios.html', usuarios=usuarios)


@app.route('/usuarios/novo', methods=['GET', 'POST'])
@login_obrigatorio
@apenas_admin
def novo_usuario():
    if request.method == 'POST':
        nome        = request.form.get('nome', '').strip()
        email       = request.form.get('email', '').strip().lower()
        senha       = request.form.get('senha', '')
        perfil      = request.form.get('perfil', 'enfermeiro')
        paciente_id = request.form.get('paciente_id') or None

        with get_db() as conn:
            pacientes = conn.execute(
                'SELECT id, nome, cpf, rg FROM pacientes WHERE ativo = 1 AND casa_id = ? ORDER BY nome',
                (casa_id_atual(),)
            ).fetchall()

        if not nome or not email or not senha:
            flash('Preencha todos os campos obrigatórios.', 'erro')
            return render_template('usuario_form.html', pacientes=pacientes)
        if len(senha) < 6:
            flash('A senha deve ter pelo menos 6 caracteres.', 'erro')
            return render_template('usuario_form.html', pacientes=pacientes)

        try:
            with get_db() as conn:
                conn.execute(
                    'INSERT INTO usuarios (nome, email, senha, perfil, paciente_id, casa_id) VALUES (?, ?, ?, ?, ?, ?)',
                    (nome, email, hash_senha(senha), perfil,
                     paciente_id if perfil == 'responsavel' else None, casa_id_atual())
                )
            flash(f'Usuário {nome} criado com sucesso!', 'sucesso')
            return redirect(url_for('listar_usuarios'))
        except Exception:
            flash('Erro: este e-mail já está cadastrado.', 'erro')

    with get_db() as conn:
        pacientes = conn.execute(
            'SELECT id, nome, cpf, rg FROM pacientes WHERE ativo = 1 AND casa_id = ? ORDER BY nome',
            (casa_id_atual(),)
        ).fetchall()
    return render_template('usuario_form.html', pacientes=pacientes)


@app.route('/usuarios/<int:usuario_id>/toggle', methods=['POST'])
@login_obrigatorio
@apenas_admin
def toggle_usuario(usuario_id):
    if usuario_id == session.get('usuario_id'):
        flash('Você não pode desativar sua própria conta.', 'erro')
        return redirect(url_for('listar_usuarios'))
    with get_db() as conn:
        usuario = conn.execute(
            'SELECT ativo, nome FROM usuarios WHERE id = ? AND casa_id = ?',
            (usuario_id, casa_id_atual())
        ).fetchone()
        if usuario:
            novo_status = 0 if usuario['ativo'] else 1
            conn.execute('UPDATE usuarios SET ativo = ? WHERE id = ?', (novo_status, usuario_id))
            acao = 'ativado' if novo_status else 'desativado'
            flash(f'Usuário {usuario["nome"]} {acao}.', 'sucesso')
    return redirect(url_for('listar_usuarios'))


@app.route('/usuarios/<int:usuario_id>/excluir', methods=['POST'])
@login_obrigatorio
@apenas_admin
def excluir_usuario(usuario_id):
    if usuario_id == session.get('usuario_id'):
        flash('Você não pode excluir sua própria conta.', 'erro')
        return redirect(url_for('listar_usuarios'))
    with get_db() as conn:
        usuario = conn.execute(
            'SELECT nome, perfil FROM usuarios WHERE id = ? AND casa_id = ?',
            (usuario_id, casa_id_atual())
        ).fetchone()
        if not usuario:
            flash('Usuário não encontrado.', 'erro')
            return redirect(url_for('listar_usuarios'))
        if usuario['perfil'] == 'admin':
            outros_admins = conn.execute(
                'SELECT COUNT(*) as total FROM usuarios WHERE casa_id = ? AND perfil = ? AND ativo = 1 AND id != ?',
                (casa_id_atual(), 'admin', usuario_id)
            ).fetchone()['total']
            if outros_admins == 0:
                flash('Não é possível excluir o único administrador da casa.', 'erro')
                return redirect(url_for('listar_usuarios'))
        conn.execute('DELETE FROM usuarios WHERE id = ? AND casa_id = ?', (usuario_id, casa_id_atual()))
        flash(f'Usuário {usuario["nome"]} excluído permanentemente.', 'sucesso')
    return redirect(url_for('listar_usuarios'))


# ============================================================================
# PACIENTES
# ============================================================================

@app.route('/')
@login_obrigatorio
@apenas_equipe
def index():
    with get_db() as conn:
        pacientes = conn.execute('''
            SELECT p.*,
                   (SELECT COUNT(*) FROM medicamentos WHERE paciente_id = p.id AND ativo = 1) as total_medicamentos,
                   (SELECT COUNT(*) FROM condicoes WHERE paciente_id = p.id) as total_condicoes
            FROM pacientes p WHERE p.ativo = 1 AND p.casa_id = ? ORDER BY p.nome
        ''', (casa_id_atual(),)).fetchall()
        pacientes_list = []
        for p in pacientes:
            p_dict = dict(p)
            p_dict['idade'] = calcular_idade(p_dict['data_nascimento'])
            pacientes_list.append(p_dict)

        antibioticos_raw = conn.execute('''
            SELECT m.id, m.nome, m.data_fim_tratamento, p.id as paciente_id, p.nome as paciente_nome
            FROM medicamentos m
            JOIN pacientes p ON m.paciente_id = p.id
            WHERE m.ativo = 1 AND p.ativo = 1 AND m.eh_antibiotico = 1 AND p.casa_id = ?
              AND m.data_fim_tratamento IS NOT NULL
              AND date(m.data_fim_tratamento) <= date('now', 'localtime', '+2 days')
            ORDER BY m.data_fim_tratamento
        ''', (casa_id_atual(),)).fetchall()
        alertas_antibiotico = []
        for a in antibioticos_raw:
            a_dict = dict(a)
            try:
                fim = datetime.strptime(a_dict['data_fim_tratamento'], '%Y-%m-%d').date()
                a_dict['dias_restantes'] = (fim - date.today()).days
            except ValueError:
                a_dict['dias_restantes'] = None
            alertas_antibiotico.append(a_dict)

        agenda_hoje_total = conn.execute('''
            SELECT COUNT(*) FROM horarios_medicamento h
            JOIN medicamentos m ON h.medicamento_id = m.id
            JOIN pacientes p ON m.paciente_id = p.id
            WHERE m.ativo = 1 AND p.ativo = 1 AND p.casa_id = ?
        ''', (casa_id_atual(),)).fetchone()[0]

        equipe_ativa_total = conn.execute('''
            SELECT COUNT(*) FROM usuarios
            WHERE casa_id = ? AND ativo = 1 AND perfil != 'responsavel'
        ''', (casa_id_atual(),)).fetchone()[0]
    return render_template('index.html', pacientes=pacientes_list,
                           alertas_antibiotico=alertas_antibiotico,
                           agenda_hoje_total=agenda_hoje_total,
                           equipe_ativa_total=equipe_ativa_total)


@app.route('/paciente/novo', methods=['GET', 'POST'])
@login_obrigatorio
@apenas_equipe
def novo_paciente():
    if request.method == 'POST':
        dados = request.form
        with get_db() as conn:
            cursor = conn.execute('''
                INSERT INTO pacientes (
                    nome, data_nascimento, sexo, cpf, rg, telefone, quarto,
                    contato_emergencia_nome, contato_emergencia_telefone,
                    contato_emergencia_parentesco, observacoes, casa_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                dados.get('nome'), dados.get('data_nascimento'), dados.get('sexo'),
                dados.get('cpf'), dados.get('rg'), dados.get('telefone'),
                dados.get('quarto'), dados.get('contato_emergencia_nome'),
                dados.get('contato_emergencia_telefone'),
                dados.get('contato_emergencia_parentesco'), dados.get('observacoes'),
                casa_id_atual()
            ))
            paciente_id = cursor.lastrowid

            # Dieta
            dieta_fields = ['peso', 'altura', 'comorbidades', 'dieta_especifica',
                            'consistencia_alimento', 'restricoes_alimentares',
                            'alergias_alimentares', 'observacoes_nutricao']
            if any(dados.get(f) for f in dieta_fields):
                conn.execute('''
                    INSERT INTO comorbidades_dieta
                        (paciente_id, peso, altura, comorbidades, dieta_especifica,
                         consistencia_alimento, restricoes_alimentares,
                         alergias_alimentares, observacoes_nutricao)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    paciente_id, dados.get('peso') or None, dados.get('altura') or None,
                    dados.get('comorbidades'), dados.get('dieta_especifica'),
                    dados.get('consistencia_alimento'), dados.get('restricoes_alimentares'),
                    dados.get('alergias_alimentares'), dados.get('observacoes_nutricao'),
                ))

            # Medicamentos
            idx = 0
            while dados.get(f'med_nome_{idx}'):
                nome       = dados.get(f'med_nome_{idx}', '').strip()
                dosagem    = dados.get(f'med_dosagem_{idx}', '')
                via        = dados.get(f'med_via_{idx}', '')
                instrucoes = dados.get(f'med_instrucoes_{idx}', '')
                freq_tipo  = dados.get(f'med_freq_tipo_{idx}', 'diaria')
                doses_dia  = dados.get(f'med_doses_dia_{idx}', '1')
                intervalo  = dados.get(f'med_intervalo_{idx}', '')
                dias_sem   = request.form.getlist(f'med_dias_{idx}[]')
                horarios   = request.form.getlist(f'med_horario_{idx}[]')
                info_freq  = _montar_info_frequencia(freq_tipo, doses_dia, intervalo, dias_sem)
                instrucoes_completas = f"{info_freq}\n{instrucoes}".strip() if instrucoes else info_freq
                if nome:
                    cur2 = conn.execute('''
                        INSERT INTO medicamentos
                            (paciente_id, nome, dosagem, via_administracao, instrucoes)
                        VALUES (?, ?, ?, ?, ?)
                    ''', (paciente_id, nome, dosagem, via, instrucoes_completas))
                    med_id = cur2.lastrowid
                    for h in horarios:
                        h = h.strip()
                        if h:
                            conn.execute(
                                'INSERT INTO horarios_medicamento (medicamento_id, horario) VALUES (?, ?)',
                                (med_id, h)
                            )
                idx += 1

            # Exames
            exame_idx = 0
            while dados.get(f'exame_tipo_{exame_idx}'):
                tipo_exame = dados.get(f'exame_tipo_{exame_idx}', '').strip()
                if tipo_exame:
                    arquivo_exame = request.files.get(f'exame_arquivo_{exame_idx}')
                    arquivo_nome_exame = salvar_arquivo_exame(arquivo_exame)
                    conn.execute('''
                        INSERT INTO exames
                            (paciente_id, tipo_exame, data_exame, laboratorio,
                             medico_solicitante, resultado, valores_referencia, arquivo_nome, observacoes)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        paciente_id, tipo_exame,
                        dados.get(f'exame_data_{exame_idx}') or date.today().isoformat(),
                        dados.get(f'exame_laboratorio_{exame_idx}', ''),
                        dados.get(f'exame_medico_{exame_idx}', ''),
                        dados.get(f'exame_resultado_{exame_idx}', ''),
                        dados.get(f'exame_valores_{exame_idx}', ''),
                        arquivo_nome_exame,
                        dados.get(f'exame_obs_{exame_idx}', '')
                    ))
                exame_idx += 1

            # Ficha psicológica
            psico_fields = ['antecedentes', 'diagnosticos_mentais', 'medicacao_psiquiatrica',
                            'comportamento', 'observacoes_psico']
            if any(dados.get(f) for f in psico_fields):
                conn.execute('''
                    INSERT INTO ficha_psicologica
                        (paciente_id, antecedentes, diagnosticos_mentais,
                         medicacao_psiquiatrica, comportamento, observacoes)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (
                    paciente_id, dados.get('antecedentes'), dados.get('diagnosticos_mentais'),
                    dados.get('medicacao_psiquiatrica'), dados.get('comportamento'),
                    dados.get('observacoes_psico')
                ))

            # Profissionais
            prof_idx = 0
            while dados.get(f'prof_nome_{prof_idx}'):
                prof_nome = dados.get(f'prof_nome_{prof_idx}', '').strip()
                if prof_nome:
                    conn.execute('''
                        INSERT INTO profissionais_consultados
                            (paciente_id, nome, especialidade, data_consulta, observacoes)
                        VALUES (?, ?, ?, ?, ?)
                    ''', (
                        paciente_id, prof_nome,
                        dados.get(f'prof_especialidade_{prof_idx}', ''),
                        dados.get(f'prof_data_{prof_idx}') or None,
                        dados.get(f'prof_obs_{prof_idx}', '')
                    ))
                prof_idx += 1

        flash('Paciente cadastrado com sucesso!', 'sucesso')
        return redirect(url_for('detalhe_paciente', paciente_id=paciente_id))
    return render_template('paciente_form.html', paciente=None, medicamentos=[],
                           dieta=None, ficha_psico=None, profissionais=[])


def _montar_info_frequencia(tipo, doses_dia, intervalo, dias_semana):
    dias_nomes = ['Dom', 'Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb']
    if tipo == 'condicional':
        return 'Frequência: Uso condicional (SOS) — administrar apenas se necessário'
    if tipo == 'semanal' and dias_semana:
        nomes = [dias_nomes[int(d)] for d in dias_semana if d.isdigit()]
        return f"Frequência: Semanal ({', '.join(nomes)})"
    if tipo == 'intervalo' and intervalo:
        return f"Frequência: A cada {intervalo} dias"
    doses_txt = {
        '1': '1 vez ao dia', '2': '2 vezes ao dia',
        '3': '3 vezes ao dia', '4': '4 vezes ao dia',
    }.get(doses_dia, f'{doses_dia}x ao dia')
    return f"Frequência: Diária — {doses_txt}"


@app.route('/paciente/<int:paciente_id>')
@login_obrigatorio
@apenas_equipe
def detalhe_paciente(paciente_id):
    with get_db() as conn:
        paciente = buscar_paciente_da_casa(conn, paciente_id)
        if not paciente:
            flash('Paciente não encontrado.', 'erro')
            return redirect(url_for('index'))
        paciente_dict = dict(paciente)
        paciente_dict['idade'] = calcular_idade(paciente_dict['data_nascimento'])

        condicoes = conn.execute(
            'SELECT * FROM condicoes WHERE paciente_id = ? ORDER BY data_diagnostico DESC',
            (paciente_id,)
        ).fetchall()

        medicamentos_raw = conn.execute(
            'SELECT * FROM medicamentos WHERE paciente_id = ? AND ativo = 1 ORDER BY nome',
            (paciente_id,)
        ).fetchall()
        medicamentos = []
        for m in medicamentos_raw:
            med_dict = dict(m)
            horarios = conn.execute(
                'SELECT horario FROM horarios_medicamento WHERE medicamento_id = ? ORDER BY horario',
                (m['id'],)
            ).fetchall()
            med_dict['horarios'] = [h['horario'] for h in horarios]

            ultimas_doses = conn.execute(
                'SELECT * FROM doses_administradas WHERE medicamento_id = ? ORDER BY horario_administrado DESC LIMIT 5',
                (m['id'],)
            ).fetchall()
            med_dict['ultimas_doses'] = [dict(d) for d in ultimas_doses]

            doses_hoje = conn.execute('''
                SELECT COUNT(*) as total FROM doses_administradas
                WHERE medicamento_id = ? AND date(horario_administrado) = date('now', 'localtime')
            ''', (m['id'],)).fetchone()['total']
            med_dict['doses_hoje'] = doses_hoje

            if med_dict.get('eh_antibiotico') and med_dict.get('data_fim_tratamento'):
                try:
                    fim = datetime.strptime(med_dict['data_fim_tratamento'], '%Y-%m-%d').date()
                    med_dict['dias_restantes_antibiotico'] = (fim - date.today()).days
                except ValueError:
                    med_dict['dias_restantes_antibiotico'] = None
            else:
                med_dict['dias_restantes_antibiotico'] = None

            medicamentos.append(med_dict)

        ficha_psico = conn.execute(
            'SELECT * FROM ficha_psicologica WHERE paciente_id = ?', (paciente_id,)
        ).fetchone()

        profissionais = conn.execute(
            'SELECT * FROM profissionais_consultados WHERE paciente_id = ? ORDER BY data_consulta DESC',
            (paciente_id,)
        ).fetchall()

        dieta = conn.execute(
            'SELECT * FROM comorbidades_dieta WHERE paciente_id = ?', (paciente_id,)
        ).fetchone()

        total_visitas = conn.execute(
            'SELECT COUNT(*) as total FROM visitas WHERE paciente_id = ?', (paciente_id,)
        ).fetchone()['total']

        total_exames = conn.execute(
            'SELECT COUNT(*) as total FROM exames WHERE paciente_id = ?', (paciente_id,)
        ).fetchone()['total']

        total_evolucoes = conn.execute(
            'SELECT COUNT(*) as total FROM evolucoes_medicas WHERE paciente_id = ?', (paciente_id,)
        ).fetchone()['total']

    return render_template(
        'paciente_detalhe.html',
        paciente=paciente_dict, condicoes=condicoes, medicamentos=medicamentos,
        ficha_psico=ficha_psico, profissionais=profissionais, dieta=dieta,
        total_visitas=total_visitas, total_exames=total_exames,
        total_evolucoes=total_evolucoes,
    )


@app.route('/paciente/<int:paciente_id>/editar', methods=['GET', 'POST'])
@login_obrigatorio
@apenas_equipe
def editar_paciente(paciente_id):
    with get_db() as conn:
        paciente_existente = buscar_paciente_da_casa(conn, paciente_id)
        if not paciente_existente:
            flash('Paciente não encontrado.', 'erro')
            return redirect(url_for('index'))

        if request.method == 'POST':
            dados = request.form
            conn.execute('''
                UPDATE pacientes SET
                    nome = ?, data_nascimento = ?, sexo = ?, cpf = ?, rg = ?,
                    telefone = ?, quarto = ?, contato_emergencia_nome = ?,
                    contato_emergencia_telefone = ?, contato_emergencia_parentesco = ?,
                    observacoes = ?
                WHERE id = ?
            ''', (
                dados.get('nome'), dados.get('data_nascimento'), dados.get('sexo'),
                dados.get('cpf'), dados.get('rg'), dados.get('telefone'),
                dados.get('quarto'), dados.get('contato_emergencia_nome'),
                dados.get('contato_emergencia_telefone'),
                dados.get('contato_emergencia_parentesco'),
                dados.get('observacoes'), paciente_id
            ))

            # Dieta
            existente = conn.execute(
                'SELECT id FROM comorbidades_dieta WHERE paciente_id = ?', (paciente_id,)
            ).fetchone()
            if existente:
                conn.execute('''
                    UPDATE comorbidades_dieta SET
                        peso = ?, altura = ?, comorbidades = ?,
                        dieta_especifica = ?, consistencia_alimento = ?,
                        restricoes_alimentares = ?, alergias_alimentares = ?,
                        observacoes_nutricao = ?, atualizado_em = datetime('now')
                    WHERE paciente_id = ?
                ''', (
                    dados.get('peso') or None, dados.get('altura') or None,
                    dados.get('comorbidades'), dados.get('dieta_especifica'),
                    dados.get('consistencia_alimento'), dados.get('restricoes_alimentares'),
                    dados.get('alergias_alimentares'), dados.get('observacoes_nutricao'),
                    paciente_id
                ))
            else:
                dieta_fields = ['peso', 'altura', 'comorbidades', 'dieta_especifica',
                                'consistencia_alimento', 'restricoes_alimentares',
                                'alergias_alimentares', 'observacoes_nutricao']
                if any(dados.get(f) for f in dieta_fields):
                    conn.execute('''
                        INSERT INTO comorbidades_dieta
                            (paciente_id, peso, altura, comorbidades, dieta_especifica,
                             consistencia_alimento, restricoes_alimentares,
                             alergias_alimentares, observacoes_nutricao)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        paciente_id, dados.get('peso') or None, dados.get('altura') or None,
                        dados.get('comorbidades'), dados.get('dieta_especifica'),
                        dados.get('consistencia_alimento'), dados.get('restricoes_alimentares'),
                        dados.get('alergias_alimentares'), dados.get('observacoes_nutricao'),
                    ))

            # Ficha psicológica
            psico_existente = conn.execute(
                'SELECT id FROM ficha_psicologica WHERE paciente_id = ?', (paciente_id,)
            ).fetchone()
            if psico_existente:
                conn.execute('''
                    UPDATE ficha_psicologica SET
                        antecedentes = ?, diagnosticos_mentais = ?,
                        medicacao_psiquiatrica = ?, comportamento = ?,
                        observacoes = ?, atualizado_em = datetime('now')
                    WHERE paciente_id = ?
                ''', (
                    dados.get('antecedentes'), dados.get('diagnosticos_mentais'),
                    dados.get('medicacao_psiquiatrica'), dados.get('comportamento'),
                    dados.get('observacoes_psico'), paciente_id
                ))
            else:
                psico_fields = ['antecedentes', 'diagnosticos_mentais', 'medicacao_psiquiatrica',
                                'comportamento', 'observacoes_psico']
                if any(dados.get(f) for f in psico_fields):
                    conn.execute('''
                        INSERT INTO ficha_psicologica
                            (paciente_id, antecedentes, diagnosticos_mentais,
                             medicacao_psiquiatrica, comportamento, observacoes)
                        VALUES (?, ?, ?, ?, ?, ?)
                    ''', (
                        paciente_id, dados.get('antecedentes'), dados.get('diagnosticos_mentais'),
                        dados.get('medicacao_psiquiatrica'), dados.get('comportamento'),
                        dados.get('observacoes_psico')
                    ))

            # Profissionais — remove e reinsere
            conn.execute(
                'DELETE FROM profissionais_consultados WHERE paciente_id = ?', (paciente_id,)
            )
            prof_idx = 0
            while dados.get(f'prof_nome_{prof_idx}'):
                prof_nome = dados.get(f'prof_nome_{prof_idx}', '').strip()
                if prof_nome:
                    conn.execute('''
                        INSERT INTO profissionais_consultados
                            (paciente_id, nome, especialidade, data_consulta, observacoes)
                        VALUES (?, ?, ?, ?, ?)
                    ''', (
                        paciente_id, prof_nome,
                        dados.get(f'prof_especialidade_{prof_idx}', ''),
                        dados.get(f'prof_data_{prof_idx}') or None,
                        dados.get(f'prof_obs_{prof_idx}', '')
                    ))
                prof_idx += 1

            # Exames novos (os já existentes continuam gerenciáveis na aba Exames)
            exame_idx = 0
            while dados.get(f'exame_tipo_{exame_idx}'):
                tipo_exame = dados.get(f'exame_tipo_{exame_idx}', '').strip()
                if tipo_exame:
                    arquivo_exame = request.files.get(f'exame_arquivo_{exame_idx}')
                    arquivo_nome_exame = salvar_arquivo_exame(arquivo_exame)
                    conn.execute('''
                        INSERT INTO exames
                            (paciente_id, tipo_exame, data_exame, laboratorio,
                             medico_solicitante, resultado, valores_referencia, arquivo_nome, observacoes)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        paciente_id, tipo_exame,
                        dados.get(f'exame_data_{exame_idx}') or date.today().isoformat(),
                        dados.get(f'exame_laboratorio_{exame_idx}', ''),
                        dados.get(f'exame_medico_{exame_idx}', ''),
                        dados.get(f'exame_resultado_{exame_idx}', ''),
                        dados.get(f'exame_valores_{exame_idx}', ''),
                        arquivo_nome_exame,
                        dados.get(f'exame_obs_{exame_idx}', '')
                    ))
                exame_idx += 1

            flash('Dados atualizados com sucesso!', 'sucesso')
            return redirect(url_for('detalhe_paciente', paciente_id=paciente_id))

        paciente = paciente_existente

        medicamentos_raw = conn.execute(
            'SELECT * FROM medicamentos WHERE paciente_id = ? AND ativo = 1 ORDER BY nome',
            (paciente_id,)
        ).fetchall()
        medicamentos = []
        for m in medicamentos_raw:
            med_dict = dict(m)
            horarios = conn.execute(
                'SELECT horario FROM horarios_medicamento WHERE medicamento_id = ? ORDER BY horario',
                (m['id'],)
            ).fetchall()
            med_dict['horarios'] = [h['horario'] for h in horarios]
            medicamentos.append(med_dict)

        dieta = conn.execute(
            'SELECT * FROM comorbidades_dieta WHERE paciente_id = ?', (paciente_id,)
        ).fetchone()
        ficha_psico = conn.execute(
            'SELECT * FROM ficha_psicologica WHERE paciente_id = ?', (paciente_id,)
        ).fetchone()
        profissionais = conn.execute(
            'SELECT * FROM profissionais_consultados WHERE paciente_id = ? ORDER BY data_consulta DESC',
            (paciente_id,)
        ).fetchall()

    return render_template('paciente_form.html', paciente=dict(paciente),
                           medicamentos=medicamentos, dieta=dieta,
                           ficha_psico=ficha_psico, profissionais=profissionais)


@app.route('/paciente/<int:paciente_id>/excluir', methods=['POST'])
@login_obrigatorio
@apenas_equipe
def excluir_paciente(paciente_id):
    with get_db() as conn:
        conn.execute(
            'UPDATE pacientes SET ativo = 0 WHERE id = ? AND casa_id = ?',
            (paciente_id, casa_id_atual())
        )
    flash('Paciente removido com sucesso.', 'sucesso')
    return redirect(url_for('index'))


# ============================================================================
# CONDIÇÕES
# ============================================================================

@app.route('/paciente/<int:paciente_id>/condicao/nova', methods=['POST'])
@login_obrigatorio
@apenas_equipe
def nova_condicao(paciente_id):
    dados = request.form
    with get_db() as conn:
        if not buscar_paciente_da_casa(conn, paciente_id):
            flash('Paciente não encontrado.', 'erro')
            return redirect(url_for('index'))
        conn.execute('''
            INSERT INTO condicoes (paciente_id, nome, descricao, gravidade, data_diagnostico)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            paciente_id, dados.get('nome'), dados.get('descricao'),
            dados.get('gravidade'), dados.get('data_diagnostico') or None
        ))
    flash('Condição adicionada com sucesso!', 'sucesso')
    return redirect(url_for('detalhe_paciente', paciente_id=paciente_id))


@app.route('/condicao/<int:condicao_id>/excluir', methods=['POST'])
@login_obrigatorio
@apenas_equipe
def excluir_condicao(condicao_id):
    with get_db() as conn:
        cond = conn.execute('''
            SELECT c.paciente_id FROM condicoes c
            JOIN pacientes p ON c.paciente_id = p.id
            WHERE c.id = ? AND p.casa_id = ?
        ''', (condicao_id, casa_id_atual())).fetchone()
        if cond:
            paciente_id = cond['paciente_id']
            conn.execute('DELETE FROM condicoes WHERE id = ?', (condicao_id,))
            flash('Condição removida.', 'sucesso')
            return redirect(url_for('detalhe_paciente', paciente_id=paciente_id))
    return redirect(url_for('index'))


# ============================================================================
# MEDICAMENTOS
# ============================================================================

@app.route('/paciente/<int:paciente_id>/medicamento/novo', methods=['POST'])
@login_obrigatorio
@apenas_equipe
def novo_medicamento(paciente_id):
    dados = request.form
    horarios = request.form.getlist('horarios[]')
    eh_antibiotico = 1 if dados.get('eh_antibiotico') else 0
    data_fim_tratamento = dados.get('data_fim_tratamento') or None
    with get_db() as conn:
        if not buscar_paciente_da_casa(conn, paciente_id):
            flash('Paciente não encontrado.', 'erro')
            return redirect(url_for('index'))
        cursor = conn.execute('''
            INSERT INTO medicamentos
                (paciente_id, nome, dosagem, via_administracao, instrucoes,
                 eh_antibiotico, data_fim_tratamento)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            paciente_id, dados.get('nome'), dados.get('dosagem'),
            dados.get('via_administracao'), dados.get('instrucoes'),
            eh_antibiotico, data_fim_tratamento if eh_antibiotico else None
        ))
        medicamento_id = cursor.lastrowid
        for h in horarios:
            if h.strip():
                conn.execute(
                    'INSERT INTO horarios_medicamento (medicamento_id, horario) VALUES (?, ?)',
                    (medicamento_id, h)
                )
    flash('Medicamento cadastrado com sucesso!', 'sucesso')
    return redirect(url_for('detalhe_paciente', paciente_id=paciente_id))


@app.route('/medicamento/<int:medicamento_id>/excluir', methods=['POST'])
@login_obrigatorio
@apenas_equipe
def excluir_medicamento(medicamento_id):
    with get_db() as conn:
        med = conn.execute('''
            SELECT m.paciente_id FROM medicamentos m
            JOIN pacientes p ON m.paciente_id = p.id
            WHERE m.id = ? AND p.casa_id = ?
        ''', (medicamento_id, casa_id_atual())).fetchone()
        if med:
            paciente_id = med['paciente_id']
            conn.execute('UPDATE medicamentos SET ativo = 0 WHERE id = ?', (medicamento_id,))
            flash('Medicamento removido.', 'sucesso')
            return redirect(url_for('detalhe_paciente', paciente_id=paciente_id))
    return redirect(url_for('index'))


@app.route('/medicamento/<int:medicamento_id>/administrar', methods=['POST'])
@login_obrigatorio
@apenas_equipe
def administrar_dose(medicamento_id):
    dados = request.form
    administrado_por = session.get('usuario_nome', dados.get('administrado_por', ''))
    with get_db() as conn:
        med = conn.execute('''
            SELECT m.paciente_id FROM medicamentos m
            JOIN pacientes p ON m.paciente_id = p.id
            WHERE m.id = ? AND p.casa_id = ?
        ''', (medicamento_id, casa_id_atual())).fetchone()
        if not med:
            flash('Medicamento não encontrado.', 'erro')
            return redirect(url_for('index'))
        conn.execute('''
            INSERT INTO doses_administradas
                (medicamento_id, horario_previsto, administrado_por, observacoes)
            VALUES (?, ?, ?, ?)
        ''', (
            medicamento_id,
            dados.get('horario_previsto') or datetime.now().isoformat(),
            administrado_por, dados.get('observacoes')
        ))
    flash(f'Dose registrada por {administrado_por}!', 'sucesso')
    return redirect(url_for('detalhe_paciente', paciente_id=med['paciente_id']))


# ============================================================================
# FICHA PSICOLÓGICA
# ============================================================================

@app.route('/paciente/<int:paciente_id>/psicologico/salvar', methods=['POST'])
@login_obrigatorio
@apenas_equipe
def salvar_psicologico(paciente_id):
    dados = request.form
    with get_db() as conn:
        if not buscar_paciente_da_casa(conn, paciente_id):
            flash('Paciente não encontrado.', 'erro')
            return redirect(url_for('index'))
        existente = conn.execute(
            'SELECT id FROM ficha_psicologica WHERE paciente_id = ?', (paciente_id,)
        ).fetchone()
        if existente:
            conn.execute('''
                UPDATE ficha_psicologica SET
                    antecedentes = ?, diagnosticos_mentais = ?,
                    medicacao_psiquiatrica = ?, comportamento = ?,
                    observacoes = ?, atualizado_em = datetime('now')
                WHERE paciente_id = ?
            ''', (
                dados.get('antecedentes'), dados.get('diagnosticos_mentais'),
                dados.get('medicacao_psiquiatrica'), dados.get('comportamento'),
                dados.get('observacoes'), paciente_id
            ))
        else:
            conn.execute('''
                INSERT INTO ficha_psicologica
                    (paciente_id, antecedentes, diagnosticos_mentais,
                     medicacao_psiquiatrica, comportamento, observacoes)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                paciente_id, dados.get('antecedentes'), dados.get('diagnosticos_mentais'),
                dados.get('medicacao_psiquiatrica'), dados.get('comportamento'),
                dados.get('observacoes')
            ))
    flash('Ficha psicológica salva com sucesso!', 'sucesso')
    return redirect(url_for('detalhe_paciente', paciente_id=paciente_id))


@app.route('/paciente/<int:paciente_id>/profissional/novo', methods=['POST'])
@login_obrigatorio
@apenas_equipe
def novo_profissional(paciente_id):
    dados = request.form
    with get_db() as conn:
        if not buscar_paciente_da_casa(conn, paciente_id):
            flash('Paciente não encontrado.', 'erro')
            return redirect(url_for('index'))
        conn.execute('''
            INSERT INTO profissionais_consultados
                (paciente_id, nome, especialidade, data_consulta, observacoes)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            paciente_id, dados.get('nome'), dados.get('especialidade'),
            dados.get('data_consulta') or None, dados.get('observacoes')
        ))
    flash('Profissional adicionado!', 'sucesso')
    return redirect(url_for('detalhe_paciente', paciente_id=paciente_id))


@app.route('/profissional/<int:prof_id>/excluir', methods=['POST'])
@login_obrigatorio
@apenas_equipe
def excluir_profissional(prof_id):
    with get_db() as conn:
        prof = conn.execute('''
            SELECT pc.paciente_id FROM profissionais_consultados pc
            JOIN pacientes p ON pc.paciente_id = p.id
            WHERE pc.id = ? AND p.casa_id = ?
        ''', (prof_id, casa_id_atual())).fetchone()
        if prof:
            conn.execute('DELETE FROM profissionais_consultados WHERE id = ?', (prof_id,))
            flash('Profissional removido.', 'sucesso')
            return redirect(url_for('detalhe_paciente', paciente_id=prof['paciente_id']))
    return redirect(url_for('index'))


# ============================================================================
# COMORBIDADES E DIETA
# ============================================================================

@app.route('/paciente/<int:paciente_id>/dieta/salvar', methods=['POST'])
@login_obrigatorio
@apenas_equipe
def salvar_dieta(paciente_id):
    dados = request.form
    with get_db() as conn:
        if not buscar_paciente_da_casa(conn, paciente_id):
            flash('Paciente não encontrado.', 'erro')
            return redirect(url_for('index'))
        existente = conn.execute(
            'SELECT id FROM comorbidades_dieta WHERE paciente_id = ?', (paciente_id,)
        ).fetchone()
        if existente:
            conn.execute('''
                UPDATE comorbidades_dieta SET
                    peso = ?, altura = ?, comorbidades = ?,
                    restricoes_alimentares = ?, dieta_especifica = ?,
                    consistencia_alimento = ?, alergias_alimentares = ?,
                    observacoes_nutricao = ?, atualizado_em = datetime('now')
                WHERE paciente_id = ?
            ''', (
                dados.get('peso') or None, dados.get('altura') or None,
                dados.get('comorbidades'), dados.get('restricoes_alimentares'),
                dados.get('dieta_especifica'), dados.get('consistencia_alimento'),
                dados.get('alergias_alimentares'), dados.get('observacoes_nutricao'),
                paciente_id
            ))
        else:
            conn.execute('''
                INSERT INTO comorbidades_dieta
                    (paciente_id, peso, altura, comorbidades, restricoes_alimentares,
                     dieta_especifica, consistencia_alimento,
                     alergias_alimentares, observacoes_nutricao)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                paciente_id, dados.get('peso') or None, dados.get('altura') or None,
                dados.get('comorbidades'), dados.get('restricoes_alimentares'),
                dados.get('dieta_especifica'), dados.get('consistencia_alimento'),
                dados.get('alergias_alimentares'), dados.get('observacoes_nutricao')
            ))
    flash('Informações de dieta salvas com sucesso!', 'sucesso')
    return redirect(url_for('detalhe_paciente', paciente_id=paciente_id))


# ============================================================================
# EVOLUÇÃO MÉDICA
# ============================================================================

@app.route('/paciente/<int:paciente_id>/evolucoes')
@login_obrigatorio
@apenas_equipe
def listar_evolucoes(paciente_id):
    with get_db() as conn:
        paciente = buscar_paciente_da_casa(conn, paciente_id)
        if not paciente:
            flash('Paciente não encontrado.', 'erro')
            return redirect(url_for('index'))
        evolucoes = conn.execute(
            'SELECT * FROM evolucoes_medicas WHERE paciente_id = ? ORDER BY data_evolucao DESC, id DESC',
            (paciente_id,)
        ).fetchall()
    return render_template('evolucoes.html', paciente=dict(paciente), evolucoes=evolucoes)


@app.route('/paciente/<int:paciente_id>/evolucao/nova', methods=['POST'])
@login_obrigatorio
@apenas_equipe
def nova_evolucao(paciente_id):
    dados = request.form
    with get_db() as conn:
        if not buscar_paciente_da_casa(conn, paciente_id):
            flash('Paciente não encontrado.', 'erro')
            return redirect(url_for('index'))
        conn.execute('''
            INSERT INTO evolucoes_medicas
                (paciente_id, data_evolucao, profissional, especialidade, descricao)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            paciente_id, dados.get('data_evolucao') or date.today().isoformat(),
            dados.get('profissional') or session.get('usuario_nome', ''),
            dados.get('especialidade'), dados.get('descricao')
        ))
    flash('Evolução médica registrada com sucesso!', 'sucesso')
    return redirect(url_for('listar_evolucoes', paciente_id=paciente_id))


@app.route('/evolucao/<int:evolucao_id>/excluir', methods=['POST'])
@login_obrigatorio
@apenas_equipe
def excluir_evolucao(evolucao_id):
    with get_db() as conn:
        evolucao = conn.execute('''
            SELECT e.paciente_id FROM evolucoes_medicas e
            JOIN pacientes p ON e.paciente_id = p.id
            WHERE e.id = ? AND p.casa_id = ?
        ''', (evolucao_id, casa_id_atual())).fetchone()
        if evolucao:
            conn.execute('DELETE FROM evolucoes_medicas WHERE id = ?', (evolucao_id,))
            flash('Registro de evolução removido.', 'sucesso')
            return redirect(url_for('listar_evolucoes', paciente_id=evolucao['paciente_id']))
    return redirect(url_for('index'))


# ============================================================================
# VISITAS
# ============================================================================

@app.route('/paciente/<int:paciente_id>/visitas')
@login_obrigatorio
@apenas_equipe
def listar_visitas(paciente_id):
    with get_db() as conn:
        paciente = buscar_paciente_da_casa(conn, paciente_id)
        if not paciente:
            flash('Paciente não encontrado.', 'erro')
            return redirect(url_for('index'))
        visitas = conn.execute(
            'SELECT * FROM visitas WHERE paciente_id = ? ORDER BY data_visita DESC, hora_entrada DESC',
            (paciente_id,)
        ).fetchall()
    return render_template('visitas.html', paciente=dict(paciente), visitas=visitas)


@app.route('/paciente/<int:paciente_id>/visita/nova', methods=['POST'])
@login_obrigatorio
@apenas_equipe
def nova_visita(paciente_id):
    dados = request.form
    with get_db() as conn:
        if not buscar_paciente_da_casa(conn, paciente_id):
            flash('Paciente não encontrado.', 'erro')
            return redirect(url_for('index'))
        conn.execute('''
            INSERT INTO visitas
                (paciente_id, visitante_nome, visitante_parentesco,
                 data_visita, hora_entrada, hora_saida, observacoes, registrado_por)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            paciente_id, dados.get('visitante_nome'), dados.get('visitante_parentesco'),
            dados.get('data_visita'), dados.get('hora_entrada') or None,
            dados.get('hora_saida') or None, dados.get('observacoes'),
            session.get('usuario_nome', '')
        ))
    flash('Visita registrada com sucesso!', 'sucesso')
    return redirect(url_for('listar_visitas', paciente_id=paciente_id))


@app.route('/visita/<int:visita_id>/excluir', methods=['POST'])
@login_obrigatorio
@apenas_equipe
def excluir_visita(visita_id):
    with get_db() as conn:
        visita = conn.execute('''
            SELECT v.paciente_id FROM visitas v
            JOIN pacientes p ON v.paciente_id = p.id
            WHERE v.id = ? AND p.casa_id = ?
        ''', (visita_id, casa_id_atual())).fetchone()
        if visita:
            conn.execute('DELETE FROM visitas WHERE id = ?', (visita_id,))
            flash('Visita removida.', 'sucesso')
            return redirect(url_for('listar_visitas', paciente_id=visita['paciente_id']))
    return redirect(url_for('index'))


# ============================================================================
# EXAMES
# ============================================================================

@app.route('/paciente/<int:paciente_id>/exames')
@login_obrigatorio
@apenas_equipe
def listar_exames(paciente_id):
    with get_db() as conn:
        paciente = buscar_paciente_da_casa(conn, paciente_id)
        if not paciente:
            flash('Paciente não encontrado.', 'erro')
            return redirect(url_for('index'))
        exames = conn.execute(
            'SELECT * FROM exames WHERE paciente_id = ? ORDER BY data_exame DESC',
            (paciente_id,)
        ).fetchall()
    return render_template('exames.html', paciente=dict(paciente), exames=exames)


@app.route('/paciente/<int:paciente_id>/exame/novo', methods=['POST'])
@login_obrigatorio
@apenas_equipe
def novo_exame(paciente_id):
    dados = request.form
    with get_db() as conn:
        if not buscar_paciente_da_casa(conn, paciente_id):
            flash('Paciente não encontrado.', 'erro')
            return redirect(url_for('index'))

    arquivo = request.files.get('arquivo')
    if arquivo and arquivo.filename and not extensao_permitida(arquivo.filename):
        flash('Formato de arquivo não permitido. Use PDF, JPG, PNG ou WEBP.', 'erro')
        return redirect(url_for('listar_exames', paciente_id=paciente_id))
    arquivo_nome = salvar_arquivo_exame(arquivo)

    with get_db() as conn:
        conn.execute('''
            INSERT INTO exames
                (paciente_id, tipo_exame, data_exame, laboratorio,
                 medico_solicitante, resultado, valores_referencia, arquivo_nome, observacoes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            paciente_id, dados.get('tipo_exame'), dados.get('data_exame'),
            dados.get('laboratorio'), dados.get('medico_solicitante'),
            dados.get('resultado'), dados.get('valores_referencia'),
            arquivo_nome, dados.get('observacoes')
        ))
    flash('Exame registrado com sucesso!', 'sucesso')
    return redirect(url_for('listar_exames', paciente_id=paciente_id))


@app.route('/exame/<int:exame_id>/excluir', methods=['POST'])
@login_obrigatorio
@apenas_equipe
def excluir_exame(exame_id):
    with get_db() as conn:
        exame = conn.execute('''
            SELECT e.paciente_id, e.arquivo_nome FROM exames e
            JOIN pacientes p ON e.paciente_id = p.id
            WHERE e.id = ? AND p.casa_id = ?
        ''', (exame_id, casa_id_atual())).fetchone()
        if exame:
            conn.execute('DELETE FROM exames WHERE id = ?', (exame_id,))
            if exame['arquivo_nome']:
                caminho = os.path.join(UPLOAD_FOLDER, exame['arquivo_nome'])
                if os.path.exists(caminho):
                    os.remove(caminho)
            flash('Exame removido.', 'sucesso')
            return redirect(url_for('listar_exames', paciente_id=exame['paciente_id']))
    return redirect(url_for('index'))


# ============================================================================
# NOTIFICAÇÕES PUSH
# ============================================================================

@app.route('/push/chave-publica')
@login_obrigatorio
def push_chave_publica():
    return jsonify({'publicKey': VAPID_PUBLIC_KEY})


@app.route('/push/inscrever', methods=['POST'])
@login_obrigatorio
@apenas_equipe
def push_inscrever():
    dados = request.get_json(silent=True) or {}
    endpoint = dados.get('endpoint')
    keys = dados.get('keys') or {}
    p256dh = keys.get('p256dh')
    auth = keys.get('auth')
    if not endpoint or not p256dh or not auth:
        return jsonify({'erro': 'Dados de inscrição inválidos.'}), 400
    with get_db() as conn:
        conn.execute('''
            INSERT INTO push_subscriptions (usuario_id, endpoint, p256dh, auth)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(endpoint) DO UPDATE SET
                usuario_id = excluded.usuario_id,
                p256dh = excluded.p256dh,
                auth = excluded.auth
        ''', (session['usuario_id'], endpoint, p256dh, auth))
    return jsonify({'ok': True})


@app.route('/push/desinscrever', methods=['POST'])
@login_obrigatorio
@apenas_equipe
def push_desinscrever():
    dados = request.get_json(silent=True) or {}
    endpoint = dados.get('endpoint')
    if endpoint:
        with get_db() as conn:
            conn.execute('DELETE FROM push_subscriptions WHERE endpoint = ?', (endpoint,))
    return jsonify({'ok': True})


# ============================================================================
# AGENDA
# ============================================================================

@app.route('/agenda')
@login_obrigatorio
@apenas_equipe
def agenda_diaria():
    with get_db() as conn:
        dados = conn.execute('''
            SELECT h.horario, m.id as medicamento_id, m.nome as medicamento,
                   m.dosagem, m.via_administracao, m.instrucoes,
                   p.id as paciente_id, p.nome as paciente, p.quarto
            FROM horarios_medicamento h
            JOIN medicamentos m ON h.medicamento_id = m.id
            JOIN pacientes p ON m.paciente_id = p.id
            WHERE m.ativo = 1 AND p.ativo = 1 AND p.casa_id = ?
            ORDER BY h.horario, p.nome
        ''', (casa_id_atual(),)).fetchall()
        agenda = {}
        for d in dados:
            h = d['horario']
            if h not in agenda:
                agenda[h] = []
            agenda[h].append(dict(d))
    return render_template('agenda.html', agenda=agenda)


# ============================================================================
# INICIALIZAÇÃO
# ============================================================================

if __name__ == '__main__':
    init_db()
    if os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        iniciar_agendador()
    print("=" * 60)
    print("Sistema de Gestão - GestCare")
    print("=" * 60)
    print(f"Banco de dados: {DB_PATH}")
    print("Acesse: http://localhost:5000")
    print("Para parar: Ctrl+C")
    print("=" * 60)
    app.run(debug=True, host='0.0.0.0', port=5000)