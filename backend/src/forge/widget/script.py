# ruff: noqa: E501 - the script is JavaScript in a string
"""The widget's browser script, served at /api/v1/widget/widget.js (E3.5). Plain JavaScript, no
dependencies, in a shadow root so the host page's styles cannot reach it and it cannot reach
them. Text is always inserted as text, never as HTML."""

WIDGET_JS = r"""(() => {
  'use strict';
  const me = document.currentScript;
  if (!me || !me.dataset.key) { console.error('Forge widget: data-key is missing'); return; }
  const key = me.dataset.key;
  const base = new URL(me.src).origin + '/api/v1/widget/' + encodeURIComponent(key);
  const store = 'forge-widget-' + key;
  let session = null;
  try { session = window.localStorage.getItem(store); } catch (e) { /* storage blocked */ }
  let busy = false;

  const host = document.createElement('div');
  host.style.cssText = 'position:fixed;right:20px;bottom:20px;z-index:2147483000;';
  const root = host.attachShadow({ mode: 'closed' });
  root.innerHTML = `
  <style>
    :host { all: initial; }
    * { box-sizing: border-box; font-family: system-ui, -apple-system, Segoe UI, sans-serif; }
    .fab { width: 56px; height: 56px; border-radius: 50%; border: 0; cursor: pointer;
      background: #1a56db; color: #fff; font-size: 26px; box-shadow: 0 4px 14px rgba(0,0,0,.3); }
    .panel { display: none; width: min(380px, calc(100vw - 24px)); height: min(560px, 80vh);
      flex-direction: column; background: #fff; color: #1f2937; border-radius: 12px;
      box-shadow: 0 8px 30px rgba(0,0,0,.3); overflow: hidden; }
    .open .panel { display: flex; } .open .fab { display: none; }
    header { background: #1a56db; color: #fff; padding: 12px 14px; display: flex;
      align-items: center; justify-content: space-between; font-weight: 600; }
    header button { background: none; border: 0; color: #fff; font-size: 20px; cursor: pointer; }
    .log { flex: 1; overflow-y: auto; padding: 12px; display: flex; flex-direction: column; gap: 8px; }
    .msg { max-width: 85%; padding: 8px 11px; border-radius: 10px; white-space: pre-wrap;
      word-wrap: break-word; font-size: 14px; line-height: 1.4; }
    .me { align-self: flex-end; background: #dbeafe; }
    .bot { align-self: flex-start; background: #f3f4f6; }
    .err { align-self: center; color: #b91c1c; font-size: 13px; }
    form { display: flex; gap: 6px; padding: 10px; border-top: 1px solid #e5e7eb; }
    input { flex: 1; padding: 9px 10px; border: 1px solid #d1d5db; border-radius: 8px; font-size: 14px; }
    form button { padding: 0 14px; border: 0; border-radius: 8px; background: #1a56db; color: #fff; cursor: pointer; }
    form button:disabled { opacity: .5; cursor: default; }
    @media (prefers-color-scheme: dark) {
      .panel { background: #111827; color: #e5e7eb; } .bot { background: #1f2937; }
      .me { background: #1e3a8a; } input { background: #1f2937; color: #e5e7eb; border-color: #374151; }
      form { border-color: #374151; }
    }
  </style>
  <div id="w">
    <button class="fab" aria-label="Open chat">&#128172;</button>
    <section class="panel" role="dialog" aria-label="Chat">
      <header><span id="title">Assistant</span><button id="close" aria-label="Close">&times;</button></header>
      <div class="log" id="log" aria-live="polite"></div>
      <form id="form"><input id="text" maxlength="2000" placeholder="Type a message" autocomplete="off">
        <button id="send" type="submit">Send</button></form>
    </section>
  </div>`;
  const $ = (id) => root.getElementById(id);
  const log = $('log'), text = $('text'), send = $('send'), wrap = $('w');

  const add = (cls, content) => {
    const d = document.createElement('div');
    d.className = 'msg ' + cls; d.textContent = content;
    log.appendChild(d); log.scrollTop = log.scrollHeight; return d;
  };
  const call = async (path, body) => {
    const r = await fetch(base + path, body === undefined ? {} : {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) { const e = new Error(data.title || 'Request failed'); e.status = r.status; e.code = data.code; throw e; }
    return data;
  };
  const ensureSession = async () => {
    if (session) return;
    session = (await call('/session', {})).session;
    try { window.localStorage.setItem(store, session); } catch (e) { /* ignore */ }
  };
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  async function ask(content) {
    busy = true; send.disabled = true;
    add('me', content);
    const pending = add('bot', '…');
    try {
      await ensureSession();
      let sent;
      try { sent = await call('/messages', { session, content }); }
      catch (e) {
        if (e.code !== 'session_expired') throw e;
        session = null; await ensureSession(); sent = await call('/messages', { session, content });
      }
      for (let i = 0; i < 240; i++) {
        await sleep(i < 4 ? 600 : 1200);
        const a = await call('/runs/' + sent.run_id + '?session=' + encodeURIComponent(session));
        if (a.status === 'done') { pending.textContent = a.output || ''; return; }
        if (a.status === 'failed') { pending.className = 'msg err'; pending.textContent = a.error; return; }
      }
      pending.className = 'msg err'; pending.textContent = 'This is taking too long. Please try again.';
    } catch (e) {
      pending.className = 'msg err'; pending.textContent = e.message || 'Something went wrong.';
    } finally { busy = false; send.disabled = false; text.focus(); }
  }

  wrap.querySelector('.fab').onclick = () => { wrap.classList.add('open'); text.focus(); };
  $('close').onclick = () => wrap.classList.remove('open');
  $('form').onsubmit = (ev) => {
    ev.preventDefault();
    const v = text.value.trim();
    if (!v || busy) return;
    text.value = ''; ask(v);
  };
  document.body.appendChild(host);
  call('/config').then((c) => {
    if (c.title) $('title').textContent = c.title;
    if (c.welcome) add('bot', c.welcome);
  }).catch(() => { host.remove(); });
})();
"""
