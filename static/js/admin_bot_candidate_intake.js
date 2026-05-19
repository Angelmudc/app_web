(function () {
  const root = document.getElementById('candidate-intake-root');
  if (!root) return;
  const rowsEl = document.getElementById('intake-rows');
  const metricsEl = document.getElementById('intake-metrics');
  const detailEl = document.getElementById('intake-detail');
  const btnApprove = document.getElementById('btn-approve');
  const btnReject = document.getElementById('btn-reject');
  const btnDuplicate = document.getElementById('btn-duplicate');
  const btnFollowup = document.getElementById('btn-followup');
  const btnEdit = document.getElementById('btn-edit');
  const csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';

  let selectedId = null;
  let selectedDetail = null;
  const REFRESH_MS = 2500;

  function badge(status) {
    const m = {
      pending_review: 'warning',
      approved: 'success',
      rejected: 'danger',
      duplicate: 'danger',
      needs_followup: 'secondary',
      incomplete: 'dark'
    };
    return `<span class="badge text-bg-${m[status] || 'light'}">${status || '-'}</span>`;
  }

  function scoreBadge(score) {
    const val = parseInt(score || '0', 10) || 0;
    const color = val >= 80 ? 'success' : (val >= 50 ? 'warning' : 'danger');
    return `<span class="badge text-bg-${color}">${val}</span>`;
  }

  function req(url, opts) {
    const options = opts || {};
    options.headers = Object.assign({ 'Content-Type': 'application/json', 'X-CSRFToken': csrf }, options.headers || {});
    return fetch(url, options).then(r => r.json().then(j => ({ status: r.status, body: j })));
  }

  function renderRows(items) {
    rowsEl.innerHTML = '';
    (items || []).forEach((x) => {
      const tr = document.createElement('tr');
      if (selectedId === x.intake_id) tr.classList.add('table-active');
      tr.innerHTML = `
        <td>${x.name || '-'}</td>
        <td>${x.phone || '-'}</td>
        <td>${x.age || '-'}</td>
        <td>${x.city_sector || '-'}</td>
        <td>${x.availability || '-'}</td>
        <td><small>${x.created_at || '-'}</small></td>
        <td>${badge(x.status)}</td>
        <td>${scoreBadge(x.quality_score)}</td>
        <td>${x.origin || '-'}</td>`;
      tr.style.cursor = 'pointer';
      tr.addEventListener('click', function () {
        selectedId = x.intake_id;
        loadDetail();
      });
      rowsEl.appendChild(tr);
    });
  }

  function renderMetrics(m) {
    const tiles = [
      ['Completadas hoy', m.completed_today],
      ['Aprobadas', m.approved],
      ['Rechazadas', m.rejected],
      ['Duplicadas', m.duplicated],
      ['Promedio score', m.avg_score],
      ['Tiempo prom. entrevista (min)', m.avg_interview_minutes]
    ];
    metricsEl.innerHTML = tiles.map(([k, v]) => (
      `<div class="col-md-2 col-6"><div class="card border-0 shadow-sm"><div class="card-body py-2"><div class="small text-muted">${k}</div><div class="h6 mb-0">${v || 0}</div></div></div></div>`
    )).join('');
  }

  function renderDetail(d) {
    selectedDetail = d;
    const duplicates = (d.duplicates || []).map(x => `<li>${x.type} #${x.candidate_id || '-'}</li>`).join('');
    const flags = (d.quality_flags || []).join(', ') || '-';
    const aiLogs = (d.ai_logs || []).slice(0, 10).map(x => `<li>${x.decision_type} · ${x.decision_result}</li>`).join('');
    detailEl.innerHTML = `
      <div><strong>${d.mapped_fields.nombre_completo || '-'}</strong> ${badge(d.status)}</div>
      <div class="mt-1">Score: ${scoreBadge(d.quality_score)} · Flags: ${flags}</div>
      <div class="mt-1">Errores/confusión: ${d.invalid_answers_count || 0}</div>
      <div class="mt-1">Resumen: ${(d.summary || '-')}</div>
      <div class="mt-2"><strong>Detectados futuros</strong><pre>${JSON.stringify(d.detected_future_data || {}, null, 2)}</pre></div>
      <div class="mt-2"><strong>Datos capturados</strong><pre>${JSON.stringify(d.collected_data || {}, null, 2)}</pre></div>
      <div class="mt-2"><strong>Duplicados detectados</strong><ul>${duplicates || '<li>ninguno</li>'}</ul></div>
      <div class="mt-2"><strong>Logs IA</strong><ul>${aiLogs || '<li>sin logs</li>'}</ul></div>
      <div class="mt-2"><a class="btn btn-sm btn-outline-secondary" href="/admin/bot/conversaciones/${d.conversation_id}">Ver conversación</a></div>
    `;
  }

  function refreshList() {
    return req('/admin/bot/candidate-intake/pending.json').then((res) => {
      if (!res.body || !res.body.ok) return;
      renderRows(res.body.items || []);
      if (!selectedId && (res.body.items || []).length > 0) {
        selectedId = res.body.items[0].intake_id;
        loadDetail();
      }
    });
  }

  function refreshMetrics() {
    return req('/admin/bot/candidate-intake/metrics.json').then((res) => {
      if (!res.body || !res.body.ok) return;
      renderMetrics(res.body);
    });
  }

  function loadDetail() {
    if (!selectedId) return Promise.resolve();
    return req(`/admin/bot/candidate-intake/${selectedId}.json`).then((res) => {
      if (!res.body || !res.body.ok) return;
      renderDetail(res.body.intake || {});
      refreshList();
    });
  }

  function runAction(action, extra) {
    if (!selectedId) return;
    req(`/admin/bot/candidate-intake/${selectedId}/action`, {
      method: 'POST',
      body: JSON.stringify(Object.assign({ action: action }, extra || {}))
    }).then((res) => {
      if (!res.body || !res.body.ok) {
        alert((res.body && res.body.error) || 'Error');
        return;
      }
      refreshMetrics();
      loadDetail();
    });
  }

  btnApprove.addEventListener('click', function () { runAction('approve', {}); });
  btnReject.addEventListener('click', function () {
    const note = prompt('Motivo de rechazo');
    if (!note) return;
    runAction('reject', { note: note });
  });
  btnDuplicate.addEventListener('click', function () {
    const note = prompt('Nota de duplicado');
    runAction('mark_duplicate', { note: note || '' });
  });
  btnFollowup.addEventListener('click', function () {
    const note = prompt('Indicación de seguimiento');
    runAction('followup', { note: note || '' });
  });
  btnEdit.addEventListener('click', function () {
    if (!selectedDetail) return;
    const name = prompt('Nombre', selectedDetail.mapped_fields.nombre_completo || '') || '';
    const city = prompt('Ciudad', selectedDetail.mapped_fields.ciudad || '') || '';
    const availability = prompt('Disponibilidad', selectedDetail.mapped_fields.modalidad_trabajo_preferida || '') || '';
    runAction('edit_before_approve', { fields: { name: name, city: city, availability: availability } });
  });

  refreshList();
  refreshMetrics();
  window.setInterval(function () {
    if (document.hidden) return;
    refreshList();
    refreshMetrics();
  }, REFRESH_MS);
})();
