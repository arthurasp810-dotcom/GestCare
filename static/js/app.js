// ── MODAIS ────────────────────────────────────────────────
function abrirModal(id) {
  const modal = document.getElementById(id);
  if (!modal) return;
  modal.classList.add('ativo');
  document.body.style.overflow = 'hidden';
  // Foca o primeiro campo para facilitar digitação no celular
  setTimeout(() => {
    const primeiro = modal.querySelector('input, select, textarea');
    if (primeiro) primeiro.focus();
  }, 300);
}

function fecharModal(id) {
  const modal = document.getElementById(id);
  if (!modal) return;
  modal.classList.remove('ativo');
  document.body.style.overflow = '';
}

// Fechar clicando fora do conteúdo
document.addEventListener('click', e => {
  if (e.target.classList.contains('modal')) {
    e.target.classList.remove('ativo');
    document.body.style.overflow = '';
  }
});

// Fechar com ESC
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    document.querySelectorAll('.modal.ativo').forEach(m => {
      m.classList.remove('ativo');
    });
    document.body.style.overflow = '';
  }
});

// ── HORÁRIOS DINÂMICOS ───────────────────────────────────
function adicionarHorario(containerId) {
  const container = document.getElementById(containerId);
  if (!container) return;
  const item = document.createElement('div');
  item.className = 'horario-item';
  item.innerHTML = `
    <input type="time" name="horarios[]" class="form-input" required>
    <button type="button" class="btn-remover-horario"
            onclick="removerHorario(this)">Remover</button>
  `;
  container.appendChild(item);
  item.querySelector('input').focus();
}

function removerHorario(botao) {
  const lista = botao.parentElement.parentElement;
  if (lista.querySelectorAll('.horario-item').length > 1) {
    botao.parentElement.remove();
  } else {
    mostrarToast('É necessário ter pelo menos um horário.', 'erro');
  }
}

// ── CONFIRMAÇÃO DE EXCLUSÃO ──────────────────────────────
function confirmarExclusao(msg) {
  return confirm(msg || 'Tem certeza? Esta ação não pode ser desfeita.');
}

// ── FORMATAÇÃO DE CPF ────────────────────────────────────
function formatarCPF(input) {
  let v = input.value.replace(/\D/g, '').slice(0, 11);
  if (v.length > 9)      v = v.replace(/(\d{3})(\d{3})(\d{3})(\d{1,2})/, '$1.$2.$3-$4');
  else if (v.length > 6) v = v.replace(/(\d{3})(\d{3})(\d{1,3})/, '$1.$2.$3');
  else if (v.length > 3) v = v.replace(/(\d{3})(\d{1,3})/, '$1.$2');
  input.value = v;
}

// ── FORMATAÇÃO DE TELEFONE ───────────────────────────────
function formatarTelefone(input) {
  let v = input.value.replace(/\D/g, '').slice(0, 11);
  if (v.length > 10)     v = v.replace(/(\d{2})(\d{5})(\d{4})/, '($1) $2-$3');
  else if (v.length > 6) v = v.replace(/(\d{2})(\d{4})(\d{1,4})/, '($1) $2-$3');
  else if (v.length > 2) v = v.replace(/(\d{2})(\d{1,5})/, '($1) $2');
  input.value = v;
}

// ── TOAST / SNACKBAR ─────────────────────────────────────
function mostrarToast(mensagem, tipo) {
  let toast = document.getElementById('_toast');
  if (!toast) {
    toast = document.createElement('div');
    toast.id = '_toast';
    toast.className = 'toast';
    document.body.appendChild(toast);
  }
  toast.textContent = mensagem;
  toast.style.background = tipo === 'erro' ? 'var(--cor-erro)' : '#1f2937';
  toast.classList.add('visivel');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => toast.classList.remove('visivel'), 3000);
}

// ── INIT ─────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {

  // Aplica formatadores nos campos marcados
  document.querySelectorAll('[data-formato="cpf"]').forEach(el =>
    el.addEventListener('input', () => formatarCPF(el))
  );
  document.querySelectorAll('[data-formato="telefone"]').forEach(el =>
    el.addEventListener('input', () => formatarTelefone(el))
  );

  // Alertas flash somem depois de 5 segundos
  setTimeout(() => {
    document.querySelectorAll('.alerta').forEach(a => {
      a.style.transition = 'opacity .5s';
      a.style.opacity = '0';
      setTimeout(() => a.remove(), 500);
    });
  }, 5000);

  // Desabilita botão de submit após clique (evita duplo envio)
  document.querySelectorAll('form').forEach(form => {
    form.addEventListener('submit', () => {
      const btn = form.querySelector('[type="submit"]');
      if (btn) {
        btn.disabled = true;
        const original = btn.textContent;
        btn.textContent = 'Aguarde...';
        setTimeout(() => {
          btn.disabled = false;
          btn.textContent = original;
        }, 5000);
      }
    });
  });

  // Registra Service Worker para funcionamento offline
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/static/sw.js').catch(() => {});
  }
});