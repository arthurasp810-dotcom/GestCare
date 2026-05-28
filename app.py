import bcrypt
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, session
import sqlite3
import os
from datetime import datetime, date
from contextlib import contextmanager

app = Flask(__name__)
app.secret_key = 'casa-idosos-sistema-2026-troque-em-producao'

DB_PATH = os.path.join(os.path.dirname(__file__), 'database', 'casa_idosos.db')

@contextmanager
def get_db():
    """Gerenciador de contexto para conexão com o banco."""
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
    """Cria as tabelas do banco de dados se não existirem."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

    with get_db() as conn:
        cursor = conn.cursor()

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS pacientes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                data_nascimento DATE NOT NULL,
                sexo TEXT,
                cpf TEXT,
                rg TEXT,
                telefone TEXT,
                quarto TEXT,
                contato_emergencia_nome TEXT,
                contato_emergencia_telefone TEXT,
                contato_emergencia_parentesco TEXT,
                observacoes TEXT,
                data_cadastro TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                ativo INTEGER DEFAULT 1
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS condicoes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                paciente_id INTEGER NOT NULL,
                nome TEXT NOT NULL,
                descricao TEXT,
                gravidade TEXT,
                data_diagnostico DATE,
                data_cadastro TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (paciente_id) REFERENCES pacientes(id) ON DELETE CASCADE
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS medicamentos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                paciente_id INTEGER NOT NULL,
                nome TEXT NOT NULL,
                dosagem TEXT,
                via_administracao TEXT,
                instrucoes TEXT,
                ativo INTEGER DEFAULT 1,
                data_cadastro TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (paciente_id) REFERENCES pacientes(id) ON DELETE CASCADE
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS horarios_medicamento (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                medicamento_id INTEGER NOT NULL,
                horario TIME NOT NULL,
                FOREIGN KEY (medicamento_id) REFERENCES medicamentos(id) ON DELETE CASCADE
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS doses_administradas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                medicamento_id INTEGER NOT NULL,
                horario_previsto TIMESTAMP NOT NULL,
                horario_administrado TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                administrado_por TEXT,
                observacoes TEXT,
                FOREIGN KEY (medicamento_id) REFERENCES medicamentos(id) ON DELETE CASCADE
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS usuarios (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                nome      TEXT NOT NULL,
                email     TEXT NOT NULL UNIQUE,
                senha     TEXT NOT NULL,
                perfil    TEXT NOT NULL DEFAULT 'enfermeiro',
                ativo     INTEGER NOT NULL DEFAULT 1,
                criado_em TEXT DEFAULT (datetime('now'))
            )
        ''')

        conn.commit()


def calcular_idade(data_nasc_str):
    """Calcula idade a partir de string de data (YYYY-MM-DD)."""
    if not data_nasc_str:
        return None
    try:
        data_nasc = datetime.strptime(data_nasc_str, '%Y-%m-%d').date()
        hoje = date.today()
        idade = hoje.year - data_nasc.year - (
            (hoje.month, hoje.day) < (data_nasc.month, data_nasc.day)
        )
        return idade
    except ValueError:
        return None

def hash_senha(senha):
    """Gera o hash seguro da senha."""
    return bcrypt.hashpw(senha.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


def verificar_senha(senha, hash_armazenado):
    """Verifica se a senha bate com o hash."""
    return bcrypt.checkpw(senha.encode('utf-8'), hash_armazenado.encode('utf-8'))


def login_obrigatorio(f):
    """Decorator: redireciona para login se não estiver logado."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'usuario_id' not in session:
            flash('Faça login para acessar esta página.', 'erro')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


def apenas_admin(f):
    """Decorator: só admin pode acessar."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('usuario_perfil') != 'admin':
            flash('Acesso restrito a administradores.', 'erro')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Página de login."""
    if 'usuario_id' in session:
        return redirect(url_for('index'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        senha = request.form.get('senha', '')

        with get_db() as conn:
            usuario = conn.execute(
                'SELECT * FROM usuarios WHERE email = ? AND ativo = 1',
                (email,)
            ).fetchone()

        if usuario and verificar_senha(senha, usuario['senha']):
            session.permanent = True
            session['usuario_id']     = usuario['id']
            session['usuario_nome']   = usuario['nome']
            session['usuario_perfil'] = usuario['perfil']
            flash(f'Bem-vindo(a), {usuario["nome"]}!', 'sucesso')
            return redirect(url_for('index'))
        else:
            flash('E-mail ou senha incorretos.', 'erro')

    return render_template('login.html')


@app.route('/logout')
def logout():
    """Encerra a sessão."""
    nome = session.get('usuario_nome', '')
    session.clear()
    flash(f'Até logo, {nome}!', 'sucesso')
    return redirect(url_for('login'))


@app.route('/usuarios')
@login_obrigatorio
@apenas_admin
def listar_usuarios():
    """Lista todos os usuários (só admin)."""
    with get_db() as conn:
        usuarios = conn.execute(
            'SELECT id, nome, email, perfil, ativo, criado_em FROM usuarios ORDER BY nome'
        ).fetchall()
    return render_template('usuarios.html', usuarios=usuarios)


@app.route('/usuarios/novo', methods=['GET', 'POST'])
@login_obrigatorio
@apenas_admin
def novo_usuario():
    """Criar novo usuário (só admin)."""
    if request.method == 'POST':
        nome   = request.form.get('nome', '').strip()
        email  = request.form.get('email', '').strip().lower()
        senha  = request.form.get('senha', '')
        perfil = request.form.get('perfil', 'enfermeiro')

        if not nome or not email or not senha:
            flash('Preencha todos os campos obrigatórios.', 'erro')
            return render_template('usuario_form.html')

        if len(senha) < 6:
            flash('A senha deve ter pelo menos 6 caracteres.', 'erro')
            return render_template('usuario_form.html')

        try:
            with get_db() as conn:
                conn.execute(
                    'INSERT INTO usuarios (nome, email, senha, perfil) VALUES (?, ?, ?, ?)',
                    (nome, email, hash_senha(senha), perfil)
                )
            flash(f'Usuário {nome} criado com sucesso!', 'sucesso')
            return redirect(url_for('listar_usuarios'))
        except Exception:
            flash('Erro: este e-mail já está cadastrado.', 'erro')

    return render_template('usuario_form.html')


@app.route('/usuarios/<int:usuario_id>/toggle', methods=['POST'])
@login_obrigatorio
@apenas_admin
def toggle_usuario(usuario_id):
    """Ativar ou desativar usuário."""
    if usuario_id == session.get('usuario_id'):
        flash('Você não pode desativar sua própria conta.', 'erro')
        return redirect(url_for('listar_usuarios'))

    with get_db() as conn:
        usuario = conn.execute(
            'SELECT ativo, nome FROM usuarios WHERE id = ?', (usuario_id,)
        ).fetchone()
        if usuario:
            novo_status = 0 if usuario['ativo'] else 1
            conn.execute(
                'UPDATE usuarios SET ativo = ? WHERE id = ?',
                (novo_status, usuario_id)
            )
            acao = 'ativado' if novo_status else 'desativado'
            flash(f'Usuário {usuario["nome"]} {acao}.', 'sucesso')

    return redirect(url_for('listar_usuarios'))

@app.route('/')
@login_obrigatorio
def index():
    """Página inicial — lista de pacientes."""
    with get_db() as conn:
        pacientes = conn.execute('''
            SELECT p.*,
                   (SELECT COUNT(*) FROM medicamentos WHERE paciente_id = p.id AND ativo = 1) as total_medicamentos,
                   (SELECT COUNT(*) FROM condicoes WHERE paciente_id = p.id) as total_condicoes
            FROM pacientes p
            WHERE p.ativo = 1
            ORDER BY p.nome
        ''').fetchall()

        pacientes_list = []
        for p in pacientes:
            p_dict = dict(p)
            p_dict['idade'] = calcular_idade(p_dict['data_nascimento'])
            pacientes_list.append(p_dict)

    return render_template('index.html', pacientes=pacientes_list)


@app.route('/paciente/novo', methods=['GET', 'POST'])
@login_obrigatorio
def novo_paciente():
    """Cadastrar novo paciente com medicamentos."""
    if request.method == 'POST':
        dados = request.form

        with get_db() as conn:
            cursor = conn.execute('''
                INSERT INTO pacientes (
                    nome, data_nascimento, sexo, cpf, rg, telefone, quarto,
                    contato_emergencia_nome, contato_emergencia_telefone,
                    contato_emergencia_parentesco, observacoes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                dados.get('nome'),
                dados.get('data_nascimento'),
                dados.get('sexo'),
                dados.get('cpf'),
                dados.get('rg'),
                dados.get('telefone'),
                dados.get('quarto'),
                dados.get('contato_emergencia_nome'),
                dados.get('contato_emergencia_telefone'),
                dados.get('contato_emergencia_parentesco'),
                dados.get('observacoes')
            ))
            paciente_id = cursor.lastrowid

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

                info_freq = _montar_info_frequencia(freq_tipo, doses_dia, intervalo, dias_sem)
                instrucoes_completas = f"{info_freq}\n{instrucoes}".strip() if instrucoes else info_freq

                if nome:
                    cur2 = conn.execute('''
                        INSERT INTO medicamentos (paciente_id, nome, dosagem, via_administracao, instrucoes)
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

        flash('Paciente cadastrado com sucesso!', 'sucesso')
        return redirect(url_for('detalhe_paciente', paciente_id=paciente_id))

    return render_template('paciente_form.html', paciente=None, medicamentos=[])


def _montar_info_frequencia(tipo, doses_dia, intervalo, dias_semana):
    """Gera texto descritivo da frequência."""
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
def detalhe_paciente(paciente_id):
    """Detalhes de um paciente."""
    with get_db() as conn:
        paciente = conn.execute(
            'SELECT * FROM pacientes WHERE id = ?', (paciente_id,)
        ).fetchone()

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
            medicamentos.append(med_dict)

    return render_template(
        'paciente_detalhe.html',
        paciente=paciente_dict,
        condicoes=condicoes,
        medicamentos=medicamentos
    )


@app.route('/paciente/<int:paciente_id>/editar', methods=['GET', 'POST'])
@login_obrigatorio
def editar_paciente(paciente_id):
    """Editar dados do paciente."""
    with get_db() as conn:
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
            flash('Dados atualizados com sucesso!', 'sucesso')
            return redirect(url_for('detalhe_paciente', paciente_id=paciente_id))

        paciente = conn.execute(
            'SELECT * FROM pacientes WHERE id = ?', (paciente_id,)
        ).fetchone()

        if not paciente:
            flash('Paciente não encontrado.', 'erro')
            return redirect(url_for('index'))

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

    return render_template('paciente_form.html', paciente=dict(paciente), medicamentos=medicamentos)


@app.route('/paciente/<int:paciente_id>/excluir', methods=['POST'])
@login_obrigatorio
def excluir_paciente(paciente_id):
    """Soft delete do paciente."""
    with get_db() as conn:
        conn.execute('UPDATE pacientes SET ativo = 0 WHERE id = ?', (paciente_id,))
    flash('Paciente removido com sucesso.', 'sucesso')
    return redirect(url_for('index'))


@app.route('/paciente/<int:paciente_id>/condicao/nova', methods=['POST'])
@login_obrigatorio
def nova_condicao(paciente_id):
    """Adicionar nova condição."""
    dados = request.form
    with get_db() as conn:
        conn.execute('''
            INSERT INTO condicoes (paciente_id, nome, descricao, gravidade, data_diagnostico)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            paciente_id,
            dados.get('nome'),
            dados.get('descricao'),
            dados.get('gravidade'),
            dados.get('data_diagnostico') or None
        ))
    flash('Condição adicionada com sucesso!', 'sucesso')
    return redirect(url_for('detalhe_paciente', paciente_id=paciente_id))


@app.route('/condicao/<int:condicao_id>/excluir', methods=['POST'])
@login_obrigatorio
def excluir_condicao(condicao_id):
    """Remover condição."""
    with get_db() as conn:
        cond = conn.execute(
            'SELECT paciente_id FROM condicoes WHERE id = ?', (condicao_id,)
        ).fetchone()
        if cond:
            paciente_id = cond['paciente_id']
            conn.execute('DELETE FROM condicoes WHERE id = ?', (condicao_id,))
            flash('Condição removida.', 'sucesso')
            return redirect(url_for('detalhe_paciente', paciente_id=paciente_id))
    return redirect(url_for('index'))


@app.route('/paciente/<int:paciente_id>/medicamento/novo', methods=['POST'])
@login_obrigatorio
def novo_medicamento(paciente_id):
    """Adicionar novo medicamento com horários."""
    dados = request.form
    horarios = request.form.getlist('horarios[]')

    with get_db() as conn:
        cursor = conn.execute('''
            INSERT INTO medicamentos (paciente_id, nome, dosagem, via_administracao, instrucoes)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            paciente_id,
            dados.get('nome'),
            dados.get('dosagem'),
            dados.get('via_administracao'),
            dados.get('instrucoes')
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
def excluir_medicamento(medicamento_id):
    """Remover medicamento (soft delete)."""
    with get_db() as conn:
        med = conn.execute(
            'SELECT paciente_id FROM medicamentos WHERE id = ?', (medicamento_id,)
        ).fetchone()
        if med:
            paciente_id = med['paciente_id']
            conn.execute('UPDATE medicamentos SET ativo = 0 WHERE id = ?', (medicamento_id,))
            flash('Medicamento removido.', 'sucesso')
            return redirect(url_for('detalhe_paciente', paciente_id=paciente_id))
    return redirect(url_for('index'))


@app.route('/medicamento/<int:medicamento_id>/administrar', methods=['POST'])
@login_obrigatorio
def administrar_dose(medicamento_id):
    """Registrar administração de uma dose."""
    dados = request.form
    administrado_por = session.get('usuario_nome', dados.get('administrado_por', ''))

    with get_db() as conn:
        conn.execute('''
            INSERT INTO doses_administradas
                (medicamento_id, horario_previsto, administrado_por, observacoes)
            VALUES (?, ?, ?, ?)
        ''', (
            medicamento_id,
            dados.get('horario_previsto') or datetime.now().isoformat(),
            administrado_por,
            dados.get('observacoes')
        ))
        med = conn.execute(
            'SELECT paciente_id FROM medicamentos WHERE id = ?', (medicamento_id,)
        ).fetchone()

    flash(f'Dose registrada por {administrado_por}!', 'sucesso')
    return redirect(url_for('detalhe_paciente', paciente_id=med['paciente_id']))



@app.route('/agenda')
@login_obrigatorio
def agenda_diaria():
    """Agenda diária de medicações."""
    with get_db() as conn:
        dados = conn.execute('''
            SELECT
                h.horario,
                m.id as medicamento_id,
                m.nome as medicamento,
                m.dosagem,
                m.via_administracao,
                m.instrucoes,
                p.id as paciente_id,
                p.nome as paciente,
                p.quarto
            FROM horarios_medicamento h
            JOIN medicamentos m ON h.medicamento_id = m.id
            JOIN pacientes p ON m.paciente_id = p.id
            WHERE m.ativo = 1 AND p.ativo = 1
            ORDER BY h.horario, p.nome
        ''').fetchall()

        agenda = {}
        for d in dados:
            h = d['horario']
            if h not in agenda:
                agenda[h] = []
            agenda[h].append(dict(d))

    return render_template('agenda.html', agenda=agenda)


if __name__ == '__main__':
    init_db()
    print("=" * 60)
    print("Sistema de Gestão - Casa de Idosos")
    print("=" * 60)
    print(f"Banco de dados: {DB_PATH}")
    print("Acesse: http://localhost:5000")
    print("Para parar: Ctrl+C")
    print("=" * 60)
    app.run(debug=True, host='0.0.0.0', port=5000)