const roles = {
  strategist: 'Стратег',
  copywriter: 'Копирайтер',
  editor: 'Редактор',
  publisher: 'Паблишер',
};
const byId = (id) => document.getElementById(id);
const form = byId('task-form');
const board = byId('board');
const emptyOutputs = Object.fromEntries(
  Object.keys(roles).map((role) => [role, byId(`${role}-output`).innerHTML]),
);
const timing = new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 3});
const clock = new Intl.DateTimeFormat('ru-RU', {timeStyle: 'medium'});
let eventCount = 0;
let reviewCount = 0;
let revisionLimit = 0;
let modelCalls = [];

function formatDuration(milliseconds) {
  if (milliseconds == null) return '—';
  if (milliseconds >= 1000) return `${timing.format(milliseconds / 1000)} с`;
  return `${timing.format(milliseconds)} мс`;
}

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function focusPanel(role) {
  const next = board.dataset.focus === role ? undefined : role;
  if (next) board.dataset.focus = next;
  else delete board.dataset.focus;
  board.querySelectorAll('.panel').forEach((panel) => {
    const focused = panel.dataset.role === next;
    panel.classList.toggle('focused', focused);
    const button = panel.querySelector('.expand');
    button.setAttribute('aria-expanded', String(focused));
    button.setAttribute('aria-label', `${focused ? 'Свернуть' : 'Развернуть'} колонку ${panel.querySelector('h2').textContent}`);
    button.textContent = focused ? '⤡' : '⤢';
  });
  if (next && matchMedia('(max-width: 740px)').matches) {
    board.querySelector('.focused').scrollIntoView({block: 'nearest'});
  }
}

document.querySelectorAll('[data-panel]').forEach((button) => {
  button.addEventListener('click', () => focusPanel(button.dataset.panel));
});
byId('show-graph').addEventListener('click', () => byId('graph-dialog').showModal());
byId('show-input').addEventListener('click', () => byId('input-dialog').showModal());

function showPhase(role, phase) {
  const button = byId('steps').querySelector(`[data-panel="${role}"]`);
  button.dataset.phase = phase;
  if (phase === 'active') button.setAttribute('aria-current', 'step');
  else button.removeAttribute('aria-current');
  board.querySelector(`[data-role="${role}"]`).dataset.active = String(phase === 'active');
}

function addLog(role, description, duration, call) {
  byId('empty-log')?.remove();
  const row = element('tr');
  const roleCell = element('td');
  if (role) {
    const link = element('button', roles[role], 'text-button');
    link.type = 'button';
    link.addEventListener('click', () => {
      if (board.dataset.focus !== role) focusPanel(role);
    });
    roleCell.append(link);
  } else {
    roleCell.textContent = 'Граф';
  }
  const detail = element('td', description);
  if (call?.generation_ms != null) {
    detail.append(element('small', `Загрузка ${formatDuration(call.load_ms)} · Промпт ${formatDuration(call.prompt_ms)} · Генерация ${formatDuration(call.generation_ms)}`, 'call-timing'));
  }
  const tokens = call ? `${call.input_tokens ?? '—'} / ${call.output_tokens ?? '—'}` : '—';
  row.append(element('td', clock.format(new Date())), roleCell, detail,
    element('td', formatDuration(duration)), element('td', tokens));
  byId('events').append(row);
  byId('event-count').textContent = `События · ${++eventCount}`;
}

function addCalls(role, calls) {
  modelCalls.push(...calls);
  for (const call of calls) {
    addLog(role, call.error ? `${call.model} · ${call.error}` : `Вызов ${call.model}`, call.duration_ms, call);
  }
  function total(field) {
    const reported = modelCalls.filter((call) => Number.isInteger(call[field]));
    if (!reported.length) return '—';
    const tokens = reported.reduce((sum, call) => sum + call[field], 0);
    return `${reported.length < modelCalls.length ? '≥ ' : ''}${timing.format(tokens)}`;
  }
  byId('call-count').textContent = modelCalls.length;
  byId('tokens').textContent = `${total('input_tokens')} / ${total('output_tokens')}`;
}

function item(title, text, subtitle) {
  const card = element('article', undefined, 'output-item');
  if (subtitle) card.append(element('small', subtitle));
  card.append(element('strong', title));
  if (text) card.append(element('p', text));
  return card;
}

function renderVersion(container, version, posts) {
  if (version === 1) container.replaceChildren();
  container.querySelectorAll('details').forEach((details) => { details.open = false; });
  const group = element('details', undefined, 'output-group');
  group.open = true;
  group.append(element('summary', `Версия ${version}`));
  for (const post of posts) group.append(item(post.title, post.text, `Пост ${post.post_id}`));
  container.prepend(group);
}

function renderPublisher(container, posts) {
  const counters = element('div', undefined, 'publisher-count');
  const published = element('b', '0');
  counters.append(published, element('span', `Опубликовано · Подготовлено ${posts.length}`));
  container.replaceChildren(counters);
  const marked = new Set();
  for (const post of posts) {
    const card = item(post.title, post.text, `Пост ${post.post_id}`);
    const toggle = element('button', 'Отметить опубликованным', 'button');
    toggle.type = 'button';
    toggle.setAttribute('aria-pressed', 'false');
    toggle.addEventListener('click', () => {
      const next = !marked.has(post.post_id);
      if (next) marked.add(post.post_id);
      else marked.delete(post.post_id);
      toggle.setAttribute('aria-pressed', String(next));
      toggle.textContent = next ? 'Снять отметку публикации' : 'Отметить опубликованным';
      published.textContent = marked.size;
    });
    card.append(toggle);
    container.append(card);
  }
}

function renderStep(role, output) {
  const container = byId(`${role}-output`);
  let description;
  let next;
  showPhase(role, 'done');
  switch (role) {
    case 'strategist':
      container.replaceChildren(...output.plan.map((entry) => item(
        entry.title, entry.selected ? entry.task : '',
        `День ${entry.post_id}${entry.selected ? ' · выбран для поста' : ''}`,
      )));
      description = `Контент-план · ${output.plan.length} дн.`;
      next = 'copywriter';
      break;
    case 'copywriter':
      renderVersion(container, output.version, output.drafts);
      description = `Черновики · версия ${output.version}`;
      byId('revisions').textContent = output.version - 1;
      next = 'editor';
      break;
    case 'editor': {
      if (reviewCount === 0) container.replaceChildren();
      const {approved, edits} = output.review;
      description = approved ? 'Принято' : 'Не принято';
      const card = item(description, undefined, `Проверка ${++reviewCount}`);
      card.querySelector('strong').className = approved ? 'decision-approved' : 'decision-rejected';
      for (const edit of edits) card.append(element('p', `Пост ${edit.post_id} · ${edit.instruction}`));
      container.prepend(card);
      if (approved) next = 'publisher';
      else {
        showPhase(role, 'review');
        const route = byId('revision-route');
        route.hidden = false;
        if (output.revision_unchanged) {
          route.textContent = 'Копирайтер повторил отклонённый текст. Требуется ручная доработка.';
        } else if (reviewCount <= revisionLimit) {
          next = 'copywriter';
          route.textContent = `Редактор → Копирайтер · возврат ${reviewCount} из ${revisionLimit}`;
        } else {
          route.textContent = 'Лимит возвратов исчерпан';
        }
      }
      break;
    }
    case 'publisher':
      renderPublisher(container, output.ready);
      description = `Подготовлено постов · ${output.ready.length}`;
      break;
  }
  addCalls(role, output.calls);
  addLog(role, description, output.duration_ms);
  if (next) showPhase(next, 'active');
}

function handleEvent(event) {
  if (event.type === 'start') {
    revisionLimit = event.input.max_revisions;
    const traceLink = byId('trace-link');
    if (event.trace_url) {
      traceLink.href = event.trace_url;
      traceLink.hidden = false;
    }
    byId('model-label').textContent = `Локальная модель · ${event.model}`;
    byId('input-preview').textContent = JSON.stringify(event.input, null, 2);
    byId('show-input').disabled = false;
    showPhase('task', 'done');
    showPhase('strategist', 'active');
    addLog(null, 'Входные данные приняты');
  } else if (event.type === 'step') {
    renderStep(event.role, event.output);
  } else if (event.type === 'error') {
    addCalls(event.role, event.calls);
    showPhase(event.role, 'review');
    byId('elapsed').textContent = formatDuration(event.elapsed_ms);
    showError(`${roles[event.role]}: ${event.message}`);
    addLog(event.role, 'Запуск прерван', event.elapsed_ms);
    return true;
  } else if (event.type === 'finish') {
    const status = event.status === 'completed' ? 'Завершено' : 'Требуется доработка';
    byId('run-status').textContent = status;
    byId('elapsed').textContent = formatDuration(event.elapsed_ms);
    byId('revisions').textContent = event.revisions;
    const message = event.status === 'no_progress' ? 'Остановка · текст после правок не изменился' : status;
    addLog(null, message, event.elapsed_ms);
    return true;
  }
  return false;
}

function payload() {
  const data = new FormData(form);
  return {
    topic: data.get('topic'), brief: data.get('brief'), days: Number(data.get('days')),
    post_count: Number(data.get('post_count')), max_revisions: Number(data.get('max_revisions')),
    roles: Object.fromEntries(Object.keys(roles).map((role) => [role, data.get(role)])),
  };
}

function showError(message) {
  byId('form-error').hidden = false;
  byId('form-error').textContent = message;
  byId('run-status').textContent = 'Ошибка запуска';
}

async function followRun(response) {
  if (!response.ok) {
    const error = await response.json();
    const detail = Array.isArray(error.detail) ? error.detail.map((issue) => issue.msg).join('. ') : error.detail;
    throw new Error(detail || 'Не удалось запустить граф.');
  }
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = '';
  let finished = false;
  for (;;) {
    const {done, value} = await reader.read();
    if (done) break;
    const lines = (buffer + value).split('\n');
    buffer = lines.pop();
    for (const line of lines) {
      if (line) finished = handleEvent(JSON.parse(line)) || finished;
    }
  }
  if (!finished) throw new Error('Соединение прервалось до завершения графа.');
}

form.addEventListener('invalid', (event) => {
  if (board.dataset.focus && board.dataset.focus !== 'task') focusPanel('task');
  const settings = event.target.closest('details');
  if (settings) settings.open = true;
}, true);

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const task = payload();
  if (task.post_count > task.days) {
    if (board.dataset.focus && board.dataset.focus !== 'task') focusPanel('task');
    showError('Число постов должно быть не больше числа дней.');
    return;
  }
  byId('form-error').hidden = true;
  byId('run').disabled = true;
  byId('show-input').disabled = true;
  eventCount = 0;
  reviewCount = 0;
  modelCalls = [];
  byId('events').replaceChildren();
  byId('event-count').textContent = '0 событий';
  byId('revision-route').hidden = true;
  byId('elapsed').textContent = '—';
  byId('revisions').textContent = '0';
  byId('call-count').textContent = '0';
  byId('tokens').textContent = '—';
  byId('trace-link').hidden = true;
  for (const role of Object.keys(roles)) {
    byId(`${role}-output`).innerHTML = emptyOutputs[role];
    showPhase(role, 'waiting');
  }
  showPhase('task', 'active');
  byId('run-status').textContent = 'Выполняется';
  try {
    await followRun(await fetch('/api/runs', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(task),
    }));
  } catch (error) {
    if (board.dataset.focus && board.dataset.focus !== 'task') focusPanel('task');
    showError(error.message);
    byId('steps').querySelectorAll('[aria-current]').forEach((step) => showPhase(step.dataset.panel, 'review'));
  } finally {
    byId('run').disabled = false;
  }
});

try {
  const defaults = JSON.parse(byId('default-task').textContent);
  for (const field of ['topic', 'brief', 'days', 'post_count', 'max_revisions']) {
    form.elements.namedItem(field).value = defaults[field];
  }
  for (const [role, name] of Object.entries(roles)) {
    const label = element('label', name);
    const input = element('textarea');
    input.name = role;
    input.value = defaults.roles[role];
    input.rows = 3;
    input.required = true;
    input.maxLength = 4000;
    label.append(input);
    byId('role-settings').append(label);
  }
  byId('run').disabled = false;
} catch {
  showError('Не удалось заполнить стартовое задание.');
}
