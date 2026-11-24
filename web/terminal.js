// Термінал картки й диктування. Сторінка лише дивиться на tmux: закриття панелі розмову не зупиняє.
window.Term = (() => {
  const $ = id => document.getElementById(id);
  let term, fit, card = null, key = null, es = null, opening = false, retry = 0;
  let ended = false;   // розмова завершилась або її зупинили: не під'єднуємось знову самі
  let ask = null;      // підтвердження: {kind: "take"|"stop", text, risky}
  let emptyHtml = null, sendChain = Promise.resolve(), pending = "", resizeTimer = 0;
  const drafts = {};
  const MAC = /Mac/i.test(navigator.platform);  // Ctrl+V на Mac потрібен самому Claude (картинки), а вставлення — Cmd+V

  function build() {
    if (term) return;
    term = new Terminal({cursorBlink: true, scrollback: 5000, allowProposedApi: false, macOptionIsMeta: false,
      fontFamily: '"JetBrains Mono", "DejaVu Sans Mono", monospace', fontSize: fontSize(),
      theme: Themes.term(), minimumContrastRatio: Themes.termContrast()});
    fit = new FitAddon.FitAddon();
    term.loadAddon(fit);
    term.loadAddon(new WebLinksAddon.WebLinksAddon((e, url) => openLink(url)));
    term.open($("term-screen"));
    term.onData(send);
    term.onSelectionChange(() => {});
    term.attachCustomKeyEventHandler(e => {
      if (e.type !== "keydown") return true;
      if (e.ctrlKey && e.code === "Space" && S.dictation) return false;        // мікрофон
      if (!MAC && e.ctrlKey && !e.altKey && e.code === "KeyV") return false;           // вставлення браузера
      if (e.ctrlKey && !e.altKey && e.code === "KeyC" && term.hasSelection()) { navigator.clipboard.writeText(term.getSelection()); term.clearSelection(); e.preventDefault(); return false; }
      return true;
    });
    // Картинку Claude бере з буфера сам, за Ctrl+V.
    $("term-screen").addEventListener("paste", e => {
      const items = [...(e.clipboardData?.items || [])];
      if (!items.some(i => i.type === "text/plain") && items.some(i => i.type.startsWith("image/"))) { e.preventDefault(); e.stopPropagation(); send("\x16"); }
    }, true);
    // Копіювання з Claude (OSC 52): лише запис у буфер.
    term.parser.registerOscHandler(52, data => {
      const b64 = data.slice(data.indexOf(";") + 1);
      if (b64 && b64 !== "?") try { navigator.clipboard.writeText(new TextDecoder().decode(bytes(b64))).catch(() => {}); } catch (e) {}
      return true;
    });
    new ResizeObserver(() => { clearTimeout(resizeTimer); resizeTimer = setTimeout(doFit, 80); }).observe($("term-screen"));
  }
  const bytes = b64 => Uint8Array.from(atob(b64), c => c.charCodeAt(0));
  const fontSize = () => Math.round(14 * font.term / 100);
  function doFit() {
    if (!term || !$("term-screen").offsetWidth) return;
    const d = fit.proposeDimensions();
    if (!d || !(d.cols >= 2) || !(d.rows >= 2)) return;
    if (d.cols === term.cols && d.rows === term.rows) return;
    fit.fit();
    if (key) post("/api/term/resize", {key, cols: term.cols, rows: term.rows});
  }
  // Клавіші йдуть строго по черзі; набране, поки попередній запит у дорозі, їде одним пакетом.
  function send(data) {
    if (!key) return;
    pending += data;
    const to = key;
    sendChain = sendChain.then(async () => {
      if (!pending || key !== to) return;
      const chunk = pending.slice(0, 30000); pending = pending.slice(30000);
      try { await post("/api/term/input", {key: to, data: chunk}); } catch (e) {}
      if (pending) send("");
    });
  }
  function detach() {
    if (es) es.close();
    if (key) post("/api/term/detach", {key}).catch(() => {});
    es = null; key = null; pending = "";
  }
  async function attach(start) {
    if (opening || key || !card) return;
    const id = card.id;
    opening = true; ended = false;
    build(); draw(); doFit();
    term.reset();
    let r;
    try { r = await post("/api/term/open", {id, cols: term.cols, rows: term.rows, start}); }
    catch (e) { r = {error: "дошка не відповідає", net: true}; }
    opening = false;
    if (card?.id !== id) {
      if (r.key) post("/api/term/detach", {key: r.key}).catch(() => {});
      return show(card);
    }
    if (!r.key) {
      if (r.net) return again();
      ended = true;
      if (!r.closed && !r.live) toast(r.error || "Не вдалося відкрити термінал");
      return draw();
    }
    key = r.key;
    const mine = es = new EventSource("/api/term/stream?key=" + encodeURIComponent(key));
    es.onmessage = e => { retry = 0; term.write(bytes(e.data)); };
    es.addEventListener("end", () => { if (es !== mine) return; es.close(); es = null; key = null; ended = true; draw(); });
    es.onerror = () => { if (es !== mine) return; es.close(); es = null; key = null; again(); };
    draw(); doFit(); term.focus();
  }
  function again() {
    const id = card?.id;
    retry = Math.min(retry + 1, 6);
    setTimeout(() => { if (card?.id === id && !key && !ended) attach(false); }, retry * 700);
    draw();
  }

  // ---------- вигляд ----------
  function show(c) {
    if (c?.id !== card?.id) {
      if (card) drafts[card.id] = $("term-text").value;
      detach();
      ask = null; ended = false; retry = 0;
      $("term-text").value = c ? drafts[c.id] || "" : "";
    }
    card = c;
    if (!c) return;
    if (c.term && !key && !opening && !ended) attach(false);
    if (!c.term && !key) ended = false;   // дошка вже знає, що розмова не тут
    draw(); grow();
  }
  function draw() {
    const on = !!key || opening;
    $("term").classList.toggle("on", on);
    if (ask?.kind === "stop" && !on) ask = null;
    const html = on && !ask ? "" : emptyView();
    if (html !== emptyHtml) { $("term-empty").innerHTML = html; emptyHtml = html; }
  }
  const btn = (t, label, primary) => `<button data-t="${t}" class="${primary ? "primary" : ""}">${label}</button>`;
  function emptyView() {
    const c = card;
    if (!c) return "";
    if (ask) return `<p>${esc(ask.text)}</p><div>${btn("yes", ask.kind === "stop" ? "Так, завершити" : "Так, забрати", true)} ${btn("no", "Скасувати")}</div>`;
    const error = c.take_error ? `<p class="warn">⚠️ ${esc(c.take_error)}</p>` : "";
    if (c.live_status && !c.term) {
      if (c.live_kind === "bg") return `<p>Це фонова розмова (claude --bg). Забрати її в дошку не можна.</p>`;
      if (c.armed) return `<p>⏳ Заберу розмову в дошку, щойно агент закінчить роботу.</p><div>${btn("cancel", "Не забирати")}</div>`;
      const busy = c.live_status === "busy";
      return `${error}<p>Розмова відкрита в іншому терміналі${busy ? ", агент зараз працює" : ""}.</p>
        <div>${btn("take", busy ? "Забрати, коли закінчить" : "⇣ Забрати в дошку", true)}</div>`;
    }
    if (c.fresh) return `<p>${ended ? "Чат закрито до першого повідомлення." : "Запускаю Claude…"}</p>`;
    if (c.term) return `<p>Розмова працює в дошці.</p><div>${btn("attach", "Під'єднатися", true)}</div>`;
    return `${error}<p>${ended ? "Розмову завершено." : "Розмову закрито."}</p><div>${btn("start", "▶ Продовжити тут", true)}</div>`;
  }
  async function take(confirm) {
    const id = card.id, r = await post("/api/term/takeover", {id, confirm});
    if (card?.id !== id) return;
    ask = null;
    if (r.confirm) ask = {kind: "take", risky: true, text: "⚠️ " + r.error + ". Забрати все одно?"};
    else if (r.error) toast(r.error);
    else if (r.result === "done") { ended = false; return attach(false); }
    draw();
  }
  async function click(t) {
    if (!card) return;
    if (t === "start") return attach(true);
    if (t === "attach") return attach(false);
    if (t === "cancel") { await post("/api/term/takeover", {id: card.id, cancel: true}); return; }
    if (t === "take") {
      if (card.live_status === "busy") return take(false);
      ask = {kind: "take", risky: false, text: "Claude у тому вікні завершиться, а розмова продовжиться тут. Ненадісланий текст там зникне."};
      return draw();
    }
    if (t === "stop") { ask = {kind: "stop", text: "Завершити Claude в цій картці? Розмова збережеться, її можна буде продовжити."}; return draw(); }
    if (t === "no") { ask = null; draw(); if (key) term.focus(); return; }
    if (t === "yes") {
      const a = ask;
      if (a.kind === "take") return take(a.risky);
      ask = null; ended = true;
      const r = await post("/api/term/stop", {id: card.id});
      if (r.error) toast(r.error);
      return draw();
    }
    if (t === "send") return sendText();
    if (t === "mic") return mic();
    if (t === "full") { document.body.classList.toggle("term-full"); return; }
  }
  document.addEventListener("click", e => { const b = e.target.closest("[data-t]"); if (b) click(b.dataset.t); });

  // ---------- поле введення ----------
  function grow() { const t = $("term-text"); t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight + 2, 150) + "px"; }
  async function sendText() {
    const t = $("term-text"), text = t.value;
    if (!text.trim()) return;
    if (!key) return toast("Спершу відкрийте термінал");
    const id = card.id;
    t.disabled = true;
    let r;
    try { r = await post("/api/term/paste", {key, text}); } catch (e) { r = {error: "дошка не відповідає"}; }
    t.disabled = false;
    if (r.error) { toast(r.error); return t.focus(); }
    if (card?.id === id && t.value === text) { t.value = ""; grow(); t.focus(); }
    drafts[id] = "";
  }
  $("term-text").addEventListener("input", grow);
  $("term-text").addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); sendText(); }
  });

  // ---------- диктування ----------
  let rec = null, recBusy = false;
  const MAX_SECONDS = 120, RATE = 16000;
  const lang = $("term-lang");
  try { lang.value = localStorage.getItem("agent-board-lang") || "auto"; } catch (e) {}
  if (!lang.value) lang.value = "auto";
  lang.addEventListener("change", () => { try { localStorage.setItem("agent-board-lang", lang.value); } catch (e) {} });
  function micLabel(text, cls) { const b = $("term-mic"); b.textContent = text; b.className = cls || ""; b.disabled = cls === "wait"; }
  async function mic() {
    if (recBusy) return;
    if (rec) return finish();
    recBusy = true;
    let stream, ctx;
    try {
      stream = await navigator.mediaDevices.getUserMedia({audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true}});
      ctx = new AudioContext({sampleRate: RATE});
      const url = URL.createObjectURL(new Blob(['registerProcessor("pcm",class extends AudioWorkletProcessor{process(i){if(i[0][0])this.port.postMessage(i[0][0].slice());return true}})'], {type: "text/javascript"}));
      await ctx.audioWorklet.addModule(url);
      URL.revokeObjectURL(url);
      const src = ctx.createMediaStreamSource(stream), node = new AudioWorkletNode(ctx, "pcm", {numberOfOutputs: 0});
      rec = {ctx, stream, chunks: [], n: 0, id: card?.id, timer: 0};
      node.port.onmessage = e => { if (rec && rec.n < MAX_SECONDS * RATE) { rec.chunks.push(e.data); rec.n += e.data.length; } };
      src.connect(node);
      rec.timer = setInterval(() => { const s = Math.floor(rec.n / RATE); micLabel("⏺ " + s + " с", "rec"); if (s >= MAX_SECONDS) finish(); }, 250);
      micLabel("⏺ 0 с", "rec");
    } catch (e) {
      stream?.getTracks().forEach(t => t.stop()); ctx?.close(); rec = null;
      toast(e.name === "NotAllowedError" ? "Мікрофон заборонено: натисніть значок ліворуч від адреси або в меню вікна й дозвольте мікрофон"
        : e.name === "NotFoundError" ? "Мікрофон не знайдено" : "Мікрофон: " + e.message);
    }
    recBusy = false;
  }
  async function finish() {
    const r = rec; rec = null; recBusy = true;
    clearInterval(r.timer); r.stream.getTracks().forEach(t => t.stop()); r.ctx.close();
    const pcm = new Int16Array(Math.min(r.n, MAX_SECONDS * RATE));
    let at = 0;
    for (const c of r.chunks) for (let i = 0; i < c.length && at < pcm.length; i++) pcm[at++] = Math.max(-1, Math.min(1, c[i])) * 32767;
    try {
      if (at < RATE * 0.3) toast("Запис надто короткий");
      else {
        micLabel("… розпізнаю", "wait");
        const res = await fetch("/api/dictate?lang=" + lang.value, {method: "POST", headers: {"Content-Type": "application/octet-stream"}, body: pcm});
        const d = await res.json();
        if (!res.ok) toast(d.error || "Не розпізнано");
        else if (!d.text) toast("Мови не чути");
        else insert(r.id, d.text);
      }
    } catch (e) { toast("Диктування: " + e.message); }
    recBusy = false; micLabel("🎤 Диктувати");
  }
  function insert(id, text) {
    if (card?.id !== id) { drafts[id] = ((drafts[id] || "").trimEnd() + " " + text).trim(); return toast("Текст — у полі тієї картки, де ви диктували"); }
    const t = $("term-text"), a = t.value.slice(0, t.selectionStart), b = t.value.slice(t.selectionEnd);
    const left = a && !/\s$/.test(a) ? a + " " : a;
    t.value = left + text + (b && !/^\s/.test(b) ? " " : "") + b;
    t.selectionStart = t.selectionEnd = left.length + text.length;
    grow(); t.focus();
  }
  document.addEventListener("keydown", e => {
    if (S.dictation && e.ctrlKey && e.code === "Space" && document.body.classList.contains("tab-term")) { e.preventDefault(); mic(); }
  });
  document.addEventListener("themechange", () => { if (term) { term.options.theme = Themes.term(); term.options.minimumContrastRatio = Themes.termContrast(); } });
  if (MAC) $("term-mic").title = "Ctrl+Shift+Пробіл — почати й закінчити";  // Ctrl+Пробіл на Mac перемикає мову вводу
  document.addEventListener("fontchange", () => { grow(); if (term) { term.options.fontSize = fontSize(); doFit(); } });
  window.addEventListener("pagehide", detach);
  // Чи можна оновити сторінку, нічого не загубивши: немає запису й ненадісланого тексту.
  const idle = () => !rec && !recBusy && !opening && !ask && !$("term-text").value && !Object.values(drafts).some(Boolean);
  return {show, idle};
})();
render();
