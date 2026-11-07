const zone = document.querySelector('[data-zone]')?.dataset.zone;
const dateFormat = new Intl.DateTimeFormat('fa-IR-u-ca-persian', { dateStyle: 'medium', timeStyle: 'short', timeZone: zone });
for (const time of document.querySelectorAll('time[datetime]')) {
  time.textContent = dateFormat.format(new Date(time.dateTime));
}
const numberFormat = new Intl.NumberFormat('fa-IR');
for (const element of document.querySelectorAll('[data-number]')) {
  element.textContent = numberFormat.format(Number(element.dataset.number));
}

const storage = {
  get(key) { try { return sessionStorage.getItem(key); } catch { return null; } },
  set(key, value) { try { sessionStorage.setItem(key, value); } catch { /* storage unavailable */ } },
  remove(key) { try { sessionStorage.removeItem(key); } catch { /* storage unavailable */ } },
};

const thread = document.querySelector('[data-thread]');
const scrollToEnd = () => { if (thread) thread.scrollTop = thread.scrollHeight; };
scrollToEnd();

const formatDates = (root) => {
  for (const time of root.querySelectorAll('time[datetime]')) time.textContent = dateFormat.format(new Date(time.dateTime));
  for (const element of root.querySelectorAll('[data-number]')) element.textContent = numberFormat.format(Number(element.dataset.number));
};

const bindCopy = (button) => {
  if (!navigator.clipboard) return;
  button.hidden = false;
  const label = button.querySelector('span');
  button.addEventListener('click', async () => {
    const answer = button.closest('.reply-body').querySelector('[data-answer]');
    try {
      await navigator.clipboard.writeText(answer.innerText);
      label.textContent = 'کپی شد';
    } catch {
      label.textContent = 'کپی ممکن نشد';
    }
    setTimeout(() => { label.textContent = 'کپی'; }, 2000);
  });
};
document.querySelectorAll('[data-copy]').forEach(bindCopy);

const flashes = document.querySelector('[data-flashes]');
const flash = (text, kind = 'error') => {
  const item = document.createElement('p');
  item.className = `flash ${kind}`;
  item.setAttribute('role', kind === 'error' ? 'alert' : 'status');
  item.textContent = text;
  flashes.replaceChildren(item);
};

async function* readEvents(response) {
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    buffer += value;
    let boundary;
    while ((boundary = buffer.indexOf('\n\n')) >= 0) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      let name = 'message';
      let data = '';
      for (const line of block.split('\n')) {
        if (line.startsWith('event:')) name = line.slice(6).trim();
        else if (line.startsWith('data:')) data += line.slice(5).trim();
      }
      if (data) yield [name, JSON.parse(data)];
    }
  }
}

const composer = document.querySelector('[data-composer]');
if (composer) {
  const field = composer.querySelector('textarea');
  const send = composer.querySelector('[data-send]');
  const stop = composer.querySelector('[data-stop]');
  const draftKey = `nexa-draft:${location.pathname}`;
  const canStream = 'TextDecoderStream' in window && 'AbortController' in window;
  let controller = null;
  const fit = () => {
    field.style.height = 'auto';
    field.style.height = `${Math.min(field.scrollHeight, 224)}px`;
  };
  if (document.querySelector('.flash.error') && !field.value) field.value = storage.get(draftKey) || '';
  storage.remove(draftKey);
  fit();
  field.addEventListener('input', fit);
  field.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      composer.requestSubmit(send);
    }
  });

  const setBusy = (busy) => {
    send.hidden = busy;
    stop.hidden = !busy;
    field.readOnly = busy;
    if (!busy) field.focus({ preventScroll: true });
  };

  const addTurn = (text) => {
    document.querySelector('.welcome')?.remove();
    const template = document.querySelector('[data-turn-template]');
    const turn = template.content.firstElementChild.cloneNode(true);
    turn.querySelector('[data-turn-question]').textContent = text;
    template.before(turn);
    scrollToEnd();
    return turn;
  };

  const enterConversation = (meta) => {
    if (composer.dataset.conversation === String(meta.conversation)) return;
    composer.dataset.conversation = meta.conversation;
    history.replaceState(null, '', meta.url);
    document.querySelector('.stage-head h1').textContent = meta.title;
    document.title = `${meta.title} | نکسا`;
    const list = document.querySelector('.history');
    list.querySelector('.history-empty')?.remove();
    let today = list.querySelector('[data-history-group]');
    if (!today || today.querySelector('h2').textContent !== 'امروز') {
      today = document.createElement('section');
      today.dataset.historyGroup = '';
      today.innerHTML = '<h2>امروز</h2><ul></ul>';
      list.prepend(today);
    }
    list.querySelector('[aria-current]')?.removeAttribute('aria-current');
    const item = document.createElement('li');
    const link = document.createElement('a');
    link.href = meta.url;
    link.dir = 'auto';
    link.textContent = meta.title;
    link.setAttribute('aria-current', 'page');
    item.append(link);
    today.querySelector('ul').prepend(item);
  };

  const streamTurn = async (text) => {
    const body = new FormData(composer);
    body.set('conversation', composer.dataset.conversation || '');
    controller = new AbortController();
    const response = await fetch(composer.dataset.streamUrl, {
      method: 'POST', body, signal: controller.signal, headers: { Accept: 'text/event-stream' },
    });
    const type = response.headers.get('Content-Type') || '';
    if (type.startsWith('application/json')) {
      const { error } = await response.json();
      flash(error || 'ارسال پرسش ممکن نشد.');
      return false;
    }
    if (!response.ok || !type.startsWith('text/event-stream')) throw new Error('fallback');
    field.value = '';
    fit();
    const turn = addTurn(text);
    const reply = turn.querySelector('[data-turn-reply]');
    const wait = reply.querySelector('.reply-wait');
    const answer = reply.querySelector('[data-answer]');
    const tools = reply.querySelector('[data-turn-tools]');
    const note = reply.querySelector('[data-turn-note]');
    const showFailure = (message) => {
      wait.hidden = true;
      reply.classList.remove('is-pending');
      const error = reply.querySelector('[data-turn-error]');
      error.textContent = message;
      error.hidden = false;
    };
    try {
      for await (const [name, data] of readEvents(response)) {
        if (name === 'meta') enterConversation(data);
        else if (name === 'delta') {
          if (answer.hidden) {
            wait.hidden = true;
            reply.classList.remove('is-pending');
            answer.hidden = false;
          }
          const nearEnd = thread.scrollHeight - thread.scrollTop - thread.clientHeight < 120;
          answer.append(data.text);
          if (nearEnd) scrollToEnd();
        } else if (name === 'done') {
          answer.classList.remove('is-streaming');
          answer.innerHTML = data.html;
          const sources = reply.querySelector('[data-turn-sources]');
          const chip = document.querySelector('[data-source-template]');
          for (const title of data.sources) {
            const item = chip.content.firstElementChild.cloneNode(true);
            item.querySelector('span').textContent = title;
            sources.append(item);
          }
          sources.hidden = !data.sources.length;
          tools.hidden = false;
          bindCopy(tools.querySelector('[data-copy]'));
          const quota = document.querySelector('[data-quota]');
          if (quota && data.quota) {
            quota.outerHTML = data.quota;
            formatDates(document.querySelector('[data-quota]'));
          }
        } else if (name === 'error') {
          answer.classList.remove('is-streaming');
          showFailure(`پاسخ داده نشد. ${data.message}`);
        }
      }
    } catch (error) {
      answer.classList.remove('is-streaming');
      if (error.name !== 'AbortError') showFailure('ارتباط با سرور قطع شد. برای دیدن نتیجه صفحه را تازه کنید.');
      else if (answer.hidden) showFailure('پاسخ با توقف شما لغو شد.');
      else {
        tools.hidden = false;
        note.textContent = 'پاسخ با توقف شما ناتمام ماند.';
      }
    }
    scrollToEnd();
    return true;
  };

  composer.addEventListener('submit', async (event) => {
    const text = field.value.trim();
    if (!text || send.hidden) {
      event.preventDefault();
      return;
    }
    storage.set(draftKey, field.value);
    if (!canStream) {
      setBusy(true);
      addTurn(text);
      return;
    }
    event.preventDefault();
    flashes.replaceChildren();
    setBusy(true);
    try {
      await streamTurn(text);
      storage.remove(draftKey);
    } catch (error) {
      if (error.name !== 'AbortError') {
        HTMLFormElement.prototype.submit.call(composer);
        return;
      }
    } finally {
      controller = null;
    }
    setBusy(false);
  });
  stop.addEventListener('click', () => controller?.abort());
  window.addEventListener('pageshow', (event) => {
    if (event.persisted) setBusy(false);
  });
  field.focus({ preventScroll: true });
}

const uploadForm = document.querySelector('[data-upload-form]');
if (uploadForm) {
  const input = uploadForm.querySelector('[data-upload-input]');
  document.querySelector('[data-upload-trigger]').addEventListener('click', () => input.click());
  input.addEventListener('change', () => {
    const file = input.files[0];
    if (!file) return;
    if (file.size > Number(input.dataset.limit)) {
      input.value = '';
      alert('حجم فایل بیش از ۲۵۶ کیلوبایت است.');
      return;
    }
    uploadForm.submit();
  });
}

const rail = document.getElementById('rail');
const scrim = document.querySelector('.rail-scrim');
for (const toggle of document.querySelectorAll('[data-rail-toggle]')) {
  toggle.addEventListener('click', () => {
    const open = rail.classList.toggle('is-open');
    scrim.hidden = !open;
    document.querySelector('.rail-open')?.setAttribute('aria-expanded', String(open));
  });
}

const filter = document.querySelector('[data-history-filter]');
if (filter) {
  filter.addEventListener('input', () => {
    const query = filter.value.trim().toLowerCase();
    for (const group of document.querySelectorAll('[data-history-group]')) {
      let visible = 0;
      for (const item of group.querySelectorAll('li')) {
        const match = !query || item.textContent.toLowerCase().includes(query);
        item.hidden = !match;
        visible += match;
      }
      group.hidden = !visible;
    }
  });
}

for (const form of document.querySelectorAll('form[data-confirm]')) {
  form.addEventListener('submit', (event) => {
    if (!confirm(form.dataset.confirm)) event.preventDefault();
  });
}
