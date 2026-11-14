/* ============================================================
   YesMedia · 前端逻辑
   契约见 docs/CONTRACT.md §8；交互说明见 docs/HANDOFF.md §6
   无外部依赖，离线可用
   ============================================================ */
(function () {
  'use strict';

  /* ---------- 小工具 ---------- */
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var sleep = function (ms) { return new Promise(function (r) { setTimeout(r, ms); }); };

  var DEMO_URL = location.origin + '/demo';
  var QUEUE_GAP = 900;          // 批量下载间隔（防浏览器拦截）
  var SSE_IDLE = 8000;          // 8s 无事件 → 回退 POST
  var IS_ANDROID = /WebGrabAndroid/i.test(navigator.userAgent);
  var IS_NATIVE = /WebGrabNative/i.test(navigator.userAgent);
  var IS_APP = (IS_ANDROID && !IS_NATIVE) || /DSH|WebGrabApp/i.test(navigator.userAgent);

  var els = {
    status: $('#status'),
    urlInput: $('#urlInput'),
    pasteBtn: $('#pasteBtn'),
    goBtn: $('#goBtn'),
    btnSpin: $('#btnSpin'),
    btnText: $('#btnText'),
    demoBtn: $('#demoBtn'),
    tpToggle: $('#tpToggle'),
    helpBtn: $('#helpBtn'),
    help: $('#help'),
    helpClose: $('#helpClose'),
    installBtnTop: $('#installBtnTop'),

    convertCard: $('#convertCard'),
    filePick: $('#filePick'),
    videoFile: $('#videoFile'),
    fileLabel: $('#fileLabel'),
    fmtRow: $('#fmtRow'),
    convBtn: $('#convBtn'),
    convStatus: $('#convStatus'),
    convFill: $('#convFill'),
    convText: $('#convText'),
    convTime: $('#convTime'),

    wvTip: $('#wvTip'),
    copyUrl: $('#copyUrl'),

    progress: $('#progress'),
    pvText: $('#pvText'),
    pvTime: $('#pvTime'),
    pvFill: $('#pvFill'),
    steps: $$('.pv-step'),

    error: $('#error'),
    errText: $('#errText'),
    retryBtn: $('#retryBtn'),
    errDemoBtn: $('#errDemoBtn'),

    result: $('#result'),
    pageTitle: $('#pageTitle'),
    pageMeta: $('#pageMeta'),
    engineNote: $('#engineNote'),
    tabs: $('#tabs'),
    grid: $('#grid'),
    empty: $('#empty'),
    dlAll: $('#dlAll'),
    newLink: $('#newLink'),
    emptyDemoBtn: $('#emptyDemoBtn'),

    dyApiBtn: $('#dyApiBtn'),

    bar: $('#bar'),
    selAll: $('#selAll'),
    selCount: $('#selCount'),
    downloadBtn: $('#downloadBtn'),
    selNum: $('#selNum'),

    viewer: $('#viewer'),
    vwClose: $('#vwClose'),
    vwCount: $('#vwCount'),
    vwDl: $('#vwDl'),
    vwStage: $('#vwStage'),
    vwPrev: $('#vwPrev'),
    vwNext: $('#vwNext'),
    vwName: $('#vwName'),
    vwSelect: $('#vwSelect'),
    vwConvert: $('#vwConvert'),

    mini: $('#mini'),
    miniText: $('#miniText'),
    toast: $('#toast')
  };

  var state = {
    items: [],
    groups: { image: [], video: [] },
    tab: 'all',
    sel: Object.create(null),
    selCount: 0,
    engine: 'direct',
    lastUrl: '',
    scanning: false,
    errAction: 'retry',
    scanAbort: null,
    fmt: 'mp3',
    convertFile: null,
    busyDl: false,
    viewer: { open: false, list: [], idx: 0, zoomed: false },
    installEvent: null,
    started: false
  };

  /* ---------- toast / mini ---------- */
  var toastTimer = 0;
  function toast(msg, kind, ms) {
    if (!els.toast) return;
    els.toast.textContent = msg;
    els.toast.className = 'toast show' + (kind ? ' ' + kind : '');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () {
      els.toast.className = 'toast' + (kind ? ' ' + kind : '');
    }, ms || 2400);
  }

  function mini(msg) {
    if (!els.mini) return;
    if (!msg) { els.mini.classList.add('hidden'); return; }
    els.miniText.textContent = msg;
    els.mini.classList.remove('hidden');
  }

  /* ---------- 文本/URL 处理 ---------- */
  function pickUrl(text) {
    if (!text) return '';
    var t = String(text).trim();
    if (!t) return '';
    var m = t.match(/https?:\/\/[^\s<>"'，。；：、）】》]+/i);
    if (m) return m[0].replace(/[),.;!?'"\]}]+$/, '');
    if (/^[\w-]+(\.[\w-]+)+(\/\S*)?$/.test(t)) return 'https://' + t;
    return '';
  }

  function hostOf(url) {
    try { return new URL(url).host.replace(/^www\./, ''); } catch (e) { return ''; }
  }

  function fmtSize(n) {
    n = Number(n) || 0;
    if (!n) return '';
    if (n < 1024) return n + 'B';
    if (n < 1024 * 1024) return (n / 1024).toFixed(0) + 'KB';
    if (n < 1024 * 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + 'MB';
    return (n / 1024 / 1024 / 1024).toFixed(2) + 'GB';
  }

  function fileName(it) {
    var n = String(it.name || '').trim();
    var ext = String(it.ext || '').trim().replace(/^\./, '');
    if (!n) n = (it.kind === 'video' ? 'video' : 'image') + '-' + (it.id || 'item');
    if (ext && !new RegExp('\\.' + ext + '$', 'i').test(n)) n += '.' + ext;
    return n.replace(/[\\/:*?"<>|\r\n]+/g, '_').slice(0, 80) || 'download';
  }

  function proxyUrl(it) {
    var u = it.thumb || it.url;
    if (!u) return '';
    return '/api/proxy?u=' + encodeURIComponent(u) + '&ref=' + encodeURIComponent(it.referer || '');
  }

  function dlUrl(it) {
    var q = 'u=' + encodeURIComponent(it.url) +
            '&ref=' + encodeURIComponent(it.referer || '') +
            '&name=' + encodeURIComponent(fileName(it));
    // 抖音直链会过期：带上来源页，后端 403 时能重解析一次
    if (it.via === 'dy' || it.source === 'douyin') {
      q += '&page=' + encodeURIComponent(it.page || '') + '&via=dy';
    }
    if (it.kind === 'video') {
      var u = String(it.url || '');
      if (/\.m3u8(\?|#|$)/i.test(u) || String(it.ext || '').toLowerCase() === 'm3u8') return '/api/hls?' + q;
      if (state.engine === 'ytdlp' || it.via === 'yt') return '/api/yt?' + q;
    }
    return '/api/dl?' + q;
  }

  function triggerDownload(url) {
    var a = document.createElement('a');
    a.href = url;
    a.setAttribute('download', '');
    a.rel = 'noopener';
    a.style.display = 'none';
    document.body.appendChild(a);
    try { a.click(); } catch (e) { location.href = url; }
    setTimeout(function () { a.remove(); }, 2000);
  }

  /* ---------- 进度 ---------- */
  var pv = { t0: 0, timer: 0 };
  var STAGE_PCT = { open: 16, probe: 54, engine: 76, done: 100 };
  var STAGE_ORDER = ['open', 'probe', 'engine', 'done'];

  function pvReset() {
    clearInterval(pv.timer);
    pv.t0 = Date.now();
    els.progress.classList.remove('hidden');
    els.pvFill.className = 'pv-fill';
    els.pvFill.style.width = '4%';
    els.pvText.textContent = '正在打开网页…';
    els.pvTime.textContent = '0.0s';
    els.steps.forEach(function (s) { s.classList.remove('on', 'ok'); });
    els.steps[0].classList.add('on');
    pv.timer = setInterval(function () {
      els.pvTime.textContent = ((Date.now() - pv.t0) / 1000).toFixed(1) + 's';
    }, 100);
  }

  function pvStage(stage, msg) {
    if (!stage) return;
    var pct = STAGE_PCT[stage] || 40;
    els.pvFill.style.width = pct + '%';
    if (msg) els.pvText.textContent = msg;
    var active = STAGE_ORDER.indexOf(stage);
    els.steps.forEach(function (s) {
      var i = STAGE_ORDER.indexOf(s.getAttribute('data-s'));
      s.classList.toggle('on', i === active);
      s.classList.toggle('ok', i < active);
    });
  }

  function pvDone(ok) {
    clearInterval(pv.timer);
    els.pvFill.style.width = '100%';
    els.pvFill.className = 'pv-fill ' + (ok ? 'ok' : 'bad');
    els.steps.forEach(function (s) { s.classList.remove('on'); s.classList.add('ok'); });
    setTimeout(function () { els.progress.classList.add('hidden'); }, ok ? 320 : 700);
  }

  /* ---------- 扫描 ---------- */
  function setBusy(on) {
    state.scanning = on;
    els.goBtn.disabled = on;
    els.btnSpin.classList.toggle('hidden', !on);
    els.btnText.textContent = on ? '解析中…' : '解析';
  }

  function showError(msg, code) {
    var ui = code && ERROR_UI[code];
    els.error.classList.remove('hidden');
    els.errText.textContent = (ui && ui.text) || msg || '解析失败，换个链接再试。';
    els.retryBtn.textContent = (ui && ui.action) || '重试';
    state.errAction = (ui && ui.key) || 'retry';
    els.result.classList.add('hidden');
    els.bar.classList.add('hidden');
  }

  /* 错误码 → 一句原因 + 一个可执行动作（不重构错误卡）。 */
  var ERROR_UI = {
    NETWORK: { text: '网络连不上。检查网络或代理后重试。', action: '重试', key: 'retry' },
    PAGE_LOAD_FAILED: { text: '页面没打开。检查网络后重试。', action: '重试', key: 'retry' },
    DETAIL_TIMEOUT: { text: '解析超时。网络慢或页面需要验证，可以重试。', action: '重试', key: 'retry' },
    SHORT_LINK_FAILED: { text: '短链没展开。复制完整链接再试。', action: '复制链接', key: 'copy' },
    SHORT_LINK_TIMEOUT: { text: '短链展开超时。检查网络后重试。', action: '重试', key: 'retry' },
    SHORT_LINK_UNRESOLVED: { text: '短链已失效，找不到作品。换一个分享链接。', action: '换链接', key: 'input' },
    NEEDS_VERIFICATION: { text: '抖音要求安全验证。请到系统浏览器打开链接完成验证，再复制完整地址回来。', action: '复制链接', key: 'copy' },
    NEEDS_LOGIN: { text: '这个作品要登录才能看。请换成公开作品，或在浏览器登录后复制直链。', action: '复制链接', key: 'copy' },
    UNAVAILABLE: { text: '作品不存在、已删除或不可见。换一个公开作品链接。', action: '换链接', key: 'input' },
    PAGE_STRUCTURE_CHANGED: { text: '页面结构变了，暂时认不出媒体。稍后重试，或复制链接用浏览器打开。', action: '重试', key: 'retry' },
    MEDIA_UNSUPPORTED: { text: '这个作品没有可下载的视频或图片。换一个作品。', action: '换链接', key: 'input' },
    NO_MEDIA: { text: '页面里没有找到能下载的内容。换一个链接。', action: '换链接', key: 'input' },
    NO_LINK: { text: '没识别到链接。粘贴作品链接或分享文案。', action: '换链接', key: 'input' },
    INVALID_INPUT: { text: '链接格式不对，要以 http 或 https 开头。', action: '换链接', key: 'input' }
  };

  var scanGen = 0;

  function hideError() { els.error.classList.add('hidden'); }

  function scan(input, opts) {
    opts = opts || {};
    var raw = pickUrl(input) || String(input || '').trim();
    if (!raw) {
      toast('先贴一个链接进来', 'bad');
      els.urlInput.focus();
      return Promise.resolve(false);
    }
    if (!/^https?:\/\//i.test(raw)) {
      toast('链接要以 http 开头', 'bad');
      return Promise.resolve(false);
    }
    if (state.scanning) return Promise.resolve(false);

    // 新一轮开始前，终止上一轮残留的 SSE / POST，避免两个解析任务同时跑。
    if (state.scanAbort) { try { state.scanAbort.abort(); } catch (e) {} state.scanAbort = null; }
    if (state.scanEs) { try { state.scanEs.close(); } catch (e) {} state.scanEs = null; }
    var gen = ++scanGen;

    state.lastUrl = raw;
    state.engine = 'direct';
    hideError();
    els.result.classList.add('hidden');
    els.bar.classList.add('hidden');
    setBusy(true);
    pvReset();

    var tp = !!(els.tpToggle && els.tpToggle.checked);
    var settled = false;
    var usedFallback = false;
    var es = null;
    var idleTimer = 0;
    var ac = window.AbortController ? new AbortController() : null;
    state.scanAbort = ac;

    function cleanup() {
      clearTimeout(idleTimer);
      if (es) {
        var dying = es;
        try { dying.close(); } catch (e) {}
        es = null;
        if (state.scanEs === dying) state.scanEs = null;
      }
      if (ac && !ac.signal.aborted) { try { ac.abort(); } catch (e) {} }
      if (state.scanAbort === ac) state.scanAbort = null;
    }

    function settle(fn, arg, code, ok) {
      if (settled || gen !== scanGen) return;
      settled = true;
      cleanup();
      setBusy(false);
      pvDone(!!ok);
      if (fn) fn(arg, code);
    }

    function fallback(reason) {
      if (settled || usedFallback || gen !== scanGen) return;
      usedFallback = true;
      // 先彻底终止旧流，再发回退请求；避免同一链接同时跑 SSE + POST。
      if (es) {
        var dying = es;
        try { dying.close(); } catch (e) {}
        es = null;
        if (state.scanEs === dying) state.scanEs = null;
      }
      clearTimeout(idleTimer);
      if (reason) els.pvText.textContent = '连接不稳定，改用普通解析…';
      postScan(raw, tp, ac ? ac.signal : null, function (d, err) {
        if (gen !== scanGen) return;
        if (d && d.ok) settle(renderResult, d, '', true);
        else {
          settle(
            showError,
            (d && d.error) || err || '服务没有返回结果，稍后重试。',
            d && d.error_code,
            false
          );
        }
      });
    }

    function armIdle() {
      clearTimeout(idleTimer);
      // 服务端每 3s 发 ping；8s 仍无任何事件才认为流断了。
      idleTimer = setTimeout(function () { fallback('stream-idle'); }, SSE_IDLE);
    }

    // --- EventSource 优先 ---
    if (window.EventSource) {
      try {
        es = new EventSource('/api/scan_stream?url=' + encodeURIComponent(raw) + '&tp=' + (tp ? 1 : 0));
        state.scanEs = es;
      } catch (e) {
        es = null;
      }
    }

    if (es) {
      armIdle();
      es.onmessage = function (ev) {
        if (settled || gen !== scanGen) return;
        armIdle();
        var d;
        try { d = JSON.parse(ev.data); } catch (e) { return; }
        if (!d) return;
        if (d.stage === 'ping') return;
        if (d.stage === 'done') {
          if (d.ok && d.result) settle(renderResult, d.result, '', true);
          else settle(showError, d.error || '解析失败', d.error_code, false);
          return;
        }
        pvStage(d.stage, d.msg);
      };
      es.onerror = function () {
        if (settled || usedFallback || gen !== scanGen) return;
        fallback('stream-error');
      };
    } else {
      fallback('');
    }

    return new Promise(function (resolve) {
      var iv = setInterval(function () {
        if (settled) { clearInterval(iv); resolve(true); }
      }, 80);
    });
  }

  function postScan(raw, tp, signal, done) {
    els.pvText.textContent = '正在解析这个页面…';
    var init = {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: raw, allow_third_party: tp })
    };
    if (signal) init.signal = signal;
    return fetch('/api/scan', init).then(function (r) {
      return r.json();
    }).then(function (d) {
      done(d, null);
      return d;
    }).catch(function (e) {
      if (e && e.name === 'AbortError') return null;
      done(null, '连不上本地服务：' + (e && e.message ? e.message : '未知错误'));
      return null;
    });
  }

  /* ---------- 结果渲染 ---------- */
  function visibleList() {
    if (state.tab === 'all') return state.items.slice();
    var ids = state.groups[state.tab] || [];
    return ids.map(function (i) { return state.items[i]; }).filter(Boolean);
  }

  function renderResult(res) {
    state.items = (res.items || []).slice();
    state.engine = res.engine || 'direct';
    state.sel = Object.create(null);
    state.selCount = 0;
    state.tab = 'all';

    var imgs = [], vids = [];
    state.items.forEach(function (it, i) {
      it._i = i;
      if (it.kind === 'video') vids.push(i); else imgs.push(i);
    });
    state.groups = { image: imgs, video: vids };

    els.pageTitle.textContent = res.title || hostOf(res.url || state.lastUrl) || '这个页面';
    var bits = [state.items.length + ' 项'];
    if (imgs.length) bits.push(imgs.length + ' 张图');
    if (vids.length) bits.push(vids.length + ' 个视频');
    els.pageMeta.textContent = bits.join(' · ');

    var note = res.note || (res.engine === 'douyin' ? '抖音第三方解析' : (res.engine === 'ytdlp' ? 'yt-dlp 解析' : ''));
    if (note) { els.engineNote.textContent = note; els.engineNote.classList.remove('hidden'); }
    else els.engineNote.classList.add('hidden');

    buildTabs();
    renderGrid();
    updateBar();

    if (!state.items.length) {
      els.empty.classList.remove('hidden');
      els.grid.classList.add('hidden');
      els.tabs.classList.add('hidden');
    } else {
      els.empty.classList.add('hidden');
      els.grid.classList.remove('hidden');
      els.tabs.classList.toggle('hidden', !(imgs.length && vids.length));
    }

    els.result.classList.remove('hidden');
    els.bar.classList.remove('hidden');
    try { els.result.scrollIntoView({ block: 'start', behavior: 'smooth' }); } catch (e) {}
    return true;
  }

  function buildTabs() {
    var imgs = state.groups.image.length, vids = state.groups.video.length;
    var defs = [{ k: 'all', t: '全部', n: state.items.length }];
    if (imgs) defs.push({ k: 'image', t: '图片', n: imgs });
    if (vids) defs.push({ k: 'video', t: '视频', n: vids });
    els.tabs.innerHTML = '';
    defs.forEach(function (d) {
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'tab' + (d.k === state.tab ? ' on' : '');
      b.setAttribute('data-tab', d.k);
      b.innerHTML = d.t + '<small>' + d.n + '</small>';
      b.addEventListener('click', function () {
        state.tab = d.k;
        $$('.tab', els.tabs).forEach(function (x) { x.classList.toggle('on', x === b); });
        renderGrid();
        updateBar();
      });
      els.tabs.appendChild(b);
    });
  }

  function tileHtml(it) {
    var thumb = proxyUrl(it);
    var isV = it.kind === 'video';
    var name = fileName(it);
    var size = fmtSize(it.size);
    var inner = thumb
      ? '<img src="' + thumb + '" alt="' + esc(name) + '" loading="lazy" decoding="async">'
      : '<span class="ph">' + (isV ? '▶' : '🖼') + '</span>';
    return '<article class="tile" data-id="' + esc(it.id) + '" data-kind="' + (isV ? 'video' : 'image') + '">' +
      '<div class="thumb" role="button" tabindex="-1" aria-label="预览">' + inner +
        '<span class="type' + (isV ? ' video' : '') + '">' + (isV ? '▶ 视频' : '图片') + '</span>' +
        (size ? '<span class="size">' + size + '</span>' : '') +
      '</div>' +
      '<button class="check" type="button" aria-label="选中" aria-pressed="false"></button>' +
      '<button class="tile-dl" type="button" aria-label="下载">⤓</button>' +
    '</article>';
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function renderGrid() {
    var list = visibleList();
    els.grid.innerHTML = list.map(tileHtml).join('');
    list.forEach(function (it) {
      var node = els.grid.querySelector('.tile[data-id="' + cssEsc(it.id) + '"]');
      if (node) node.classList.toggle('sel', !!state.sel[it.id]);
    });
  }

  function cssEsc(s) { return String(s).replace(/["\\]/g, '\\$&'); }

  /* ---------- 选中 ---------- */
  function updateBar() {
    var total = state.items.length;
    els.selCount.textContent = '已选 ' + state.selCount + ' / 共 ' + total;
    els.selNum.textContent = state.selCount;
    els.downloadBtn.disabled = !state.selCount || state.busyDl;
    var list = visibleList();
    var all = list.length > 0 && list.every(function (it) { return !!state.sel[it.id]; });
    els.selAll.textContent = all ? '全不选' : '全选';
    els.selAll.setAttribute('data-all', all ? '1' : '0');
  }

  function setSel(id, on) {
    var it = byId(id);
    if (!it) return;
    if (on) { if (!state.sel[id]) { state.sel[id] = true; state.selCount++; } }
    else { if (state.sel[id]) { delete state.sel[id]; state.selCount--; } }
    var node = els.grid.querySelector('.tile[data-id="' + cssEsc(id) + '"]');
    if (node) {
      node.classList.toggle('sel', on);
      var c = node.querySelector('.check');
      if (c) c.setAttribute('aria-pressed', on ? 'true' : 'false');
    }
    updateBar();
  }

  function toggleSel(id) {
    setSel(id, !state.sel[id]);
    if (els.viewer.classList.contains('open') || !els.viewer.classList.contains('hidden')) syncViewerActs();
  }

  function byId(id) {
    for (var i = 0; i < state.items.length; i++) if (state.items[i].id === id) return state.items[i];
    return null;
  }

  function selectedItems() {
    return state.items.filter(function (it) { return !!state.sel[it.id]; });
  }

  /* ---------- 下载 ---------- */
  function downloadMany(list, label) {
    if (!list.length) { toast('先选几项再下载', 'bad'); return Promise.resolve(0); }
    if (state.busyDl) { toast('正在下载，请稍等'); return Promise.resolve(0); }
    state.busyDl = true;
    updateBar();
    var n = 0;
    els.dlAll.classList.add('busy');

    return new Promise(function (resolve) {
      function step() {
        if (n >= list.length) {
          state.busyDl = false;
          updateBar();
          els.dlAll.classList.remove('busy');
          toast('已发起 ' + n + ' 个下载，去「下载」文件夹看看', 'ok');
          resolve(n);
          return;
        }
        var it = list[n];
        triggerDownload(dlUrl(it));
        n++;
        if (label) mini(label + ' ' + n + '/' + list.length);
        if (n >= list.length) { step(); return; }
        setTimeout(step, QUEUE_GAP);
      }
      step();
    });
  }

  function downloadOne(it) {
    triggerDownload(dlUrl(it));
    toast('开始下载 ' + fileName(it));
    var node = els.grid.querySelector('.tile[data-id="' + cssEsc(it.id) + '"] .tile-dl');
    if (node) { node.classList.add('busy'); setTimeout(function () { node.classList.remove('busy'); }, 1200); }
  }

  /* ---------- 预览 ---------- */
  function viewerList() { return visibleList(); }

  function openViewer(it) {
    var list = viewerList();
    var idx = list.indexOf(it);
    if (idx < 0) { list = state.items.slice(); idx = list.indexOf(it); }
    if (idx < 0) return;
    state.viewer.list = list;
    state.viewer.idx = idx;
    state.viewer.open = true;
    state.viewer.zoomed = false;
    els.viewer.classList.remove('hidden');
    document.body.classList.add('lock');
    renderViewer();
  }

  function renderViewer() {
    var v = state.viewer;
    if (zoomTapTimer) {
      clearTimeout(zoomTapTimer);
      zoomTapTimer = null;
    }
    var it = v.list[v.idx];
    if (!it) return closeViewer();
    var src = it.kind === 'video' ? dlUrl(it) : proxyUrl(it);
    els.vwStage.innerHTML = '';
    v.zoomed = false;

    var media;
    if (it.kind === 'video') {
      media = document.createElement('video');
      media.src = src;
      media.controls = true;
      media.playsInline = true;
      media.preload = 'metadata';
      media.setAttribute('playsinline', '');
      if (it.thumb) media.poster = it.thumb.indexOf('http') === 0 ? proxyUrl({ url: it.thumb, thumb: it.thumb, referer: it.referer }) : proxyUrl(it);
    } else {
      media = document.createElement('img');
      media.src = src;
      media.alt = fileName(it);
      media.draggable = false;
    }
    media.setAttribute('data-kind', it.kind);
    els.vwStage.appendChild(media);

    els.vwCount.textContent = (v.idx + 1) + ' / ' + v.list.length;
    els.vwName.textContent = fileName(it);
    els.vwPrev.disabled = false;
    els.vwNext.disabled = false;
    els.vwConvert.classList.toggle('hidden', it.kind !== 'video');
    syncViewerActs();
  }

  function syncViewerActs() {
    var it = state.viewer.list[state.viewer.idx];
    if (!it) return;
    var on = !!state.sel[it.id];
    els.vwSelect.textContent = on ? '已选' : '选中';
    els.vwSelect.classList.toggle('on', on);
  }

  function closeViewer() {
    state.viewer.open = false;
    els.viewer.classList.add('hidden');
    document.body.classList.remove('lock');
    var media = els.vwStage.querySelector('video');
    if (media) { try { media.pause(); } catch (e) {} }
    els.vwStage.innerHTML = '';
  }

  function vwGo(delta) {
    var v = state.viewer;
    if (!v.list.length) return;
    var n = v.list.length;
    v.idx = (v.idx + delta + n) % n;
    renderViewer();
  }

  var zoomTapTimer = null;

  function toggleZoom(ev) {
    if (zoomTapTimer) { clearTimeout(zoomTapTimer); zoomTapTimer = null; }
    var media = els.vwStage.querySelector('img');
    if (!media) return;
    state.viewer.zoomed = !media.classList.contains('zoom');
    media.classList.toggle('zoom', state.viewer.zoomed);
    if (state.viewer.zoomed && ev) {
      var r = media.getBoundingClientRect();
      var x = ((ev.clientX - r.left) / r.width) * 100;
      var y = ((ev.clientY - r.top) / r.height) * 100;
      media.style.transformOrigin = Math.max(0, Math.min(100, x)) + '% ' + Math.max(0, Math.min(100, y)) + '%';
    } else if (media) {
      media.style.transformOrigin = 'center center';
    }
  }

  /* ---------- 转音频 ---------- */
  function setConvState(s) {
    els.convStatus.classList.remove('hidden', 'is-error', 'is-ok');
    if (s === 'err') els.convStatus.classList.add('is-error');
    if (s === 'ok') els.convStatus.classList.add('is-ok');
  }

  function convProgress(pct, text, time) {
    els.convFill.classList.remove('indet');
    if (pct == null) { els.convFill.classList.add('indet'); }
    else els.convFill.style.width = Math.max(2, Math.min(100, pct)) + '%';
    if (text != null) els.convText.textContent = text;
    if (time != null) els.convTime.textContent = time;
  }

  function showConvResult(res) {
    var old = $('.conv-result', els.convStatus);
    if (old) old.remove();
    var a = document.createElement('a');
    a.className = 'conv-result';
    a.href = res.url;
    a.setAttribute('download', res.name || '');
    a.textContent = '⬇ ' + (res.name || '音频已生成') + (res.size ? '（' + fmtSize(res.size) + '）' : '');
    els.convStatus.appendChild(a);
    // 渲染完再自动触发一次下载（不跳走当前页面）
    setTimeout(function () { triggerDownload(res.url); }, 60);
    return a;
  }

  function convertUpload() {
    var f = state.convertFile;
    if (!f) { toast('先选一个视频', 'bad'); return; }
    var fmt = state.fmt;
    els.convBtn.disabled = true;
    setConvState('run');
    convProgress(2, '正在上传…', fmtSize(f.size));
    var t0 = Date.now();
    var xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/convert/upload?fmt=' + encodeURIComponent(fmt), true);
    xhr.setRequestHeader('X-Filename', encodeURIComponent(f.name || 'video.mp4'));
    xhr.setRequestHeader('Content-Type', 'application/octet-stream');

    xhr.upload.onprogress = function (e) {
      if (!e.lengthComputable) return;
      var pct = (e.loaded / e.total) * 100;
      convProgress(pct * 0.85, '上传中 ' + Math.round(pct) + '%', fmtSize(e.loaded) + ' / ' + fmtSize(e.total));
      if (pct >= 100) convProgress(null, '上传完成，正在转 ' + fmt.toUpperCase() + '…', '');
    };

    xhr.onload = function () {
      if (xhr.status === 413) {
        els.convBtn.disabled = false;
        setConvState('err'); convProgress(100, '文件太大了（上限 800MB）');
        return;
      }
      var d = null;
      try { d = JSON.parse(xhr.responseText); } catch (e) {}
      els.convBtn.disabled = false;
      if (d && d.ok) {
        setConvState('ok');
        var secs = ((Date.now() - t0) / 1000).toFixed(1);
        convProgress(100, '转好了，共 ' + secs + 's', '');
        showConvResult(d);
        toast('音频已生成：' + (d.name || ''), 'ok');
      } else {
        setConvState('err');
        convProgress(100, (d && d.error) || '转换失败，换个视频再试', '');
      }
    };
    xhr.onerror = function () {
      els.convBtn.disabled = false;
      setConvState('err');
      convProgress(100, '上传失败，检查一下服务是否还在跑');
    };
    xhr.ontimeout = xhr.onerror;
    xhr.send(f);
  }

  function convertLink(it) {
    if (!it) return;
    var fmt = state.fmt;
    var u = String(it.url || '');
    var via = /\.m3u8(\?|#|$)/i.test(u) || String(it.ext || '').toLowerCase() === 'm3u8' ? 'hls'
      : ((state.engine === 'ytdlp' || it.via === 'yt') ? 'yt' : 'direct');
    mini('转 ' + fmt.toUpperCase() + ' 中…');
    els.vwConvert.disabled = true;
    return fetch('/api/convert/link', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ u: it.url, ref: it.referer || '', via: via, fmt: fmt, name: it.name || '' })
    }).then(function (r) { return r.json(); }).then(function (d) {
      mini('');
      els.vwConvert.disabled = false;
      if (d && d.ok) {
        toast('音频已生成：' + (d.name || ''), 'ok');
        setConvState('ok');
        els.convStatus.classList.remove('hidden');
        convProgress(100, '已生成 ' + (d.name || '音频'), '');
        showConvResult(d);
      } else {
        toast((d && d.error) || '转换失败', 'bad', 3200);
      }
      return d;
    }).catch(function () {
      mini('');
      els.vwConvert.disabled = false;
      toast('转换请求失败', 'bad');
    });
  }

  /* ---------- 帮助 / 设置弹层 ---------- */
  function openHelp(show) {
    els.help.classList.toggle('hidden', !show);
    document.body.classList.toggle('lock', !!show);
  }

  /* ---------- 抖音接口配置 ---------- */
  function loadConfig() {
    return fetch('/api/config').then(function (r) { return r.json(); }).catch(function () { return {}; });
  }

  function configDouyin() {
    loadConfig().then(function (cfg) {
      var cur = (cfg && cfg.douyin_apis && cfg.douyin_apis[0]) || '';
      var v = window.prompt('抖音解析接口地址（含 {url} 占位符，留空表示用默认源）', cur);
      if (v === null) return;
      v = v.trim();
      if (v && !/^https?:\/\//i.test(v)) { toast('要以 http:// 或 https:// 开头', 'bad'); return; }
      if (v && v.indexOf('{url}') < 0) { toast('地址里要包含 {url}', 'bad'); return; }
      var body = v ? { douyin_apis: [v] } : { douyin_apis: null };
      fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(v ? { douyin_apis: [v] } : { douyin_apis: [] })
      }).then(function (r) { return r.json(); }).then(function (d) {
        if (d && d.ok) toast(v ? '抖音接口已保存' : '已恢复默认接口', 'ok');
        else toast((d && d.error) || '保存失败', 'bad');
      }).catch(function () { toast('保存失败：服务没响应', 'bad'); });
    });
  }

  /* ---------- 健康检查 ---------- */
  function checkHealth() {
    return fetch('/api/health').then(function (r) { return r.json(); }).then(function (d) {
      if (d && d.ok) {
        els.status.innerHTML = '<i class="dot ok"></i>已就绪 · v' + esc(d.version || '1.0.0') +
          (d.convert === false ? ' · 转换不可用' : '');
      } else {
        els.status.innerHTML = '<i class="dot bad"></i>服务异常';
      }
      return d;
    }).catch(function () {
      els.status.innerHTML = '<i class="dot bad"></i>没连上本地服务';
    });
  }

  /* ---------- 分享参数 / 首屏 ---------- */
  function readShare() {
    var q = new URLSearchParams(location.search);
    var u = q.get('url') || '';
    var text = q.get('text') || '';
    if (!u && text) u = pickUrl(text);
    if (!u) u = pickUrl(q.toString());
    return u || '';
  }

  function boot() {
    hideError();
    checkHealth();

    if (IS_ANDROID) {
      els.installBtnTop.classList.add('hidden');
    } else {
      window.addEventListener('beforeinstallprompt', function (e) {
        e.preventDefault();
        state.installEvent = e;
        els.installBtnTop.classList.remove('hidden');
      });
      window.addEventListener('appinstalled', function () {
        els.installBtnTop.classList.add('hidden');
        toast('已添加到桌面', 'ok');
      });
    }

    if (IS_APP) els.wvTip.classList.remove('hidden');

    var shared = readShare();
    if (shared) {
      els.urlInput.value = shared;
      try { history.replaceState(null, '', '/'); } catch (e) {}
      scan(shared);
    } else {
      setTimeout(function () { scan(DEMO_URL); }, 260);
    }

    if ('serviceWorker' in navigator && /^https?:$/.test(location.protocol)) {
      window.addEventListener('load', function () {
        navigator.serviceWorker.register('/sw.js').catch(function () {});
      });
    }
  }

  /* ---------- 事件绑定 ---------- */
  function bind() {
    els.goBtn.addEventListener('click', function () { scan(els.urlInput.value); });

    els.urlInput.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); scan(els.urlInput.value); }
    });

    els.pasteBtn.addEventListener('click', function () {
      if (navigator.clipboard && navigator.clipboard.readText) {
        navigator.clipboard.readText().then(function (t) {
          if (!t) { toast('剪贴板是空的'); return; }
          els.urlInput.value = t.trim();
          var u = pickUrl(t);
          if (u) els.urlInput.value = u;
          toast('已粘贴');
        }).catch(function () { els.urlInput.focus(); toast('长按输入框手动粘贴'); });
      } else {
        els.urlInput.focus();
        toast('长按输入框手动粘贴');
      }
    });

    var demo = function () {
      els.urlInput.value = DEMO_URL;
      scan(DEMO_URL);
    };
    els.demoBtn.addEventListener('click', demo);
    els.errDemoBtn.addEventListener('click', demo);
    els.emptyDemoBtn.addEventListener('click', demo);

    els.helpBtn.addEventListener('click', function () { openHelp(true); });
    els.helpClose.addEventListener('click', function () { openHelp(false); });
    els.help.addEventListener('click', function (e) {
      if (e.target === els.help) openHelp(false);
    });

    els.dyApiBtn.addEventListener('click', configDouyin);

    els.retryBtn.addEventListener('click', function () {
      var act = state.errAction || 'retry';
      if (act === 'copy') {
        var t = state.lastUrl || els.urlInput.value.trim();
        if (!t) { els.urlInput.focus(); return; }
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(t).then(function () {
            toast('链接已复制，到浏览器打开', 'ok');
          }).catch(function () {
            els.urlInput.value = t;
            els.urlInput.focus();
            els.urlInput.select();
            toast('长按输入框复制链接');
          });
        } else {
          els.urlInput.value = t;
          els.urlInput.focus();
          els.urlInput.select();
          toast('长按输入框复制链接');
        }
        return;
      }
      if (act === 'input') {
        els.urlInput.focus();
        els.urlInput.select();
        return;
      }
      if (state.lastUrl) scan(state.lastUrl); else demo();
    });

    els.newLink.addEventListener('click', function () {
      els.result.classList.add('hidden');
      els.bar.classList.add('hidden');
      els.urlInput.value = '';
      els.urlInput.focus();
      window.scrollTo({ top: 0, behavior: 'smooth' });
    });

    els.copyUrl.addEventListener('click', function () {
      var t = location.origin && location.origin !== 'null' ? location.origin : location.href;
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(t).then(function () { toast('网址已复制', 'ok'); })
          .catch(function () { toast('复制失败，手动输入：' + t); });
      } else {
        toast('手动输入：' + t);
      }
    });

    els.installBtnTop.addEventListener('click', function () {
      var ev = state.installEvent;
      if (!ev) { toast('这个浏览器不支持直接添加，请用菜单里的「添加到主屏幕」'); return; }
      ev.prompt();
      ev.userChoice && ev.userChoice.then(function () {
        state.installEvent = null;
        els.installBtnTop.classList.add('hidden');
      });
    });

    /* --- 转音频 --- */
    $$('.fmt', els.fmtRow).forEach(function (b) {
      b.addEventListener('click', function () {
        state.fmt = b.getAttribute('data-fmt');
        $$('.fmt', els.fmtRow).forEach(function (x) { x.classList.toggle('on', x === b); });
      });
    });

    els.videoFile.addEventListener('change', function () {
      var f = els.videoFile.files && els.videoFile.files[0];
      state.convertFile = f || null;
      if (f) {
        els.fileLabel.textContent = f.name + ' · ' + fmtSize(f.size);
        els.filePick.classList.add('picked');
        els.convBtn.disabled = false;
        var old = $('.conv-result', els.convStatus);
        if (old) old.remove();
        convProgress(0, '准备中…', '');
        els.convStatus.classList.add('hidden');
      } else {
        els.fileLabel.textContent = '视频';
        els.filePick.classList.remove('picked');
        els.convBtn.disabled = true;
      }
    });

    els.convBtn.addEventListener('click', convertUpload);

    /* --- 网格（事件委托） --- */
    els.grid.addEventListener('click', function (e) {
      var tile = e.target.closest('.tile');
      if (!tile) return;
      var it = byId(tile.getAttribute('data-id'));
      if (!it) return;
      if (e.target.closest('.check')) { toggleSel(it.id); return; }
      if (e.target.closest('.tile-dl')) { downloadOne(it); return; }
      if (e.target.closest('.thumb')) { openViewer(it); return; }
    });

    els.selAll.addEventListener('click', function () {
      var list = visibleList();
      var all = list.length > 0 && list.every(function (it) { return !!state.sel[it.id]; });
      list.forEach(function (it) { setSel(it.id, !all); });
      updateBar();
    });

    els.downloadBtn.addEventListener('click', function () {
      var list = selectedItems();
      dlBusyGuard(downloadMany(list, '下载中'));
    });

    els.dlAll.addEventListener('click', function () {
      var list = state.items.slice();
      downloadMany(list, '下载中');
    });

    /* --- 预览层 --- */
    els.vwClose.addEventListener('click', closeViewer);
    els.vwPrev.addEventListener('click', function (e) { e.stopPropagation(); vwGo(-1); });
    els.vwNext.addEventListener('click', function (e) { e.stopPropagation(); vwGo(1); });
    els.vwDl.addEventListener('click', function () {
      var it = state.viewer.list[state.viewer.idx];
      if (it) downloadOne(it);
    });
    els.vwSelect.addEventListener('click', function () {
      var it = state.viewer.list[state.viewer.idx];
      if (it) toggleSel(it.id);
    });
    els.vwConvert.addEventListener('click', function () {
      var it = state.viewer.list[state.viewer.idx];
      if (it) convertLink(it);
    });

    // 双击缩放
    els.vwStage.addEventListener('dblclick', function (e) {
      if (e.target.closest('video')) return;
      toggleZoom(e);
    });

    // 单击图片：放大状态下点一下复原（延后 240ms，避免和双击缩放打架）
    els.vwStage.addEventListener('click', function (e) {
      var img = e.target.closest('img');
      if (!img) return;
      if (!state.viewer.zoomed) return;
      if (zoomTapTimer) clearTimeout(zoomTapTimer);
      zoomTapTimer = setTimeout(function () {
        zoomTapTimer = null;
        if (state.viewer.zoomed) toggleZoom(null);
      }, 240);
    });

    // 左右滑
    var swipe = { x: 0, y: 0, t: 0, active: false };
    els.vwStage.addEventListener('pointerdown', function (e) {
      if (e.pointerType === 'mouse' && e.button !== 0) return;
      swipe.x = e.clientX; swipe.y = e.clientY; swipe.t = Date.now(); swipe.active = true;
    });
    els.vwStage.addEventListener('pointerup', function (e) {
      if (!swipe.active) return;
      swipe.active = false;
      var dx = e.clientX - swipe.x;
      var dy = e.clientY - swipe.y;
      var dt = Date.now() - swipe.t;
      if (state.viewer.zoomed) return;
      if (Math.abs(dx) > 46 && Math.abs(dx) > Math.abs(dy) * 1.4 && dt < 900) {
        vwGo(dx < 0 ? 1 : -1);
      }
    });
    els.vwStage.addEventListener('pointercancel', function () { swipe.active = false; });

    // 点背景关闭
    els.viewer.addEventListener('click', function (e) {
      if (e.target === els.viewer) closeViewer();
    });

    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && !els.help.classList.contains('hidden')) {
        e.preventDefault(); openHelp(false); return;
      }
      if (els.viewer.classList.contains('hidden')) return;
      if (e.key === 'Escape') { e.preventDefault(); closeViewer(); }
      else if (e.key === 'ArrowRight') { e.preventDefault(); vwGo(1); }
      else if (e.key === 'ArrowLeft') { e.preventDefault(); vwGo(-1); }
    });
  }

  function dlBusyGuard(p) { return p; }

  /* ---------- 启动 ---------- */
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { bind(); boot(); });
  } else {
    bind(); boot();
  }

  /* 暴露给自动化测试 / 调试 */
  window.WebGrab = {
    state: state,
    scan: scan,
    openViewer: function (i) { openViewer(state.items[i] || visibleList()[i]); },
    closeViewer: closeViewer,
    downloadMany: function (n) { return downloadMany(state.items.slice(0, n || state.items.length), '下载中'); },
    renderResult: renderResult,
    toast: toast,
    convertLink: convertLink,
    tool: { pickUrl: pickUrl, fileName: fileName, dlUrl: dlUrl, visibleList: visibleList }
  };
})();
