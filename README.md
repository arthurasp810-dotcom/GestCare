# 🏡 Sistema de Gestão - Casa de Idosos

Sistema simples e robusto para digitalizar o cadastro de pacientes idosos, suas condições de saúde e o controle de medicamentos e horários.

## ✨ Funcionalidades

- **Cadastro completo de pacientes** — dados pessoais, documentos, quarto, contato de emergência e observações
- **Registro de condições e doenças** — com gravidade (leve / moderada / grave) e data de diagnóstico
- **Controle de medicamentos** — nome, dosagem, via de administração, instruções e múltiplos horários por dia
- **Agenda diária unificada** — todos os medicamentos de todos os pacientes organizados por horário (ótimo para o turno dos cuidadores)
- **Registro de administração de doses** — histórico de quem deu, quando e com que observações
- **100% responsivo** — funciona perfeitamente em computador, tablet e celular
- **Banco de dados SQLite embutido** — zero configuração externa

## 🛠️ Tecnologia

- **Python + Flask** (backend robusto e leve)
- **SQLite** (banco de dados — cria o arquivo automaticamente)
- **HTML + CSS + JavaScript** (frontend responsivo)
- **Jinja2** (templates)

## 📦 Estrutura de arquivos

```
casa_idosos/
├── app.py                      # Aplicação Flask principal (backend + rotas)
├── requirements.txt            # Dependências Python
├── database/
│   └── casa_idosos.db          # Banco SQLite (criado automaticamente)
├── static/
│   ├── css/style.css           # Estilos
│   └── js/app.js               # JavaScript
└── templates/
    ├── base.html               # Layout base
    ├── index.html              # Lista de pacientes
    ├── paciente_form.html      # Cadastro / edição
    ├── paciente_detalhe.html   # Detalhes + condições + medicamentos
    └── agenda.html             # Agenda diária de medicações
```

## 🚀 Como instalar e executar

### 1. Pré-requisitos
- **Python 3.8 ou superior** instalado no computador
  - Windows: baixe em [python.org](https://www.python.org/downloads/) (marque "Add Python to PATH" na instalação)
  - Mac/Linux: normalmente já vem instalado

### 2. Instalar a dependência
Abra o terminal na pasta do projeto e execute:

```bash
pip install -r requirements.txt
```

Ou, se preferir, diretamente:

```bash
pip install flask
```

### 3. Executar o sistema
Na pasta do projeto, rode:

```bash
python app.py
```

Você verá algo como:
```
============================================================
Sistema de Gestão - Casa de Idosos
============================================================
Banco de dados: /caminho/casa_idosos.db
Acesse: http://localhost:5000
Para parar: Ctrl+C
============================================================
```

### 4. Abrir no navegador
Abra qualquer navegador (Chrome, Firefox, Edge, Safari) e acesse:

**http://localhost:5000**

Pronto! O sistema está rodando.

## 📱 Usar no celular (mesma rede)

Se quiser acessar pelo celular na mesma rede Wi-Fi:

1. Descubra o IP do computador que está rodando o sistema:
   - Windows: `ipconfig` no terminal (procure "IPv4")
   - Mac/Linux: `ifconfig` (ou `ip addr`)
2. No celular, acesse: `http://SEU_IP:5000` (ex: `http://192.168.1.10:5000`)

## 💾 Backup dos dados

Todos os dados ficam guardados em um único arquivo:
```
database/casa_idosos.db
```

Para fazer backup, **basta copiar este arquivo** para outro local (pen drive, nuvem, etc). Para restaurar, apenas cole-o de volta no mesmo lugar.

## 🔒 Recomendações para uso em produção

Este sistema foi projetado para uso interno em uma casa de idosos. Se for colocá-lo num servidor real, considere:

- Trocar a `secret_key` no arquivo `app.py` por uma chave longa e aleatória
- Desativar `debug=True` na última linha do `app.py`
- Usar um servidor WSGI como Gunicorn ou Waitress em vez do servidor de desenvolvimento do Flask
- Adicionar autenticação (login/senha) se houver múltiplos usuários
- Fazer backups diários do arquivo `casa_idosos.db`

## 🩺 Fluxo de uso sugerido

1. **Cadastrar paciente** → preencha dados pessoais, contato de emergência
2. **Adicionar condições** → hipertensão, diabetes, etc, com gravidade
3. **Adicionar medicamentos** → um por vez, com todos os horários do dia
4. **Consultar "Agenda"** → no início de cada turno, veja tudo organizado por horário
5. **Registrar doses** → clique em "Administrado" quando der o remédio (fica registrado no histórico)

---

Desenvolvido com cuidado para quem cuida. 💙
