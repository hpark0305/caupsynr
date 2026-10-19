/* Sensitive response content is fetched only after a CSRF-protected, audited read. */
(() => {
  const text = (tag, value, className) => {
    const node = document.createElement(tag);
    node.textContent = value == null ? '' : String(value);
    if (className) node.className = className;
    return node;
  };
  const setOpen = (button, open) => {
    button.setAttribute('aria-expanded', String(open));
    const arrow = button.querySelector('.mm-arrow');
    if (arrow) arrow.classList.toggle('open', open);
  };
  document.addEventListener('click', async event => {
    const button = event.target.closest('.mm-detail-button');
    if (!button || button.disabled) return;
    const detail = document.getElementById(button.getAttribute('aria-controls'));
    if (button.getAttribute('aria-expanded') === 'true') {
      detail.replaceChildren();
      detail.hidden = true;
      setOpen(button, false);
      return;
    }
    button.disabled = true;
    detail.hidden = false;
    detail.replaceChildren(text('p', '열람 기록을 저장하고 불러오는 중…', 'mm-detail-status'));
    try {
      const kind = encodeURIComponent(button.dataset.kind);
      const id = encodeURIComponent(button.dataset.recordId);
      const response = await fetch(`/portal/mindmate/detail/${kind}/${id}`, {
        method: 'POST', credentials: 'same-origin', cache: 'no-store',
        headers: {'X-CSRFToken': document.querySelector('meta[name="csrf-token"]').content,
                  'Content-Type': 'application/json'}, body: '{}',
        signal: AbortSignal.timeout(15000),
      });
      if (!response.ok || response.redirected) throw new Error('Read denied');
      const result = await response.json();
      if (!result.ok) throw new Error('Read failed');
      const table = document.createElement('table');
      table.className = 'kv-table';
      for (const field of [{label: '참여자', value: result.participant}, ...result.fields]) {
        const row = document.createElement('tr');
        const heading = text('th', field.label);
        heading.scope = 'row';
        row.append(heading, text('td', field.value));
        table.append(row);
      }
      detail.replaceChildren(text('p', '열람 기록이 저장되었습니다. 펼친 내용은 열람 시점의 응답입니다.', 'mm-detail-status'), table);
      setOpen(button, true);
    } catch (_) {
      detail.replaceChildren(text('p', '상세를 불러오지 못했습니다. 로그인 상태를 확인하고 다시 눌러주세요.', 'mm-detail-status'));
      setOpen(button, false);
    } finally {
      button.disabled = false;
    }
  });

  const box = document.getElementById('liveListBox');
  if (!box) return;
  const rows = new Map();
  let nextId = 0;
  function render(events) {
    const active = new Set();
    for (const event of events) {
      const kind = event.type === 'sync' ? 'survey' : event.type;
      if (!['survey', 'music', 'open'].includes(kind)) continue;
      const key = `${kind}:${event.id}`;
      active.add(key);
      let entry = rows.get(key);
      if (!entry) {
        const node = text('div', '', 'mm-entry');
        const button = text('button', '', 'mm-detail-button');
        button.type = 'button';
        button.dataset.kind = kind;
        button.dataset.recordId = event.id;
        button.setAttribute('aria-expanded', 'false');
        const detail = text('div', '', 'mm-detail');
        detail.id = `live-detail-${++nextId}`;
        detail.hidden = true;
        button.setAttribute('aria-controls', detail.id);
        const badge = text('span', '', 'mm-badge');
        const participant = text('strong', '');
        const date = text('span', '', 'mm-time');
        const at = text('span', '', 'mm-time');
        button.append(badge, participant, date, at, text('span', '▶', 'mm-arrow'));
        node.append(button, detail);
        entry = {node, button, badge, participant, date, at};
        rows.set(key, entry);
      }
      entry.badge.textContent = event.label;
      entry.participant.textContent = event.participant;
      entry.date.textContent = event.date || '';
      entry.at.textContent = event.at || '';
    }
    // Keep the actual DOM node and response snapshot for open/in-flight rows.
    for (const [key, entry] of rows) {
      if (!active.has(key) && entry.button.getAttribute('aria-expanded') !== 'true' && !entry.button.disabled) {
        entry.node.remove();
        rows.delete(key);
      }
    }
    const ordered = [...active, ...[...rows.keys()].filter(key => !active.has(key))];
    const nodes = ordered.map(key => rows.get(key).node);
    // insertBefore avoids recreating nodes and preserves disclosure state.
    for (let index = 0; index < nodes.length; index++) {
      if (box.children[index] !== nodes[index]) box.insertBefore(nodes[index], box.children[index] || null);
    }
    while (box.children.length > nodes.length) box.lastElementChild.remove();
    if (!nodes.length) box.append(text('p', '최근 활동이 없습니다.', 'mm-entry'));
  }
  async function poll() {
    try {
      if (document.hidden) return;
      const response = await fetch('/portal/mindmate/live/data', {
        cache: 'no-store', signal: AbortSignal.timeout(15000),
      });
      if (!response.ok || response.redirected) throw new Error('Poll denied');
      const result = await response.json();
      if (!result.ok) throw new Error('Poll failed');
      render(result.events);
      document.getElementById('statusLine').textContent =
        `최근 ${result.events.length}건 · 마지막 갱신 ${new Date().toLocaleTimeString('ko-KR')} · 펼친 항목 유지`;
    } catch (_) {
      document.getElementById('statusLine').textContent = '갱신 실패 — 기존 목록을 유지합니다. 로그인 상태를 확인해주세요.';
    } finally {
      setTimeout(poll, 10000);
    }
  }
  poll();
})();
