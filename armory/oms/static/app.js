const APP_TITLE = "스마트 무기고 시스템";
document.title = APP_TITLE;

const NAV = [
  { id: "login", label: "로그인" },
  { id: "settings", label: "보관함 설정", need: "manage" },
  { id: "people", label: "관리자 정보", need: "manage" },
  { id: "users", label: "사용자 정보", need: "manage" },
  { id: "status", label: "총기 현황", need: "status" },
  { id: "combo", label: "총기·CCTV 현황" },
  { id: "guns", label: "총기 목록", need: "manage" },
  { id: "issue", label: "불출 내용", need: "manage" },
  { id: "history", label: "이력 관리", need: "history" },
  { id: "cctv", label: "CCTV" },
  { id: "patlite", label: "경광등" },
  { id: "rfid", label: "RFID" },
];
let cctvLiveTimer = null;
let cctvMetaTimer = null;
let cctvComboTimer = null;
let cctvLiveInFlight = false;
let cctvConnected = false;
/** @type {"1"|"1x1"|"2x2"} */
let cctvLayout = "1";
let cctvLayoutBeforeFull = "";
let cctvChannelHosts = [];
let comboFrame = "side";
let comboSwapped = false;
let comboLogOpen = false;
let comboLogHeight = 200;
let cctvPaneSwapped = false;
let comboSplitRatio = 0.5;
let cctvColRatio = 0.5;
let cctvRowRatio = 0.5;
let comboResizeObserver = null;
/** @type {"live"|"play"|"event"} */
let cctvViewMode = "live";
let cctvPlayUrl = "";
let cctvActiveCell = 0;
let cctvRecordings = [];
let cctvEvents = [];
let cctvRecWaitId = "";
let cctvContinuousOn = false;
let cctvContinuousDetail = "";
let cctvFfmpegOk = true;
let cctvRetentionDays = 1;
let cctvSettingsChannel = 1;
let cctvChannelCfgs = [];
let cctvSetPreviewTimer = null;

const state = { data: null, selected: 1, popup: null, gunIndex: null };
let historyQuery = {};
let historyView = [];
let historyScope = "gun";
let warnQueue = [];
let warnShowing = false;
const seenWarnIds = new Set();
let localWarnSeq = 900000;
/** @type {Array<{id:number,time:string,level:string,text:string}>} */
let localLogs = [];
const localLogKeys = new Set();
/** @type {Object.<string, boolean>} */
const boardOnlinePrev = {};
/** @type {Object.<string, boolean>} */
const slotPresentPrev = {};
/** @type {Object.<string, boolean>} */
const slotDoorPrev = {};
let liveWatchReady = false;
/** @type {Object.<string, boolean>} UI에 보이는 총기 보관상태(enrolled&&present) */
const slotStoredPrev = {};

const $ = (id) => document.getElementById(id);

function toast(text) {
  const el = $("toast");
  el.textContent = text;
  el.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { el.hidden = true; }, 2800);
}

async function api(path, options) {
  const res = await fetch(path, { credentials: "same-origin", ...options });
  const data = await res.json().catch(() => ({}));
  if (res.status === 404 && String(path || "").includes("/api/cctv")) {
    data.error = data.error || "구버전 OMS입니다. 프로그램을 종료한 뒤 최신 '스마트 무기고 시스템.exe'를 다시 실행하세요.";
    data.ok = false;
  }
  return { ok: res.ok && data.ok !== false, data, status: res.status };
}

async function post(path, body) {
  return api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
}

function escapeAttr(value) {
  return String(value ?? "").replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
}

function allowed(item) {
  if (item.id === "login" || item.id === "cctv" || item.id === "combo" || item.id === "patlite" || item.id === "rfid") return true;
  if (!item.need) return true;
  const can = state.data && state.data.can;
  if (!can) return false;
  return can[item.need] !== false;
}

function whoText(data) {
  const who = data && data.who;
  if (!who) return "주식회사 젤루텍";
  if (who.role === "operator") return "운영자";
  return `마스터 ${who.slot}`;
}

function selectedBoard(data) {
  return (data.boards || []).find((board) => board.selected) || (data.boards || [])[0] || null;
}

function purposeCount(data, name) {
  return fleetOf(data).purposes[name] || 0;
}

function paintChrome() {
  const data = state.data;
  $("who").textContent = whoText(data);
  const nav = $("toolbar");
  nav.innerHTML = "";
  NAV.forEach((item) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "mb" + (allowed(item) ? "" : " locked");
    btn.dataset.nav = item.id;
    btn.textContent = item.id === "login" ? ((data && data.who) ? "로그아웃" : "로그인") : item.label;
    nav.appendChild(btn);
  });
  alignLeftColumn();
}

function alignLeftColumn() {
  const main = document.querySelector(".oms-main");
  const button = document.querySelector('#toolbar [data-nav="people"]');
  if (!main || !button) return;
  const width = Math.ceil(button.getBoundingClientRect().right - main.getBoundingClientRect().left);
  if (width < 180) return;
  main.style.gridTemplateColumns = `${width}px minmax(0, 1fr) 240px`;
}

function paintNet() {
  const data = state.data;
  const box = $("netList");
  if (!data) {
    box.innerHTML = "";
    return;
  }
  const rows = (data.boards || []).map((board) => {
    const pistol = board.kind === "pistol";
    const gun = pistol ? "/assets/pistol-clear.png?v=59" : "/assets/rifle-side.png?v=59";
    const gunAlt = pistol ? "권총보관함" : "소총보관함";
    return `
    <button type="button" class="net-item ${board.online ? "online" : "linking"} ${board.selected ? "picked" : ""}" data-locker="${escapeAttr(board.id)}" title="${escapeAttr(board.host)}:${board.port}"><img class="net-gun${pistol ? " is-pistol" : ""}" src="${gun}" alt="${gunAlt}" /><span>${escapeAttr(board.name)}</span></button>`;
  }).join("");
  const unitName = (data.unit_name || "지문인식").trim() || "지문인식";
  box.innerHTML = `<div class="net-tree"><button type="button" class="net-head" id="netUnit" title="부대이름"><img src="/assets/zellootec.png" alt="" /><span>${escapeAttr(unitName)}</span></button><div class="net-children">${rows}</div></div>`;
  $("netUnit").onclick = () => openUnitDialog();
  box.querySelectorAll("[data-locker]").forEach((btn) => {
    btn.onclick = () => selectLocker(btn.dataset.locker);
    btn.ondblclick = () => {
      if (state.data && state.data.can && state.data.can.manage) openLockerDialog(btn.dataset.locker);
    };
  });
}

function scannerIcon() {
  return `<img class="scanner-device-icon" src="/assets/scanner.png" alt="지문인식기" />`;
}

function towerIcon() {
  return `<img class="scanner-tower-icon" src="/assets/patlite.png" alt="경광등" />`;
}

function rfidIcon() {
  return `<img class="scanner-rfid-icon" src="/assets/rfid.png" alt="RFID" />`;
}

function emptyFleetBucket() {
  return { lockers: 0, registered: 0, issued: 0, stored: 0, empty: 0, unpaid: 0, bays: 0 };
}

function fleetOf(data) {
  const names = ["사격", "훈련", "정비", "근무", "기타", "젤루텍"];
  const emptyPurposes = () => Object.fromEntries(names.map((name) => [name, 0]));
  const purposes = emptyPurposes();
  const riflePurposes = emptyPurposes();
  const pistolPurposes = emptyPurposes();
  const rifle = emptyFleetBucket();
  const pistol = emptyFleetBucket();
  const total = emptyFleetBucket();
  (data.boards || []).filter((item) => item.online).forEach((board) => {
    const kind = board.kind === "pistol" ? "pistol" : "rifle";
    const bucket = kind === "pistol" ? pistol : rifle;
    const kindPurposes = kind === "pistol" ? pistolPurposes : riflePurposes;
    bucket.lockers += 1;
    bucket.bays += 10;
    bucket.registered += Number(board.registered) || 0;
    bucket.issued += Number(board.issued) || 0;
    bucket.stored += Number(board.stored) || 0;
    bucket.empty += Number(board.empty) || 0;
    bucket.unpaid += Number(board.unpaid) || 0;
    total.lockers += 1;
    total.bays += 10;
    total.registered += Number(board.registered) || 0;
    total.issued += Number(board.issued) || 0;
    total.stored += Number(board.stored) || 0;
    total.empty += Number(board.empty) || 0;
    total.unpaid += Number(board.unpaid) || 0;
    (board.purposes || []).forEach((item) => {
      if (!Object.prototype.hasOwnProperty.call(purposes, item.name)) return;
      const count = Number(item.count) || 0;
      purposes[item.name] += count;
      kindPurposes[item.name] += count;
    });
  });
  return { total, rifle, pistol, purposes, riflePurposes, pistolPurposes, names };
}

function purposeTitle(data, name) {
  if (name === "젤루텍") return (data && data.purpose_label) || "젤루텍";
  return name;
}

function paintStats() {
  const data = state.data;
  const box = $("statBox");
  if (!data) {
    box.innerHTML = "";
    return;
  }
  const locker = data.locker || {};
  const kind = locker.kind === "pistol" ? "pistol" : "rifle";
  const title = kind === "pistol" ? "권총보관함 현황" : "소총 보관함 현황";
  const summary = data.summary || {};
  const purposeMap = Object.fromEntries((data.purposes || []).map((item) => [item.name, Number(item.count) || 0]));
  const names = ["사격", "훈련", "정비", "근무", "기타", "젤루텍"];
  const countOf = (name) => purposeMap[name] || 0;
  const sum = names.reduce((n, name) => n + countOf(name), 0);
  const n = (value) => value;
  const pair = (a, b) => `<tr><th>${escapeAttr(purposeTitle(data, a))}</th><td>${a ? n(countOf(a)) : ""}</td><th>${escapeAttr(purposeTitle(data, b))}</th><td>${b ? n(countOf(b)) : ""}</td></tr>`;
  const towerOn = !!(data.patlite && data.patlite.connected);
  const rfidOn = !!(data.rfid && data.rfid.connected);
  // 연결된 지문인식기(보관함)만 아이콘 생성 — 연결되면 자동으로 늘어남
  const linked = (data.boards || []).filter((board) => board.online && board.sensor);
  const scanners = linked.map((board, idx) => `
    <button type="button" class="scanner on" data-scanner-id="${escapeAttr(board.id)}" title="${escapeAttr(board.name)}">${scannerIcon()}<span>${escapeAttr(board.name || ("스캐너 #" + (idx + 1)))}</span></button>`).join("");
  const tower = `
    <button type="button" class="scanner ${towerOn ? "on" : ""}" data-tower="1">${towerIcon()}<span>경광등</span></button>`;
  const rfidBtn = `
    <button type="button" class="scanner ${rfidOn ? "on" : ""}" data-rfid="1">${rfidIcon()}<span>RFID</span></button>`;
  const insideOn = !!data.armory_inside;
  box.innerHTML = `
    <table class="box"><caption>${escapeAttr(title)}</caption>
        <tr><th>보관</th><td>${n(summary.stored || 0)}</td><th>불출</th><td>${n(summary.issued || 0)}</td></tr>
        <tr><th>미지급</th><td>${n(summary.empty || 0)}</td><th>미등록</th><td>${n(summary.empty || 0)}</td></tr>
        <tr><th>등록</th><td colspan="3">${n(summary.registered || 0)}</td></tr>
      </table>
      <table class="box"><caption>불 출 현 황</caption>
        ${pair(names[0], names[1])}
        ${pair(names[2], names[3])}
        ${pair(names[4], names[5])}
        <tr><th>계</th><td colspan="3">${n(sum)}</td></tr>
      </table>
      <label class="armory-inside${insideOn ? " on" : ""}">
        <input type="checkbox" id="armoryInside" ${insideOn ? "checked" : ""} />
        <span>무기고에 출입중입니다</span>
      </label>
      <div class="scanners">${scanners}${tower}${rfidBtn}</div>
`;
  box.querySelectorAll("[data-scanner-id]").forEach((btn) => {
    btn.onclick = () => {
      const id = btn.dataset.scannerId;
      const board = (data.boards || []).find((item) => item.id === id);
      toast(board ? `${board.name} 지문인식기가 연결되어 있습니다.` : "지문인식기가 연결되어 있습니다.");
      if (id) selectLocker(id);
    };
  });
  const towerBtn = box.querySelector("[data-tower]");
  if (towerBtn) {
    towerBtn.onclick = () => openPatlite();
  }
  const rfidEl = box.querySelector("[data-rfid]");
  if (rfidEl) {
    rfidEl.onclick = () => openRfid();
  }
  const inside = $("armoryInside");
  if (inside) {
    inside.onchange = async () => {
      const active = !!inside.checked;
      const result = await post("/api/armory-inside", { armory_inside: active });
      if (!result.ok) {
        inside.checked = !active;
        toast((result.data && result.data.error) || "출입중 설정을 저장하지 못했습니다.");
        return;
      }
      if (state.data) state.data.armory_inside = active;
      // 서버가 person 알림을 지워도 클라이언트 큐/노란테두리가 남을 수 있음 → 즉시 정리.
      if (active) dropPersonWarnsFromQueue();
      toast(
        active
          ? "출입중 — 사람감지·황색 경광등 알림 끄기"
          : "출입중 해제 — 사람감지·황색 경광등 알림 켜기"
      );
      const wrap = inside.closest(".armory-inside");
      if (wrap) wrap.classList.toggle("on", active);
    };
  }
}

function patliteOptionHtml(choices, selected) {
  const rows = Array.isArray(choices) ? choices : [];
  return rows.map((item) => {
    const id = String(item.id);
    const on = String(selected) === id ? " selected" : "";
    return `<option value="${escapeAttr(id)}"${on}>${escapeAttr(item.label || id)}</option>`;
  }).join("");
}

function patliteLimitOptions(selected) {
  const cur = Number(selected);
  const rows = [`<option value="0"${cur === 0 ? " selected" : ""}>연속 (0)</option>`];
  for (let n = 1; n <= 15; n += 1) {
    rows.push(`<option value="${n}"${cur === n ? " selected" : ""}>${n}회</option>`);
  }
  return rows.join("");
}

function patliteDefaultProfile(color) {
  if (color === "green") return { pattern: 3, limit: 2, pitch_a: 14, pitch_b: 15, led: 3 };
  if (color === "yellow") return { pattern: 4, limit: 2, pitch_a: 14, pitch_b: 15, led: 3 };
  return { pattern: 2, limit: 0, pitch_a: 14, pitch_b: 15, led: 3 };
}

function patliteReadProfile(prefix) {
  return {
    pattern: Number($(prefix + "Pattern") && $(prefix + "Pattern").value) || 0,
    limit: Number($(prefix + "Limit") && $(prefix + "Limit").value) || 0,
    pitch_a: Number($(prefix + "PitchA") && $(prefix + "PitchA").value) || 0,
    pitch_b: Number($(prefix + "PitchB") && $(prefix + "PitchB").value) || 0,
    led: Number($(prefix + "Led") && $(prefix + "Led").value) || 3,
  };
}

function patliteColorCard(color, title) {
  const cls = color;
  return `
    <div class="patlite-card" data-color="${escapeAttr(color)}">
      <div class="patlite-card-head"><span class="swatch ${cls}"></span>${escapeAttr(title)}</div>
      <div class="patlite-enable" role="group" aria-label="${escapeAttr(title)} 알림">
        <label class="check"><input type="radio" name="pl_${color}_enable" id="pl_${color}_On" value="1" checked /> 알림켜기</label>
        <label class="check"><input type="radio" name="pl_${color}_enable" id="pl_${color}_Off" value="0" /> 알림끄기</label>
      </div>
      <label>램프 점멸<select id="pl_${color}_Led"></select></label>
      <label>부저 패턴<select id="pl_${color}_Pattern"></select></label>
      <label>울림 횟수<select id="pl_${color}_Limit"></select></label>
      <label>음높이 A<select id="pl_${color}_PitchA"></select></label>
      <label>음높이 B<select id="pl_${color}_PitchB"></select></label>
      <button type="button" class="mini" data-color-preview="${escapeAttr(color)}">이 설정으로 듣기</button>
    </div>`;
}

async function openPatlite() {
  openModal(`
    <div class="win-caption">
      <h3>경광등</h3>
      <div class="win-chrome">
        <button type="button" class="cap cap-min" id="patliteMin" aria-label="최소화"></button>
        <button type="button" class="cap cap-max" id="patliteMax" aria-label="최대화"></button>
        <button type="button" class="cap cap-close" id="patliteClose" aria-label="닫기"></button>
      </div>
    </div>
    <div class="patlite-body">
      <div class="patlite-conn">
        <div class="patlite-conn-main">
          <div class="state bad" id="patliteState">확인 중…</div>
          <div class="meta" id="patliteMeta">USB가 꽂혀 있으면 자동 연결됩니다. (COM 포트 아님 · USB-HID)</div>
          <label class="patlite-device-pick">장치 (자동 선택 / 변경 가능)
            <select id="patliteDevice"></select>
          </label>
        </div>
        <div class="patlite-conn-actions">
          <button type="button" class="gb" id="patliteRefresh">다시 검색</button>
          <button type="button" class="gb" id="patliteConnect">이 장치로 고정</button>
        </div>
      </div>
      <div class="patlite-note" id="patliteVolumeNote">음량: 본체 High/Low 스위치로만 조절 (소프트웨어 제어 불가)</div>
      <div class="patlite-section">
        <h4>색상별 설정 (적=알람 / 녹=지문스캔 / 황=사람인지)</h4>
        <div class="patlite-cards">
          ${patliteColorCard("red", "적색")}
          ${patliteColorCard("yellow", "황색")}
          ${patliteColorCard("green", "녹색")}
        </div>
      </div>
      <div class="patlite-section">
        <h4>자유 테스트 (저장과 별도)</h4>
        <div class="patlite-lab">
          <label>패턴<select id="pl_lab_Pattern"></select></label>
          <label>횟수<select id="pl_lab_Limit"></select></label>
          <label>음높이 A<select id="pl_lab_PitchA"></select></label>
          <label>음높이 B<select id="pl_lab_PitchB"></select></label>
          <button type="button" class="gb" id="patliteLabPlay">테스트 재생</button>
        </div>
      </div>
      <div class="patlite-actions">
        <button type="button" class="gb" id="patliteStop">소등/정지</button>
        <button type="button" class="gb primary" id="patliteSave">저장</button>
      </div>
    </div>
  `, "patlite");
  const modal = $("modal");
  const maxBtn = $("patliteMax");
  const syncMax = () => maxBtn && maxBtn.classList.toggle("is-restore", modal.classList.contains("patlite-max"));
  $("patliteClose").onclick = closeModal;
  $("patliteMin").onclick = () => {
    if (modal.classList.contains("patlite-min")) {
      modal.classList.remove("patlite-min");
      return;
    }
    modal.classList.remove("patlite-max");
    modal.classList.add("patlite-min");
    syncMax();
  };
  maxBtn.onclick = () => {
    modal.classList.remove("patlite-min");
    modal.classList.toggle("patlite-max");
    syncMax();
  };
  $("patliteRefresh").onclick = () => refreshPatlitePanel(true);
  $("patliteConnect").onclick = () => connectPatliteDevice();
  $("patliteStop").onclick = () => patlitePreview("off");
  $("patliteSave").onclick = () => savePatliteConfig();
  $("patliteLabPlay").onclick = () => patlitePreview("custom", null, patliteReadProfile("pl_lab_"));
  document.querySelectorAll("[data-color-preview]").forEach((btn) => {
    btn.onclick = () => {
      const color = btn.getAttribute("data-color-preview");
      patlitePreview(color, null, patliteReadProfile(`pl_${color}_`));
    };
  });
  // 알림켜기/끄기는 선택 즉시 저장 (새로고침 시 켜기로 되돌아가는 문제 방지)
  ["red", "yellow", "green"].forEach((color) => {
    const onEl = $(`pl_${color}_On`);
    const offEl = $(`pl_${color}_Off`);
    const persist = () => { savePatliteConfig(true); };
    if (onEl) onEl.onchange = persist;
    if (offEl) offEl.onchange = persist;
  });
  await refreshPatlitePanel(true);
}

function patliteFillSelects(info) {
  const patterns = info.pattern_choices || [
    { id: 0, label: "끄기" }, { id: 1, label: "연속음" }, { id: 2, label: "패턴1" },
    { id: 3, label: "패턴2" }, { id: 4, label: "패턴3" }, { id: 5, label: "패턴4" },
  ];
  const leds = info.led_choices || [
    { id: 1, label: "점등 (고정)" }, { id: 2, label: "점멸1 (빠름)" },
    { id: 3, label: "점멸2" }, { id: 4, label: "점멸3" }, { id: 5, label: "점멸4" },
  ];
  const pitches = info.pitch_choices || [
    { id: 14, label: "기본(음A)" }, { id: 15, label: "기본(음B)" },
  ];
  const profiles = info.profiles || info.sounds || {};
  const enables = info.enables || {};
  ["red", "yellow", "green"].forEach((color) => {
    const profile = profiles[color] || patliteDefaultProfile(color);
    const led = $(`pl_${color}_Led`);
    const p = $(`pl_${color}_Pattern`);
    const l = $(`pl_${color}_Limit`);
    const a = $(`pl_${color}_PitchA`);
    const b = $(`pl_${color}_PitchB`);
    if (led) led.innerHTML = patliteOptionHtml(leds, profile.led != null ? profile.led : 3);
    if (p) p.innerHTML = patliteOptionHtml(patterns, profile.pattern);
    if (l) l.innerHTML = patliteLimitOptions(profile.limit);
    if (a) a.innerHTML = patliteOptionHtml(pitches, profile.pitch_a);
    if (b) b.innerHTML = patliteOptionHtml(pitches, profile.pitch_b);
    const on = enables[color] !== false;
    const onEl = $(`pl_${color}_On`);
    const offEl = $(`pl_${color}_Off`);
    if (onEl) onEl.checked = on;
    if (offEl) offEl.checked = !on;
  });
  const lab = profiles.red || patliteDefaultProfile("red");
  const lp = $("pl_lab_Pattern");
  const ll = $("pl_lab_Limit");
  const la = $("pl_lab_PitchA");
  const lb = $("pl_lab_PitchB");
  if (lp && !lp.dataset.ready) {
    lp.dataset.ready = "1";
    lp.innerHTML = patliteOptionHtml(patterns, lab.pattern);
    if (ll) ll.innerHTML = patliteLimitOptions(lab.limit);
    if (la) la.innerHTML = patliteOptionHtml(pitches, lab.pitch_a);
    if (lb) lb.innerHTML = patliteOptionHtml(pitches, lab.pitch_b);
  }
}

function patliteFillDevices(info) {
  const sel = $("patliteDevice");
  if (!sel) return;
  const devices = Array.isArray(info.devices) ? info.devices : [];
  const current = String(info.device_path || "");
  if (!devices.length) {
    sel.innerHTML = `<option value="">장치를 찾지 못함 — USB 연결 후 검색</option>`;
    return;
  }
  sel.innerHTML = devices.map((item) => {
    const path = String(item.path || "");
    const label = `${item.product || "Patlite"} (${item.vendor_id || ""}/${item.product_id || ""})${item.serial ? " · " + item.serial : ""}`;
    const on = path === current ? " selected" : "";
    return `<option value="${escapeAttr(path)}"${on}>${escapeAttr(label)}</option>`;
  }).join("");
}

async function refreshPatlitePanel(force) {
  const stateEl = $("patliteState");
  const metaEl = $("patliteMeta");
  let result;
  try {
    result = await Promise.race([
      api(`/api/patlite/status${force ? "?force=1" : ""}`),
      new Promise((_, reject) => setTimeout(() => reject(new Error("timeout")), 6000)),
    ]);
  } catch (err) {
    if (stateEl) {
      stateEl.textContent = "연결 확인 실패";
      stateEl.className = "state bad";
    }
    if (metaEl) {
      metaEl.textContent = "USB-HID 응답 없음 — USB 재연결 후 [다시 검색]을 눌러 주세요.";
    }
    patliteFillDevices({ devices: [] });
    return;
  }
  const info = result.data || {};
  const connected = !!(result.ok && info.connected);
  if (stateEl) {
    stateEl.textContent = connected ? "연결됨" : "연결 안 됨";
    stateEl.className = "state " + (connected ? "ok" : "bad");
  }
  if (metaEl) {
    const model = info.model || "LR6-USB (RYG)";
    const bus = info.bus || "USB-HID";
    const err = info.error ? ` · ${info.error}` : "";
    const count = Array.isArray(info.devices) ? info.devices.length : 0;
    metaEl.textContent = connected
      ? `${model} · ${bus} · ${info.vendor_id || "0x191A"} / ${info.product_id || "0x8003"} · ${count}대`
      : `${model} · ${bus} 검색 결과 ${count}대${err}`;
  }
  const note = $("patliteVolumeNote");
  if (note && info.volume && info.volume.note) note.textContent = info.volume.note;
  patliteFillDevices(info);
  if (state.data) {
    state.data.patlite = {
      connected,
      mode: info.mode || "off",
      model: info.model || "LR6-USB",
    };
    refresh.seen = "";
    paintStats();
  }
  patliteFillSelects(info);
}

async function connectPatliteDevice() {
  const sel = $("patliteDevice");
  const path = sel ? String(sel.value || "") : "";
  if (!path) {
    toast("연결할 USB 장치를 선택하세요.");
    return;
  }
  const result = await post("/api/patlite/devices", { action: "select", path });
  if (!result.ok) {
    toast((result.data && result.data.error) || "장치에 연결하지 못했습니다.");
  } else {
    toast(result.data && result.data.connected ? "경광등 USB 연결됨" : "장치를 선택했습니다.");
  }
  await refreshPatlitePanel(true);
}

async function patlitePreview(mode, soundId, profile) {
  const body = { mode };
  if (soundId) body.sound_id = soundId;
  if (profile) body.profile = profile;
  const result = await post("/api/patlite/test", body);
  if (!result.ok) {
    toast((result.data && result.data.error) || "경광등을 제어하지 못했습니다.");
    await refreshPatlitePanel(true);
    return;
  }
  if (mode === "off" || mode === "reset") toast("경광등을 소등했습니다.");
}

function patliteReadEnable(color) {
  const onEl = $(`pl_${color}_On`);
  if (onEl) return !!onEl.checked;
  return true;
}

async function openRfid() {
  openModal(`
    <div class="win-caption">
      <h3>RFID</h3>
      <div class="win-chrome">
        <button type="button" class="cap cap-min" id="rfidMin" aria-label="최소화"></button>
        <button type="button" class="cap cap-max" id="rfidMax" aria-label="최대화"></button>
        <button type="button" class="cap cap-close" id="rfidClose" aria-label="닫기"></button>
      </div>
    </div>
    <div class="patlite-body rfid-body">
      <div class="patlite-conn">
        <div class="patlite-conn-main">
          <div class="state bad" id="rfidState">확인 중…</div>
          <div class="meta" id="rfidMeta">RL17W · SYSTEMBASE RS232(COM) · ISO15693 · 8E1</div>
          <label class="patlite-device-pick">통신 방식
            <select id="rfidCommType"></select>
          </label>
          <label class="patlite-device-pick" id="rfidComWrap">COM 포트 (자동 권장)
            <select id="rfidComName"></select>
          </label>
          <label class="patlite-device-pick" id="rfidUsbWrap" hidden>USB 장치
            <select id="rfidUsbSerial"></select>
          </label>
          <div class="rfid-conn-grid">
            <label class="patlite-device-pick">보드레이트
              <select id="rfidBaud"></select>
            </label>
            <label class="patlite-device-pick">프레임
              <select id="rfidFrame"></select>
            </label>
            <label class="patlite-device-pick">버스주소
              <input id="rfidBusAddr" type="text" maxlength="3" value="255" />
            </label>
            <label class="patlite-device-pick">드라이버(RDType)
              <input id="rfidRdType" type="text" maxlength="32" value="RL8000" />
            </label>
          </div>
        </div>
        <div class="patlite-conn-actions">
          <button type="button" class="gb" id="rfidRefresh">다시 검색</button>
          <button type="button" class="gb primary" id="rfidConnect">연결</button>
          <button type="button" class="gb" id="rfidDisconnect">연결 해제</button>
        </div>
      </div>
      <div class="patlite-note" id="rfidHwNote">하드웨어: SYSTEMBASE USB-RS232 · 점퍼 232 · DB9 2/3/5 · COM번호는 PC마다 자동 탐색 · 38400/8E1/BusAddr 255</div>
      <div class="patlite-section">
        <h4>사용자기준 설정 (이중교차)</h4>
        <div class="rfid-options">
          <label class="check"><input type="checkbox" id="rfidCrossCheck" checked /> 지문 불출 ↔ RFID 이중교차 (불출 없이 태깅·칸 불일치 감지)</label>
          <label class="check"><input type="checkbox" id="rfidMismatchAlarm" checked /> 이상 태깅 시 경광등(적색) 알람</label>
          <label class="check"><input type="checkbox" id="rfidBeepOnRead" checked /> 태그 인식 시 리더 부저</label>
          <label class="check"><input type="checkbox" id="rfidAutoConnect" checked /> 프로그램 시작 시 자동 연결 시도</label>
        </div>
      </div>
      <div class="patlite-section">
        <h4>태깅 대기 · 수동 테스트</h4>
        <div class="patlite-lab">
          <div class="meta">연결되면 자동으로 태깅 대기합니다. 스티커만 대면 로그·이중교차가 동작합니다.</div>
          <button type="button" class="gb" id="rfidInventory">지금 읽기 (수동)</button>
          <div class="meta" id="rfidTags">아직 읽은 태그 없음</div>
        </div>
      </div>
      <div class="patlite-actions">
        <button type="button" class="gb primary" id="rfidSave">저장</button>
      </div>
    </div>
  `, "patlite rfid");
  const modal = $("modal");
  const maxBtn = $("rfidMax");
  const syncMax = () => maxBtn && maxBtn.classList.toggle("is-restore", modal.classList.contains("patlite-max"));
  $("rfidClose").onclick = closeModal;
  $("rfidMin").onclick = () => {
    if (modal.classList.contains("patlite-min")) {
      modal.classList.remove("patlite-min");
      return;
    }
    modal.classList.remove("patlite-max");
    modal.classList.add("patlite-min");
    syncMax();
  };
  maxBtn.onclick = () => {
    modal.classList.remove("patlite-min");
    modal.classList.toggle("patlite-max");
    syncMax();
  };
  $("rfidRefresh").onclick = () => refreshRfidPanel(true);
  $("rfidConnect").onclick = () => connectRfid(true);
  $("rfidDisconnect").onclick = () => connectRfid(false);
  $("rfidSave").onclick = () => saveRfidConfig();
  $("rfidInventory").onclick = () => rfidInventory();
  const comm = $("rfidCommType");
  if (comm) {
    comm.onchange = () => {
      const usb = comm.value === "USB";
      const comWrap = $("rfidComWrap");
      const usbWrap = $("rfidUsbWrap");
      if (comWrap) comWrap.hidden = usb;
      if (usbWrap) usbWrap.hidden = !usb;
    };
  }
  await refreshRfidPanel(true);
}

function rfidReadForm() {
  return {
    comm_type: ($("rfidCommType") && $("rfidCommType").value) || "COM",
    com_name: ($("rfidComName") && $("rfidComName").value) || "",
    usb_serial: ($("rfidUsbSerial") && $("rfidUsbSerial").value) || "",
    baud_rate: ($("rfidBaud") && $("rfidBaud").value) || "38400",
    frame: ($("rfidFrame") && $("rfidFrame").value) || "8E1",
    bus_addr: ($("rfidBusAddr") && $("rfidBusAddr").value) || "255",
    rd_type: ($("rfidRdType") && $("rfidRdType").value) || "RL8000",
    cross_check: !!($("rfidCrossCheck") && $("rfidCrossCheck").checked),
    mismatch_alarm: !!($("rfidMismatchAlarm") && $("rfidMismatchAlarm").checked),
    beep_on_read: !!($("rfidBeepOnRead") && $("rfidBeepOnRead").checked),
    auto_connect: !!($("rfidAutoConnect") && $("rfidAutoConnect").checked),
  };
}

function rfidFillPanel(info) {
  const cfg = info.config || {};
  const baudChoices = info.baud_choices || [{ id: "38400", label: "38400" }];
  const frameChoices = info.frame_choices || [
    { id: "8E1", label: "8E1 (기본)" },
    { id: "8N1", label: "8N1" },
  ];
  const commChoices = info.comm_choices || [{ id: "COM", label: "COM" }, { id: "USB", label: "USB" }];
  const devices = info.devices || {};
  const comPorts = Array.isArray(devices.com) ? devices.com : [];
  const usbDevs = Array.isArray(devices.usb) ? devices.usb : [];
  // RL17W R-Tool 기준 기본은 8E1 (이전 잘못된 8N1 저장값도 보정)
  const frameSelected = (cfg.frame === "8N1" || !cfg.frame) ? "8E1" : cfg.frame;

  const fill = (el, rows, selected) => {
    if (!el) return;
    el.innerHTML = rows.map((item) => {
      const id = String(item.id);
      const on = String(selected) === id ? " selected" : "";
      return `<option value="${escapeAttr(id)}"${on}>${escapeAttr(item.label || id)}</option>`;
    }).join("");
  };
  fill($("rfidCommType"), commChoices, cfg.comm_type || "COM");
  fill($("rfidBaud"), baudChoices, cfg.baud_rate || "38400");
  fill($("rfidFrame"), frameChoices, frameSelected);
  if (!comPorts.length) {
    if ($("rfidComName")) {
      $("rfidComName").innerHTML =
        `<option value="">자동 (리더 연결 시 탐색)</option>` +
        `<option value="" disabled>COM 포트 없음 — USB-RS232를 꽂으세요</option>`;
    }
  } else {
    const comRows = [
      { id: "", label: "자동 (이 PC에서 리더 찾기)" },
      ...comPorts.map((p) => ({
        id: p.id,
        label: p.path ? `${p.id} · ${p.path}` : (p.label || p.id),
      })),
    ];
    // Keep saved COM selected only when it still exists on this PC.
    const savedCom = String(cfg.com_name || "");
    const stillThere = comPorts.some((p) => String(p.id) === savedCom);
    fill($("rfidComName"), comRows, stillThere ? savedCom : "");
  }
  if (!usbDevs.length) {
    if ($("rfidUsbSerial")) $("rfidUsbSerial").innerHTML = `<option value="">USB 장치 없음</option>`;
  } else {
    fill($("rfidUsbSerial"), usbDevs.map((d) => ({ id: d.serial || d.id, label: d.label || d.id })), cfg.usb_serial || "");
  }
  if ($("rfidBusAddr")) $("rfidBusAddr").value = cfg.bus_addr || "255";
  if ($("rfidRdType")) $("rfidRdType").value = cfg.rd_type || "RL8000";
  if ($("rfidCrossCheck")) $("rfidCrossCheck").checked = cfg.cross_check !== false;
  if ($("rfidMismatchAlarm")) $("rfidMismatchAlarm").checked = cfg.mismatch_alarm !== false;
  if ($("rfidBeepOnRead")) $("rfidBeepOnRead").checked = cfg.beep_on_read !== false;
  if ($("rfidAutoConnect")) $("rfidAutoConnect").checked = cfg.auto_connect !== false;
  const usb = String(cfg.comm_type || "COM").toUpperCase() === "USB";
  if ($("rfidComWrap")) $("rfidComWrap").hidden = usb;
  if ($("rfidUsbWrap")) $("rfidUsbWrap").hidden = !usb;
  const tags = Array.isArray(info.last_tags) ? info.last_tags : [];
  const tagsEl = $("rfidTags");
  if (tagsEl) {
    tagsEl.textContent = tags.length
      ? tags.map((t) => t.uid).join(", ")
      : "아직 읽은 태그 없음";
  }
  if ($("rfidHwNote") && info.note) $("rfidHwNote").textContent = info.note;
}

async function refreshRfidPanel(force) {
  const stateEl = $("rfidState");
  const metaEl = $("rfidMeta");
  let result;
  try {
    result = await Promise.race([
      api(`/api/rfid/status${force ? "?force=1" : ""}`),
      new Promise((_, reject) => setTimeout(() => reject(new Error("timeout")), 6000)),
    ]);
  } catch (err) {
    if (stateEl) {
      stateEl.textContent = "연결 확인 실패";
      stateEl.className = "state bad";
    }
    if (metaEl) metaEl.textContent = "RFID 응답 없음 — COM/USB 재연결 후 [다시 검색]";
    return;
  }
  const info = result.data || {};
  const connected = !!(result.ok && info.connected);
  if (stateEl) {
    stateEl.textContent = connected ? "연결됨" : "연결 안 됨";
    stateEl.className = "state " + (connected ? "ok" : "bad");
  }
  if (metaEl) {
    const model = info.model || "RL17W / RL1700 HF";
    const bus = info.bus || "COM";
    const err = info.error ? ` · ${info.error}` : "";
    const sdk = info.sdk_loaded ? "SDK OK" : "SDK 미로드";
    const watching = !!(info.watching);
    metaEl.textContent = connected
      ? `${model} · ${bus} · ${sdk}${watching ? " · 태깅 대기중" : ""}`
      : `${model} · ${bus} · ${sdk}${err}`;
  }
  rfidFillPanel(info);
  if (state.data) {
    state.data.rfid = {
      connected,
      model: info.model || "RL17W",
      cross_check: !!(info.config && info.config.cross_check !== false),
    };
    refresh.seen = "";
    paintStats();
  }
}

async function connectRfid(on) {
  const body = rfidReadForm();
  body.action = on ? "connect" : "disconnect";
  const result = await post("/api/rfid/connect", body);
  if (!result.ok) {
    toast((result.data && result.data.error) || (on ? "RFID 연결 실패" : "RFID 해제 실패"));
  } else {
    toast(on ? "RFID 연결됨" : "RFID 연결 해제");
  }
  await refreshRfidPanel(true);
}

async function saveRfidConfig() {
  const result = await post("/api/rfid/config", rfidReadForm());
  if (!result.ok) {
    toast((result.data && result.data.error) || "RFID 설정을 저장하지 못했습니다.");
    return;
  }
  toast("RFID 설정을 저장했습니다.");
  await refreshRfidPanel(true);
}

async function rfidInventory() {
  const result = await post("/api/rfid/inventory", {});
  if (!result.ok) {
    toast((result.data && result.data.error) || "태그 읽기 실패");
  } else {
    const tags = (result.data && result.data.tags) || [];
    toast(tags.length ? `태그 ${tags.length}개 인식` : "태그 없음");
  }
  await refreshRfidPanel(false);
}

async function savePatliteConfig(quiet) {
  const body = {
    profiles: {
      red: patliteReadProfile("pl_red_"),
      yellow: patliteReadProfile("pl_yellow_"),
      green: patliteReadProfile("pl_green_"),
    },
    enables: {
      red: patliteReadEnable("red"),
      yellow: patliteReadEnable("yellow"),
      green: patliteReadEnable("green"),
    },
    device_path: ($("patliteDevice") && $("patliteDevice").value) || "",
  };
  const result = await post("/api/patlite/config", body);
  if (!result.ok) {
    toast((result.data && result.data.error) || "저장하지 못했습니다.");
    return;
  }
  if (!quiet) toast("경광등 설정을 저장했습니다.");
  else {
    const off = ["red", "yellow", "green"].filter((c) => !body.enables[c]).map((c) => ({ red: "적색", yellow: "황색", green: "녹색" }[c]));
    if (off.length) toast(`${off.join("/")} 알림끄기 저장`);
    else toast("경광등 알림켜기 저장");
  }
  // 황색 알림켜기/끄기는 출입중 상태와 같이 맞춘다.
  if (state.data && Object.prototype.hasOwnProperty.call(body.enables, "yellow")) {
    state.data.armory_inside = !body.enables.yellow;
    refresh.seen = "";
  }
  await refreshPatlitePanel(true);
}

function slotLabel(slot) {
  const person = slot.person || {};
  let rank = String(person.rank || "").trim();
  let name = String(person.name || "").trim();
  if (!rank && !name) {
    const raw = String(slot.live_name || "").trim();
    const cut = raw.indexOf(" ");
    if (cut > 0 && cut <= 4) {
      rank = raw.slice(0, cut);
      name = raw.slice(cut + 1).trim();
    } else {
      name = raw;
    }
  }
  if (slot.enrolled && (rank || name)) {
    return `<small>${escapeAttr(rank)}</small><span>${escapeAttr(name)}</span>`;
  }
  return `<span>사용자${slot.slot}</span>`;
}

function noteGunColorFade(lockerId, slotNo, stored, doorOpen) {
  const key = `${lockerId || ""}:${Number(slotNo)}`;
  const nowStored = !!stored;
  const nowDoor = !!doorOpen;
  if (!Object.prototype.hasOwnProperty.call(slotStoredPrev, key)) {
    slotStoredPrev[key] = nowStored;
    slotDoorPrev[key] = nowDoor;
    return;
  }
  const wasStored = slotStoredPrev[key];
  slotStoredPrev[key] = nowStored;
  slotDoorPrev[key] = nowDoor;
  // 비상개폐 팝업은 서버(_detect_emergency_sens)만 사용 — 여기서 중복/오탐 방지
}

function paintGrid() {
  const data = state.data;
  const panel = $("grid");
  if (!data) {
    panel.innerHTML = "";
    return;
  }
  const lockerId = data.locker && data.locker.id != null ? String(data.locker.id) : "";
  const kind = data.locker && data.locker.kind === "pistol" ? "pistol" : "rifle";
  if (kind === "pistol") {
    panel.innerHTML = paintPistolGrid(data, lockerId);
  } else {
    const names = [];
    const rifles = [];
    (data.slots || []).forEach((slot) => {
      const stored = !!(slot.enrolled && slot.present);
      noteGunColorFade(lockerId, slot.slot, stored, slot.door_open);
      const img = slot.enrolled
        ? `<img class="pi-rifle ${stored ? "is-in" : "is-out"}" src="/assets/rifle.png" alt="" />`
        : "";
      names.push(`<div class="pi-name">${slotLabel(slot)}</div>`);
      rifles.push(`<div class="pi-cell">${img}</div>`);
    });
    panel.innerHTML = `
      <div class="pi-lockers">
        <div class="pi-names">${names.join("")}</div>
        <div class="pi-rifles">${rifles.join("")}</div>
      </div>
      <div class="sheet"></div>`;
  }
  panel.onclick = () => openFullScreen();
}

function paintPistolGrid(data, lockerId) {
  const bySlot = {};
  (data.slots || []).forEach((slot) => { bySlot[slot.slot] = slot; });
  // display number 0-9 maps to user slots 1-10
  const layout = [
    [0, "monitor", 6],
    [1, "fingerprint", 7],
    [2, 4, 8],
    [3, 5, 9],
  ];
  const rows = layout.map((row) => {
    const names = [];
    const guns = [];
    row.forEach((cell) => {
      if (cell === "monitor") {
        names.push(`<div class="pi-name"><span>모니터</span></div>`);
        guns.push(`<div class="pi-cell"><img class="pi-bay-icon" src="/assets/monitor.png" alt="모니터" /></div>`);
        return;
      }
      if (cell === "fingerprint") {
        names.push(`<div class="pi-name"><span>지문</span></div>`);
        guns.push(`<div class="pi-cell"><img class="pi-bay-icon pi-bay-icon--fp" src="/assets/fingerprint.png" alt="지문인식기" /></div>`);
        return;
      }
      const slotNo = Number(cell) + 1;
      const slot = bySlot[slotNo] || { slot: slotNo, enrolled: false };
      const stored = !!(slot.enrolled && slot.present);
      noteGunColorFade(lockerId, slotNo, stored, slot.door_open);
      const gun = slot.enrolled
        ? `<img class="pi-rifle ${stored ? "is-in" : "is-out"}" src="/assets/pistol.png" alt="" />`
        : "";
      names.push(`<div class="pi-name">${slotLabel(slot)}</div>`);
      guns.push(`<div class="pi-cell">${gun}</div>`);
    });
    return `
      <div class="pi-names pi-names--3">${names.join("")}</div>
      <div class="pi-rifles pi-rifles--3">${guns.join("")}</div>`;
  }).join("");
  return `
    <div class="pi-lockers pi-lockers--pistol">
      ${rows}
    </div>
    <div class="sheet"></div>`;
}


function visibleKey(data) {
  if (!data) return "";
  const slotKey = (data.slots || []).map((slot) => [
    slot.slot,
    slot.enrolled ? 1 : 0,
    slot.present ? 1 : 0,
    (slot.person && slot.person.rank) || "",
    (slot.person && slot.person.name) || "",
    slot.live_name || "",
  ].join(",")).join(";");
  const boardKey = (data.boards || []).map((board) => [
    board.id,
    board.name,
    board.online ? 1 : 0,
    board.linking ? 1 : 0,
    board.selected ? 1 : 0,
    board.kind || "",
    board.registered || 0,
    board.issued || 0,
    board.stored || 0,
    board.empty || 0,
    board.sensor ? 1 : 0,
    (board.purposes || []).map((item) => item.count).join("."),
  ].join(",")).join(";");
  const who = data.who ? `${data.who.role}:${data.who.slot}` : "";
  return [
    who,
    data.unit_name || "",
    data.purpose_label || "",
    data.armory_inside ? "1" : "0",
    data.memo || "",
    (data.locker && data.locker.kind) || "",
    (data.locker && data.locker.id) || "",
    (data.patlite && data.patlite.connected) ? "1" : "0",
    JSON.stringify(data.summary || {}),
    JSON.stringify(data.purposes || []),
    JSON.stringify(data.can || {}),
    slotKey,
    boardKey,
  ].join("|");
}

function paintFrame() {
  paintChrome();
  paintNet();
  paintStats();
  paintGrid();
  paintLogs(state.data && state.data.logs);
  const memo = $("memoText");
  if (memo && document.activeElement !== memo && state.data) memo.value = state.data.memo || "";
}

function nowStamp() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function pushLocalLog(level, text) {
  const message = String(text || "").trim();
  if (!message) return;
  const dedupe = `${level}|${message}`;
  if (localLogKeys.has(dedupe)) return;
  localLogKeys.add(dedupe);
  setTimeout(() => localLogKeys.delete(dedupe), 8000);
  localWarnSeq += 1;
  localLogs.push({
    // Prefix so local ids never collide with server log ids in sort/paint keys.
    id: `L${localWarnSeq}`,
    time: nowStamp(),
    level: level || "info",
    text: message.slice(0, 160),
  });
  if (localLogs.length > 40) localLogs = localLogs.slice(-40);
  // Show immediately — do not wait for the next /api/state poll.
  paintLogs(state.data && state.data.logs);
}

function pushLocalWarn(message, level) {
  const text = String(message || "").trim();
  if (!text) return;
  localWarnSeq += 1;
  // Negative ids: never collide with server-side alert ids (seenWarnIds).
  const id = -localWarnSeq;
  pushLocalLog(level || "error", text.replace(/\n+/g, " "));
  enqueueWarnAlerts([{ id, message: text }]);
}

function paintLogBox(box, rows, key) {
  if (!box) return;
  const prevKey = box.dataset.key || "";
  if (prevKey === key) return;
  // Newest is on top — stay pinned there unless the operator scrolled down.
  const nearTop = !prevKey || box.scrollTop < 40;
  const prevIds = new Set(prevKey.split("|").filter(Boolean));
  box.dataset.key = key;
  if (!rows.length) {
    box.innerHTML = `<div class="oms-log__empty">이벤트 대기 중…</div>`;
    return;
  }
  box.innerHTML = `<div class="oms-log__stack">${rows.map((row) => {
    const level = String((row && row.level) || "info");
    const time = String((row && row.time) || "").slice(11, 19) || "--:--:--";
    const text = escapeAttr((row && row.text) || "");
    const id = String(row && row.id != null ? row.id : "");
    const rise = id && !prevIds.has(id) ? " is-new" : "";
    return `<div class="oms-log__row${rise}" data-id="${escapeAttr(id)}"><span class="t">${escapeAttr(time)}</span><span class="lv ${escapeAttr(level)}">${escapeAttr(level)}</span><span class="msg">${text}</span></div>`;
  }).join("")}</div>`;
  if (nearTop) box.scrollTop = 0;
}

function paintLogs(logs) {
  const merged = []
    .concat(Array.isArray(logs) ? logs : [])
    .concat(localLogs)
    .sort((a, b) => {
      // Newest first (top of the panel).
      const ta = String((a && a.time) || "");
      const tb = String((b && b.time) || "");
      if (ta !== tb) return ta < tb ? 1 : -1;
      const ia = String((a && a.id) != null ? a.id : "");
      const ib = String((b && b.id) != null ? b.id : "");
      if (ia === ib) return 0;
      const na = Number(ia);
      const nb = Number(ib);
      if (Number.isFinite(na) && Number.isFinite(nb)) return nb - na;
      return ia < ib ? 1 : -1;
    });
  const rows = merged.slice(0, 120);
  const key = rows.map((row) => String(row && row.id != null ? row.id : "")).join("|");
  paintLogBox($("logLines"), rows, key);
  paintLogBox($("comboLogLines"), rows, key);
}

/** 좌측 초록→파란 / 총기 진함→연함 과 같은 순간에만 알람 (재연결 때는 단절 알람 금지) */
function watchLiveSecurity(_prev, next) {
  if (!next) return;
  (next.boards || []).forEach((board, index) => {
    if (!board || board.id == null) return;
    const id = String(board.id);
    const online = !!board.online;
    if (!Object.prototype.hasOwnProperty.call(boardOnlinePrev, id)) {
      boardOnlinePrev[id] = online;
      return;
    }
    const wasOnline = boardOnlinePrev[id];
    boardOnlinePrev[id] = online;
    if (!liveWatchReady) return;
    const no = Number(board.no) || (index + 1);
    const label = String(board.name || "보관함").trim() || "보관함";
    // 단절: 초록(online) → 파란(!online) 순간만
    if (wasOnline === true && online === false) {
      pushLocalWarn(`네트워크연결이 단절되었습니다.\n${label}`, "error");
    } else if (wasOnline === false && online === true) {
      // 재연결: 로그만, 단절 메시지 금지
      pushLocalLog("ok", `[${label}] 연결이 복구됐습니다.`);
    }
  });

  const lockerId = next.locker && next.locker.id != null ? String(next.locker.id) : "";
  (next.slots || []).forEach((slot) => {
    if (!slot || slot.slot == null) return;
    const key = `${lockerId}:${Number(slot.slot)}`;
    const present = !!slot.present;
    const doorOpen = !!slot.door_open;
    // 화면에서 총기가 진함→연함으로 바뀌는 조건과 동일
    const stored = !!(slot.enrolled && slot.present);
    if (!Object.prototype.hasOwnProperty.call(slotStoredPrev, key)) {
      slotPresentPrev[key] = present;
      slotDoorPrev[key] = doorOpen;
      slotStoredPrev[key] = stored;
      return;
    }
    const wasStored = slotStoredPrev[key];
    const wasPresent = slotPresentPrev[key];
    slotPresentPrev[key] = present;
    slotDoorPrev[key] = doorOpen;
    slotStoredPrev[key] = stored;
    if (!liveWatchReady) return;
    // 비상개폐는 서버 알림만 표시 (로컬 present 추적은 로그/UI 상태용)
  });
  liveWatchReady = true;
}

function openMenu(item) {
  if (item.id === "login") {
    toggleLogin();
    return;
  }
  if (!allowed(item)) {
    toast(state.data && state.data.who ? "이 계정으로는 이 화면을 열 수 없습니다." : "로그인한 뒤에 사용할 수 있습니다.");
    return;
  }
  if (item.id === "status") openFullScreen();
  else if (item.id === "combo") openComboStatus();
  else if (item.id === "settings") openLockerDialog(state.data.locker && state.data.locker.id);
  else if (item.id === "people") openGroups();
  else if (item.id === "users") openUsers();
  else if (item.id === "guns") {
    state.gunIndex = null;
    openGuns();
  }
  else if (item.id === "issue") openIssue();
  else if (item.id === "history") openHistory("all");
  else if (item.id === "cctv") openCctv();
  else if (item.id === "patlite") openPatlite();
  else if (item.id === "rfid") openRfid();
}

function stopCctvTimers() {
  if (cctvLiveTimer) {
    clearInterval(cctvLiveTimer);
    cctvLiveTimer = null;
  }
  if (cctvMetaTimer) {
    clearInterval(cctvMetaTimer);
    cctvMetaTimer = null;
  }
  if (cctvComboTimer) {
    clearInterval(cctvComboTimer);
    cctvComboTimer = null;
  }
  if (cctvSetPreviewTimer) {
    clearInterval(cctvSetPreviewTimer);
    cctvSetPreviewTimer = null;
  }
}

function openCctv() {
  state.popup = () => openCctv();
  stopCctvTimers();
  cctvViewMode = "live";
  const who = (state.data && state.data.user && state.data.user.name)
    || (state.data && state.data.who && state.data.who.role)
    || "operator";
  openModal(`
    <div class="win-head cctv-head">
      <div class="cctv-head-left">
        <h3 class="win-title">CCTV 관제</h3>
        <div class="cctv-menubar">
          <button type="button" class="cctv-menu-btn" id="cctvMenuSettings">설정</button>
        </div>
      </div>
      <div class="cctv-iconbar" id="cctvLayoutBar">
        <button type="button" class="lay-btn on" data-layout="1" title="1화면" aria-label="1화면"><span class="lay lay-1"></span></button>
        <button type="button" class="lay-btn lay-btn--full" data-layout="full" title="전체 화면" aria-label="전체 화면"><span class="lay lay-full">전체 화면</span></button>
        <button type="button" class="lay-btn" data-layout="2v" title="좌우 2분할. 한 번 더 누르면 좌우가 바뀝니다" aria-label="좌우 2분할"><span class="lay lay-2v"><i></i><i></i></span></button>
        <button type="button" class="lay-btn" data-layout="2h" title="상하 2분할. 한 번 더 누르면 상하가 바뀝니다" aria-label="상하 2분할"><span class="lay lay-2h"><i></i><i></i></span></button>
        <button type="button" class="lay-btn" data-layout="2x2" title="4분할" aria-label="4분할"><span class="lay lay-4"><i></i><i></i><i></i><i></i></span></button>
        <span class="cctv-ico-sep"></span>
        <button type="button" class="cctv-ico" id="cctvSnap" title="스냅샷">캡처</button>
        <button type="button" class="cctv-ico primary" id="cctvRec" title="이벤트 녹화">●</button>
        <button type="button" class="cctv-ico" id="cctvComboBtn" title="총기·CCTV 현황">총기·CCTV</button>
        <button type="button" class="cctv-ico" id="cctvClose" title="닫기">닫기</button>
      </div>
      <span class="cctv-clock" id="cctvClock"></span>
    </div>
    <div class="win-body cctv-body">
      <aside class="cctv-side">
        <div class="cctv-side-top">
          <div class="cctv-userline">ID: ${escapeAttr(String(who))} <span class="cctv-badge">Net</span></div>
          <div class="cctv-cam-stats" id="cctvCamStats">연결 확인 중…</div>
        </div>
        <div class="cctv-side-mid">
          <h4 class="cctv-panel-title">카메라 목록</h4>
          <ul class="cctv-cam-list" id="cctvCamList"></ul>
          <h4 class="cctv-panel-title" style="margin-top:12px;">레이아웃</h4>
          <ul class="cctv-layout-list">
            <li class="on" data-layout-pick="1">1화면</li>
            <li data-layout-pick="full">전체 화면</li>
            <li data-layout-pick="2v">좌우 2분할</li>
            <li data-layout-pick="2h">상하 2분할</li>
            <li data-layout-pick="2x2">4분할</li>
          </ul>
        </div>
        <div class="cctv-side-bot cctv-ptz">
          <h4>1CH 팬·틸트</h4>
          <div class="cctv-ptz-pad">
            <span></span>
            <button type="button" data-ptz="up" title="위">▲</button>
            <span></span>
            <button type="button" data-ptz="left" title="왼쪽">◀</button>
            <button type="button" class="ptz-c" data-ptz="stop" title="정지">■</button>
            <button type="button" data-ptz="right" title="오른쪽">▶</button>
            <span></span>
            <button type="button" data-ptz="down" title="아래">▼</button>
            <span></span>
          </div>
          <div class="cctv-ptz-zoom">
            <button type="button" data-ptz="zoomin">줌+</button>
            <button type="button" data-ptz="zoomout">줌−</button>
            <button type="button" class="ptz-home" data-ptz="home" title="원래 위치로 이동">Home</button>
            <button type="button" data-ptz="set_home" title="지금 화면을 Home으로 저장">Home저장</button>
          </div>
          <div class="cctv-ptz-msg" id="cctvPtzMsg">방향=이동 · Home저장=원위치 등록 · Home=복귀</div>
        </div>
      </aside>
      <section class="cctv-main">
        <div class="cctv-tabs">
          <button type="button" class="cctv-tab on" id="cctvTabLive">라이브</button>
          <button type="button" class="cctv-tab" id="cctvTabPlay">전체녹화</button>
          <button type="button" class="cctv-tab" id="cctvTabEvent">이벤트</button>
          <span class="cctv-live-badge off" id="cctvLiveBadge">LIVE</span>
          <span class="cctv-rec-badge" id="cctvRecBadge" hidden>REC</span>
          <span id="cctvTitle" class="cctv-title-right"></span>
        </div>
        <div class="cctv-grid cctv-grid--1x1" id="cctvGrid"></div>
        <div class="cctv-play" id="cctvPlayPane" hidden>
          <div class="cctv-play-list" id="cctvPlayList"><div class="cctv-empty">녹화 목록 없음</div></div>
          <div class="cctv-play-stage">
            <video id="cctvPlayVideo" controls playsinline></video>
            <div class="cctv-play-hint" id="cctvPlayHint">왼쪽 목록에서 녹화를 선택하세요.</div>
          </div>
        </div>
        <div class="cctv-play" id="cctvEventPane" hidden>
          <div class="cctv-play-list" id="cctvEventList"><div class="cctv-empty">이벤트 영상 없음</div></div>
          <div class="cctv-play-stage">
            <video id="cctvEventVideo" controls playsinline></video>
            <div class="cctv-play-hint" id="cctvEventHint">이벤트 영상을 선택하세요.</div>
          </div>
        </div>
        <div class="cctv-log-wrap">
          <h4>이벤트 로그</h4>
          <div class="cctv-log-head">
            <span>장치</span><span>위치</span><span>유형</span><span>시간</span><span>내용</span>
          </div>
          <div class="cctv-log" id="cctvEventLog"><div class="cctv-empty">이벤트 대기 중…</div></div>
        </div>
      </section>
      <aside class="cctv-right">
        <h4>이벤트 영상</h4>
        <div class="cctv-snaps" id="cctvSnaps"><div class="cctv-empty">스냅샷 없음</div></div>
      </aside>
    </div>
    <div class="cctv-settings" id="cctvSettings" hidden></div>
  `, "cctv");
  const modal = $("modal");
  modal.className = "modal cctv-max";
  applyCctvLayout(cctvLayout);
  $("cctvClose").onclick = () => { stopCctvTimers(); closeModal(); };
  $("cctvMenuSettings").onclick = () => openCctvSettingsPanel();
  $("cctvComboBtn").onclick = () => openComboStatus();
  $("cctvTabLive").onclick = () => setCctvViewMode("live");
  $("cctvTabPlay").onclick = () => setCctvViewMode("play");
  $("cctvTabEvent").onclick = () => setCctvViewMode("event");
  document.querySelectorAll("#cctvLayoutBar [data-layout]").forEach((btn) => {
    btn.addEventListener("click", () => selectCctvLayout(btn.getAttribute("data-layout") || "1"));
  });
  const grid = $("cctvGrid");
  if (grid) {
    grid.addEventListener("dblclick", (ev) => {
      if (ev.target.closest("button, a, input, select, .cctv-split")) return;
      enterCctvFullscreen(false);
    });
  }
  const playStage = document.querySelector(".cctv-play-stage");
  if (playStage) {
    playStage.addEventListener("dblclick", () => enterStageFullscreen(playStage));
  }
  document.querySelectorAll("[data-layout-pick]").forEach((li) => {
    li.addEventListener("click", () => selectCctvLayout(li.getAttribute("data-layout-pick") || "1"));
  });
  $("cctvRec").onclick = async () => {
    const btn = $("cctvRec");
    if (btn && btn.classList.contains("is-on")) return;
    if (btn) {
      btn.classList.add("is-on");
      btn.title = "녹화 중";
    }
    try {
      const result = await post("/api/cctv/record", { reason: "manual", message: "수동 이벤트 녹화" });
      if (!result.ok) {
        if (btn) {
          btn.classList.remove("is-on");
          btn.title = "이벤트 녹화";
        }
        toast((result.data && result.data.error) || "녹화 실패");
      } else if (result.data && result.data.recording === false) {
        if (btn) {
          btn.classList.remove("is-on");
          btn.title = "이벤트 녹화";
        }
        toast("영상 파일은 저장되지 않았습니다. 스냅샷만 남았습니다.");
      } else {
        cctvRecWaitId = (result.data && result.data.event && result.data.event.id) || "";
        const sec = (result.data && result.data.seconds) || 30;
        toast(`${sec}초 녹화를 시작했습니다. 끝나면 재생합니다.`);
      }
      refreshCctvMeta();
    } catch (_) {
      if (btn) {
        btn.classList.remove("is-on");
        btn.title = "이벤트 녹화";
      }
      toast("녹화 요청 실패");
    }
  };
  $("cctvSnap").onclick = () => {
    const img = $("cctvLiveImg");
    if (!img || !img.src) { toast("표시 중인 영상이 없습니다."); return; }
    const a = document.createElement("a");
    a.href = img.src;
    a.download = `cctv_${Date.now()}.jpg`;
    a.click();
    toast("스냅샷을 저장했습니다.");
  };
  bindCctvPtzControls(document.querySelector(".cctv-side"));
  bootCctvAutoConnect();
  cctvMetaTimer = setInterval(refreshCctvMeta, 5000);
  tickCctvClock();
}

function cctvLayoutKey(layout) {
  const key = String(layout || "1");
  if (key === "1x1") return "1";
  if (key === "2x2" || key === "2v" || key === "2h" || key === "full" || key === "1") return key;
  return "1";
}

function cctvLayoutCellCount(layout) {
  const key = cctvLayoutKey(layout);
  if (key === "2x2") return 4;
  if (key === "2v" || key === "2h") return 2;
  return 1;
}

function selectCctvLayout(layout) {
  if (layout === "full") {
    enterCctvFullscreen(true);
    return;
  }
  const key = cctvLayoutKey(layout);
  const current = cctvLayoutKey(cctvLayout);
  if ((key === "2v" || key === "2h") && current === key) {
    cctvPaneSwapped = !cctvPaneSwapped;
    const grid = $("cctvGrid");
    if (grid) grid.classList.toggle("is-swapped", cctvPaneSwapped);
    return;
  }
  cctvPaneSwapped = false;
  applyCctvLayout(key);
}

function applyCctvLayout(layout) {
  const key = cctvLayoutKey(layout);
  cctvLayout = key;
  cctvActiveCell = 0;
  const grid = $("cctvGrid");
  if (!grid) return;
  const monitor = grid.classList.contains("is-monitor");
  const n = cctvLayoutCellCount(key);
  const swapped = (key === "2v" || key === "2h") && cctvPaneSwapped ? " is-swapped" : "";
  grid.className = `cctv-grid cctv-grid--${key}${monitor ? " is-monitor" : ""}${swapped}`;
  const cells = [];
  for (let i = 0; i < n; i += 1) {
    const ch = i + 1;
    const ready = ch === 1 || !!(cctvChannelHosts[i] && String(cctvChannelHosts[i]).trim());
    cells.push(`
      <div class="cctv-cell ${i === 0 ? "active" : ""}" data-cell="${i}">
        ${ready ? `<img class="cctv-live-img" ${i === 0 ? 'id="cctvLiveImg"' : ""} data-ch="${ch}" alt="${ch}CH" hidden />
        <div class="cctv-placeholder ${i === 0 ? "" : "soft"}" ${i === 0 ? 'id="cctvPlaceholder"' : ""}>${i === 0 ? "저장된 카메라로 자동 연결 중…" : `CAM-0${ch}<br/><small>연결 중…</small>`}</div>` : `<div class="cctv-placeholder soft">CAM-0${ch}<br/><small>카메라 미등록</small></div>`}
        <div class="cctv-cell-label">CAM-0${ch}</div>
      </div>`);
  }
  grid.innerHTML = cells.join("") + `
    <div class="cctv-split cctv-split--v" data-split="col" role="separator" aria-orientation="vertical" aria-label="좌우 크기" hidden></div>
    <div class="cctv-split cctv-split--h" data-split="row" role="separator" aria-orientation="horizontal" aria-label="상하 크기" hidden></div>`;
  bindPaneSplit(grid.querySelector('[data-split="col"]'), "col", (ratio) => {
    cctvColRatio = ratio;
    applyCctvSplits();
  });
  bindPaneSplit(grid.querySelector('[data-split="row"]'), "row", (ratio) => {
    cctvRowRatio = ratio;
    applyCctvSplits();
  });
  applyCctvSplits();
  document.querySelectorAll("#cctvLayoutBar [data-layout], [data-layout-pick]").forEach((el) => {
    const pick = el.getAttribute("data-layout") || el.getAttribute("data-layout-pick");
    el.classList.toggle("on", cctvLayoutKey(pick) === key);
  });
  if (cctvConnected && cctvViewMode === "live") startCctvLive();
}

function applyCctvSplits() {
  const grid = $("cctvGrid");
  if (!grid) return;
  const col = Math.min(0.85, Math.max(0.15, cctvColRatio));
  const row = Math.min(0.85, Math.max(0.15, cctvRowRatio));
  grid.style.setProperty("--split-col", `${col}fr`);
  grid.style.setProperty("--split-col-b", `${(1 - col)}fr`);
  grid.style.setProperty("--split-row", `${row}fr`);
  grid.style.setProperty("--split-row-b", `${(1 - row)}fr`);
  grid.style.setProperty("--split-col-pct", `${col * 100}%`);
  grid.style.setProperty("--split-row-pct", `${row * 100}%`);
  const key = cctvLayoutKey(cctvLayout);
  const v = grid.querySelector(".cctv-split--v");
  const h = grid.querySelector(".cctv-split--h");
  if (v) v.hidden = !(key === "2v" || key === "2x2");
  if (h) h.hidden = !(key === "2h" || key === "2x2");
}

function bindPaneSplit(handle, axis, onRatio) {
  if (!handle || handle.dataset.bound === "1") return;
  handle.dataset.bound = "1";
  if (axis) handle.dataset.axis = axis;
  handle.addEventListener("pointerdown", (ev) => {
    if (ev.button != null && ev.button !== 0) return;
    ev.preventDefault();
    ev.stopPropagation();
    const parent = handle.parentElement;
    if (!parent) return;
    const rect = parent.getBoundingClientRect();
    const useAxis = handle.dataset.axis || axis;
    handle.setPointerCapture(ev.pointerId);
    const move = (e) => {
      const span = useAxis === "col" ? rect.width : rect.height;
      const pos = useAxis === "col" ? (e.clientX - rect.left) : (e.clientY - rect.top);
      onRatio(Math.min(0.85, Math.max(0.15, pos / Math.max(1, span))));
    };
    const up = (e) => {
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", up);
      handle.removeEventListener("pointercancel", up);
      try { handle.releasePointerCapture(e.pointerId); } catch (_) {}
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", up);
    handle.addEventListener("pointercancel", up);
  });
  handle.addEventListener("dblclick", (ev) => {
    ev.preventDefault();
    ev.stopPropagation();
    onRatio(0.5);
  });
}

function enterStageFullscreen(node) {
  if (!node) return;
  if (document.fullscreenElement || node.classList.contains("is-monitor")) {
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    node.classList.remove("is-monitor");
    return;
  }
  node.classList.add("is-monitor");
  const req = node.requestFullscreen || node.webkitRequestFullscreen;
  if (!req) return;
  Promise.resolve(req.call(node)).then(() => {
    node.classList.remove("is-monitor");
  }).catch(() => {});
}

function enterCctvFullscreen(singleCamera) {
  const grid = $("cctvGrid");
  if (!grid) return;
  if (document.fullscreenElement || grid.classList.contains("is-monitor")) {
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    grid.classList.remove("is-monitor");
    return;
  }
    if (singleCamera) {
    if (cctvLayout !== "full") cctvLayoutBeforeFull = cctvLayout;
    applyCctvLayout("full");
  }
  enterStageFullscreen($("cctvGrid"));
}

function leaveCctvMonitor() {
  if (cctvLayout === "full" && cctvLayoutBeforeFull) {
    const back = cctvLayoutBeforeFull;
    cctvLayoutBeforeFull = "";
    applyCctvLayout(back);
  }
}

document.addEventListener("fullscreenchange", () => {
  if (!document.fullscreenElement) leaveCctvMonitor();
});
document.addEventListener("keydown", (ev) => {
  if (ev.key !== "Escape") return;
  const grid = $("cctvGrid");
  const combo = document.querySelector(".combo-shell") || document.querySelector(".combo-body");
  const play = document.querySelector(".cctv-play-stage");
  const monitoring = !!(
    document.fullscreenElement
    || (grid && grid.classList.contains("is-monitor"))
    || (combo && combo.classList.contains("is-monitor"))
    || (play && play.classList.contains("is-monitor"))
  );
  if (!monitoring) return;
  ev.stopImmediatePropagation();
  if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
  if (grid && grid.classList.contains("is-monitor")) {
    grid.classList.remove("is-monitor");
    leaveCctvMonitor();
  }
  if (combo && combo.classList.contains("is-monitor")) combo.classList.remove("is-monitor");
  if (play && play.classList.contains("is-monitor")) play.classList.remove("is-monitor");
});

function setCctvViewMode(mode) {
  if (mode === "play" || mode === "event") cctvViewMode = mode;
  else cctvViewMode = "live";
  const liveTab = $("cctvTabLive");
  const playTab = $("cctvTabPlay");
  const eventTab = $("cctvTabEvent");
  const grid = $("cctvGrid");
  const play = $("cctvPlayPane");
  const eventPane = $("cctvEventPane");
  const main = grid && grid.closest(".cctv-main");
  if (liveTab) liveTab.classList.toggle("on", cctvViewMode === "live");
  if (playTab) playTab.classList.toggle("on", cctvViewMode === "play");
  if (eventTab) eventTab.classList.toggle("on", cctvViewMode === "event");
  if (grid) grid.hidden = cctvViewMode !== "live";
  if (play) play.hidden = cctvViewMode !== "play";
  if (eventPane) eventPane.hidden = cctvViewMode !== "event";
  if (main) main.classList.toggle("is-play", cctvViewMode === "play" || cctvViewMode === "event");
  if (cctvViewMode === "live") {
    if (cctvConnected) startCctvLive();
  } else {
    stopCctvLiveOnly();
    if (cctvViewMode === "play") paintCctvPlayList();
    else paintCctvEventList();
  }
}

function syncCctvSideClip(url) {
  document.querySelectorAll(".cctv-snap").forEach((fig) => {
    const same = !!(url && fig.getAttribute("data-url") === url);
    fig.classList.toggle("is-playing", same);
  });
}

function startCctvVideo(el, url) {
  if (!el) return;
  el.hidden = false;
  const kick = () => {
    const played = el.play();
    if (played && played.catch) played.catch(() => {});
  };
  if (el.dataset.clip !== url) {
    el.dataset.clip = url;
    el.onloadeddata = kick;
    el.src = url;
  }
  kick();
}

function playCctvClip(url, mode) {
  if (!url) {
    toast("재생할 영상이 없습니다.");
    return;
  }
  cctvPlayUrl = url;
  const view = mode === "event" ? "event" : "play";
  setCctvViewMode(view);
  const hint = $(view === "event" ? "cctvEventHint" : "cctvPlayHint");
  if (hint) hint.hidden = true;
  const main = $(view === "event" ? "cctvEventVideo" : "cctvPlayVideo");
  if (main) {
    main.onerror = () => toast("영상을 재생하지 못했습니다.");
    startCctvVideo(main, url);
  }
  syncCctvSideClip(url);
}

function paintCctvPlayList() {
  const box = $("cctvPlayList");
  if (!box) return;
  const full = (Array.isArray(cctvRecordings) ? cctvRecordings : [])
    .filter((rec) => rec.kind === "continuous" && Number(rec.size || 0) >= 1000);
  const item = (rec, idx) => `
    <button type="button" class="cctv-play-item${rec.url === cctvPlayUrl ? " on" : ""}" data-rec="${idx}">
      <b>전체</b>
      <span>${escapeAttr(rec.label || rec.mtime || rec.name || "")}</span>
    </button>`;
  if (!full.length) {
    let empty = "저장된 전체 녹화가 없습니다.";
    if (!cctvFfmpegOk) {
      empty = "ffmpeg가 없어 전체 녹화를 할 수 없습니다.<br/>OMS 폴더에 ffmpeg.exe를 넣으세요.";
    } else if (cctvContinuousOn) {
      empty = "전체 연속 녹화 중입니다.<br/>파일이 닫히면(매 정시) 여기에 나타납니다.";
    } else if (cctvContinuousDetail) {
      empty = escapeAttr(cctvContinuousDetail);
    }
    box.innerHTML = `<div class="cctv-empty">${empty}</div>`;
    return;
  }
  box.innerHTML = `
    <div class="cctv-play-head">전체 연속 녹화 · ${cctvRetentionDays}일 후 자동 삭제</div>
    ${cctvContinuousDetail ? `<div class="cctv-play-note">${escapeAttr(cctvContinuousDetail)}</div>` : ""}
    ${full.map(item).join("")}`;
  box.querySelectorAll("[data-rec]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const rec = full[Number(btn.getAttribute("data-rec"))];
      if (!rec || !rec.url) return;
      playCctvClip(rec.url, "play");
    });
  });
}

function paintCctvEventList() {
  const box = $("cctvEventList");
  if (!box) return;
  const fromFiles = (Array.isArray(cctvRecordings) ? cctvRecordings : [])
    .filter((rec) => rec.kind === "event" && Number(rec.size || 0) >= 1000);
  const fromMeta = (Array.isArray(cctvEvents) ? cctvEvents : [])
    .filter((ev) => ev.video_url && !ev.recording && Number(ev.video_size || 0) > 1000)
    .map((ev) => ({
      url: ev.video_url,
      label: `${ev.reason || "이벤트"} · ${ev.time || ""}`,
      reason: ev.reason || "",
      id: ev.id || "",
    }));
  const seen = new Set();
  const rows = [];
  fromMeta.forEach((row) => {
    if (row.url && !seen.has(row.url)) {
      seen.add(row.url);
      rows.push(row);
    }
  });
  fromFiles.forEach((rec) => {
    if (rec.url && !seen.has(rec.url)) {
      seen.add(rec.url);
      rows.push({
        url: rec.url,
        label: `이벤트 · ${rec.label || rec.mtime || rec.name || ""}`,
        reason: "event",
        id: rec.id || "",
      });
    }
  });
  if (!rows.length) {
    box.innerHTML = `<div class="cctv-empty">이벤트 영상이 없습니다.<br/>비상·비인가·빨간 녹화 버튼 영상이 여기에 모입니다.</div>`;
    return;
  }
  box.innerHTML = `
    <div class="cctv-play-head">이벤트 영상 · ${cctvRetentionDays}일 후 자동 삭제</div>
    ${rows.map((rec, idx) => `
      <button type="button" class="cctv-play-item${rec.url === cctvPlayUrl ? " on" : ""}" data-ev="${idx}">
        <b>이벤트</b>
        <span>${escapeAttr(rec.label || "")}</span>
      </button>`).join("")}`;
  box.querySelectorAll("[data-ev]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const rec = rows[Number(btn.getAttribute("data-ev"))];
      if (!rec || !rec.url) return;
      playCctvClip(rec.url, "event");
    });
  });
}

function bindCctvPtzControls(root, options) {
  const scope = root && root.querySelectorAll ? root : document;
  const opts = options || {};
  const channelOf = typeof opts.channel === "function" ? opts.channel : (() => 1);
  const msgId = opts.msgId || "cctvPtzMsg";
  const map = {
    up: { pan: 0, tilt: 1, zoom: 0 },
    down: { pan: 0, tilt: -1, zoom: 0 },
    left: { pan: -1, tilt: 0, zoom: 0 },
    right: { pan: 1, tilt: 0, zoom: 0 },
    zoomin: { pan: 0, tilt: 0, zoom: 1 },
    zoomout: { pan: 0, tilt: 0, zoom: -1 },
  };
  const msg = () => scope.querySelector("#" + msgId) || $(msgId);
  const send = async (action, body) => {
    try {
      const result = await post("/api/cctv/ptz", Object.assign({ action, channel: channelOf() }, body || {}));
      if (msg()) msg().textContent = (result.data && result.data.detail) || (result.ok ? "OK" : "PTZ 실패");
      return result;
    } catch (_) {
      if (msg()) msg().textContent = "PTZ 요청 실패 (카메라 PTZ 미지원 가능)";
      return { ok: false, data: {} };
    }
  };
  scope.querySelectorAll("[data-ptz]").forEach((btn) => {
    const key = btn.getAttribute("data-ptz");
    if (key === "stop") {
      btn.addEventListener("click", () => send("stop"));
      return;
    }
    if (key === "home") {
      btn.addEventListener("click", () => send("home"));
      return;
    }
    if (key === "set_home") {
      btn.addEventListener("click", async () => {
        const result = await send("set_home");
        const token = result && result.data && result.data.token;
        const input = scope.querySelector('[name="ptz_home_preset"]');
        if (token && input) input.value = token;
        if (result && result.ok) toast("현재 위치를 이 채널 Home으로 저장했습니다.");
      });
      return;
    }
    const vel = map[key];
    if (!vel) return;
    const start = (ev) => {
      ev.preventDefault();
      send("move", vel);
    };
    const stop = (ev) => {
      ev.preventDefault();
      send("stop");
    };
    btn.addEventListener("mousedown", start);
    btn.addEventListener("mouseup", stop);
    btn.addEventListener("mouseleave", stop);
    btn.addEventListener("touchstart", start, { passive: false });
    btn.addEventListener("touchend", stop);
  });
}

function scaleComboGuns() {
  const box = $("comboGuns");
  if (!box) return;
  const wrap = box.querySelector(".combo-fs-wrap");
  const fs = box.querySelector(".fs--embed");
  if (!wrap || !fs) return;
  const bw = Math.max(1, box.clientWidth - 8);
  const bh = Math.max(1, box.clientHeight - 8);
  const scale = Math.max(0.2, Math.min(bw / 1280, bh / 720));
  const designW = Math.max(1280, Math.round(bw / scale));
  const designH = 720;
  wrap.style.width = `${Math.round(designW * scale)}px`;
  wrap.style.height = `${Math.round(designH * scale)}px`;
  fs.style.width = `${designW}px`;
  fs.style.height = `${designH}px`;
  fs.style.transform = `scale(${scale})`;
  wrap.style.setProperty("--combo-fs-scale", String(scale));
  alignComboVideoFrame();
}

function alignComboVideoFrame() {
  const guns = document.querySelector(".combo-guns");
  const img = $("comboCctvImg");
  const stage = document.querySelector(".combo-cctv-stage");
  if (!guns) return;
  if (!img || !stage || img.hidden || !img.naturalWidth || !img.naturalHeight) {
    guns.classList.remove("has-cam-frame");
    return;
  }
  if (comboFrame === "stack") {
    guns.classList.remove("has-cam-frame");
    return;
  }
  const stageRect = stage.getBoundingClientRect();
  const gunsRect = guns.getBoundingClientRect();
  const fit = Math.min(stageRect.width / img.naturalWidth, stageRect.height / img.naturalHeight);
  const contentH = img.naturalHeight * fit;
  const contentTop = stageRect.top + (stageRect.height - contentH) / 2;
  guns.dataset.camAxis = "y";
  guns.style.setProperty("--cam-line-a", `${Math.max(0, contentTop - gunsRect.top)}px`);
  guns.style.setProperty("--cam-line-b", `${Math.max(0, gunsRect.bottom - contentTop - contentH)}px`);
  guns.classList.add("has-cam-frame");
}

function applyComboSplit() {
  const body = document.querySelector(".combo-body");
  if (!body) return;
  const ratio = Math.min(0.85, Math.max(0.15, comboSplitRatio));
  comboSplitRatio = ratio;
  body.style.setProperty("--combo-a", `${ratio}fr`);
  body.style.setProperty("--combo-b", `${(1 - ratio)}fr`);
  body.style.setProperty("--combo-pct", `${ratio * 100}%`);
  const handle = body.querySelector(".combo-split");
  if (handle) {
    handle.dataset.axis = comboFrame === "stack" ? "row" : "col";
    handle.classList.toggle("combo-split--v", comboFrame !== "stack");
    handle.classList.toggle("combo-split--h", comboFrame === "stack");
  }
  scaleComboGuns();
}

function paintComboGuns() {
  const box = $("comboGuns");
  if (!box) return;
  if (!state.data) {
    box.innerHTML = `<div class="cctv-empty">총기 현황 데이터 없음</div>`;
    return;
  }
  const next = fullScreenHtml(state.data, { embed: true });
  if (box.dataset.view !== next) {
    box.dataset.view = next;
    box.innerHTML = `<div class="combo-fs-wrap">${next}</div>`;
  }
  requestAnimationFrame(() => {
    scaleComboGuns();
    requestAnimationFrame(scaleComboGuns);
  });
}

function applyComboLogHeight() {
  const shell = document.querySelector(".combo-shell");
  const panel = $("comboLogPanel");
  if (!shell || !panel || panel.hidden) return;
  const maxH = Math.max(140, Math.floor(shell.clientHeight * 0.72));
  const h = Math.min(Math.max(Number(comboLogHeight) || 200, 120), maxH);
  comboLogHeight = h;
  shell.style.setProperty("--combo-log-h", `${h}px`);
}

function bindComboLogSplit() {
  const handle = $("comboLogSplit");
  if (!handle || handle.dataset.bound === "1") return;
  handle.dataset.bound = "1";
  handle.addEventListener("pointerdown", (ev) => {
    if (ev.button != null && ev.button !== 0) return;
    ev.preventDefault();
    ev.stopPropagation();
    const shell = document.querySelector(".combo-shell");
    if (!shell) return;
    handle.setPointerCapture(ev.pointerId);
    const move = (e) => {
      const rect = shell.getBoundingClientRect();
      const next = Math.round(rect.bottom - e.clientY);
      comboLogHeight = next;
      applyComboLogHeight();
      scaleComboGuns();
    };
    const up = (e) => {
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", up);
      handle.removeEventListener("pointercancel", up);
      try { handle.releasePointerCapture(e.pointerId); } catch (_) {}
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", up);
    handle.addEventListener("pointercancel", up);
  });
  handle.addEventListener("dblclick", (ev) => {
    ev.preventDefault();
    ev.stopPropagation();
    comboLogHeight = 200;
    applyComboLogHeight();
    scaleComboGuns();
  });
}

function setComboLogVisible(on) {
  comboLogOpen = !!on;
  const panel = $("comboLogPanel");
  const split = $("comboLogSplit");
  const btn = $("comboLogBtn");
  if (panel) panel.hidden = !comboLogOpen;
  if (split) split.hidden = !comboLogOpen;
  if (btn) {
    btn.classList.toggle("on", comboLogOpen);
    btn.setAttribute("aria-pressed", comboLogOpen ? "true" : "false");
  }
  if (comboLogOpen) {
    const comboBox = $("comboLogLines");
    if (comboBox) delete comboBox.dataset.key;
    paintLogs(state.data && state.data.logs);
    applyComboLogHeight();
  }
  requestAnimationFrame(() => {
    applyComboSplit();
    scaleComboGuns();
  });
}

function openComboStatus() {
  stopCctvTimers();
  openModal(`
    <div class="win-head cctv-head">
      <h3 class="win-title">총기 · CCTV 현황</h3>
      <div class="cctv-iconbar" id="comboFrameBar">
        <button type="button" class="lay-btn${comboFrame === "side" ? " on" : ""}" data-combo-frame="side" title="좌우 분할. 한 번 더 누르면 좌우가 바뀝니다" aria-label="좌우 분할"><span class="lay lay-2v"><i></i><i></i></span></button>
        <button type="button" class="lay-btn${comboFrame === "stack" ? " on" : ""}" data-combo-frame="stack" title="상하 분할. 한 번 더 누르면 상하가 바뀝니다" aria-label="상하 분할"><span class="lay lay-2h"><i></i><i></i></span></button>
        <button type="button" class="cctv-ico" id="comboToCctv">CCTV 관제</button>
        <button type="button" class="cctv-ico${comboLogOpen ? " on" : ""}" id="comboLogBtn" aria-pressed="${comboLogOpen ? "true" : "false"}" title="하단 실시간 현황 표시">실시간현황</button>
        <button type="button" class="cctv-ico" id="comboClose">닫기</button>
      </div>
      <span class="cctv-clock" id="cctvClock"></span>
    </div>
    <div class="combo-shell">
      <div class="win-body combo-body${comboFrame === "stack" ? " combo-body--stack" : ""}${comboSwapped ? " combo-swapped" : ""}">
        <section class="combo-pane combo-guns">
          <div class="combo-pane-title">총기 보관현황</div>
          <div class="combo-guns-inner" id="comboGuns"></div>
        </section>
        <section class="combo-pane combo-cctv">
          <div class="combo-pane-title">CCTV 라이브 <span class="cctv-live-badge off" id="cctvLiveBadge">LIVE</span></div>
          <div class="combo-cctv-stage">
            <img id="comboCctvImg" alt="CCTV" hidden />
            <div class="cctv-placeholder" id="comboCctvPh">카메라 연결 중…</div>
          </div>
        </section>
        <div class="combo-split ${comboFrame === "stack" ? "combo-split--h" : "combo-split--v"}" id="comboSplit" role="separator" aria-label="화면 크기" title="드래그하여 크기 조절"></div>
      </div>
      <div class="combo-log-split" id="comboLogSplit" role="separator" aria-label="실시간 현황 크기" title="드래그하여 실시간 현황 높이 조절"${comboLogOpen ? "" : " hidden"}></div>
      <footer class="oms-log combo-log" id="comboLogPanel" aria-label="실시간 현황"${comboLogOpen ? "" : " hidden"}>
        <div class="oms-log__label"><span>실시간</span><span>현황</span></div>
        <div class="oms-log__lines" id="comboLogLines"></div>
      </footer>
    </div>
  `, "cctv");
  const modal = $("modal");
  modal.className = "modal cctv-max";
  paintComboGuns();
  applyComboSplit();
  bindComboLogSplit();
  if (comboLogOpen) {
    applyComboLogHeight();
    paintLogs(state.data && state.data.logs);
  }
  window.addEventListener("resize", scaleComboGuns);
  if (comboResizeObserver) comboResizeObserver.disconnect();
  if (typeof ResizeObserver !== "undefined" && $("comboGuns")) {
    comboResizeObserver = new ResizeObserver(() => scaleComboGuns());
    comboResizeObserver.observe($("comboGuns"));
  }
  bindPaneSplit($("comboSplit"), comboFrame === "stack" ? "row" : "col", (ratio) => {
    comboSplitRatio = ratio;
    applyComboSplit();
  });
  document.querySelectorAll("[data-combo-frame]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const next = btn.getAttribute("data-combo-frame") === "stack" ? "stack" : "side";
      if (next === comboFrame) comboSwapped = !comboSwapped;
      else {
        comboSwapped = false;
        comboFrame = next;
      }
      const body = document.querySelector(".combo-body");
      if (body) {
        body.classList.toggle("combo-body--stack", comboFrame === "stack");
        body.classList.toggle("combo-swapped", comboSwapped);
      }
      document.querySelectorAll("[data-combo-frame]").forEach((el) => {
        el.classList.toggle("on", el.getAttribute("data-combo-frame") === comboFrame);
      });
      applyComboSplit();
    });
  });
  const comboShell = document.querySelector(".combo-shell");
  if (comboShell) {
    comboShell.addEventListener("dblclick", (ev) => {
      if (ev.target.closest("button, a, input, select, .combo-split, .combo-log-split, .oms-log__lines")) return;
      enterStageFullscreen(comboShell);
      requestAnimationFrame(() => {
        if (comboLogOpen) applyComboLogHeight();
        applyComboSplit();
        scaleComboGuns();
      });
    });
  }
  $("comboLogBtn").onclick = () => setComboLogVisible(!comboLogOpen);
  $("comboClose").onclick = () => {
    window.removeEventListener("resize", scaleComboGuns);
    if (comboResizeObserver) comboResizeObserver.disconnect();
    comboResizeObserver = null;
    stopCctvTimers();
    closeModal();
  };
  $("comboToCctv").onclick = () => {
    window.removeEventListener("resize", scaleComboGuns);
    if (comboResizeObserver) comboResizeObserver.disconnect();
    comboResizeObserver = null;
    stopCctvTimers();
    openCctv();
  };
  tickCctvClock();
  let comboLiveBusy = false;
  const refreshComboLive = () => {
    if (comboLiveBusy) return;
    const img = $("comboCctvImg");
    const ph = $("comboCctvPh");
    const badge = $("cctvLiveBadge");
    if (!img) return;
    comboLiveBusy = true;
    const next = new Image();
    next.onload = () => {
      img.src = next.src;
      img.hidden = false;
      if (ph) ph.hidden = true;
      if (badge) badge.classList.remove("off");
      alignComboVideoFrame();
      comboLiveBusy = false;
    };
    next.onerror = () => {
      if (badge) badge.classList.add("off");
      comboLiveBusy = false;
    };
    next.src = `/api/cctv/snapshot?t=${Date.now()}`;
  };
  refreshComboLive();
  cctvComboTimer = setInterval(refreshComboLive, 120);
}

async function bootCctvAutoConnect() {
  try {
    const result = await post("/api/cctv/test", { save: false });
    const ok = !!(result.ok && result.data && result.data.ok !== false);
    cctvConnected = ok;
    if (ok) startCctvLive();
    else {
      stopCctvLiveOnly();
      const ph = $("cctvPlaceholder");
      if (ph) {
        ph.hidden = false;
        ph.innerHTML = "저장된 카메라에 연결하지 못했습니다.<br/>상단 <b>설정</b>에서 IP / 아이디 / 비밀번호를 확인하세요.";
      }
    }
  } catch (_) {
    const ph = $("cctvPlaceholder");
    if (ph) ph.innerHTML = "연결 확인 실패. 상단 <b>설정</b>을 열어 주세요.";
  }
  refreshCctvMeta();
}

function cctvFormBody(form, extra) {
  const fd = new FormData(form);
  const body = Object.assign({
    save: true,
    name: String(fd.get("name") || "").trim(),
    host: String(fd.get("host") || "").trim(),
    mode: String(fd.get("mode") || "auto"),
    use_https: !!fd.get("use_https"),
    http_port: Number(fd.get("http_port") || 80),
    https_port: Number(fd.get("https_port") || 443),
    rtsp_port: Number(fd.get("rtsp_port") || 554),
    user: String(fd.get("user") || "").trim(),
    password: String(fd.get("password") || ""),
    snapshot_path: String(fd.get("snapshot_path") || "").trim(),
    rtsp_path: String(fd.get("rtsp_path") || "").trim(),
    onvif_device_path: String(fd.get("onvif_device_path") || "").trim(),
    onvif_media_path: String(fd.get("onvif_media_path") || "").trim(),
    onvif_profile: String(fd.get("onvif_profile") || "").trim(),
    onvif_ptz_path: String(fd.get("onvif_ptz_path") || "").trim(),
    ptz_home_preset: String(fd.get("ptz_home_preset") || "").trim(),
    retention_days: Number(fd.get("retention_days") || 1),
    continuous_record: !!fd.get("continuous_record"),
    event_record_sec: Number(fd.get("event_record_sec") || 30),
    monitor_inside: !!fd.get("monitor_inside"),
    monitor_outside: !!fd.get("monitor_outside"),
    auto_event_on_emergency: !!fd.get("auto_event_on_emergency"),
    auto_event_on_unauthorized: !!fd.get("auto_event_on_unauthorized"),
    auto_event_on_rfid_emergency: !!fd.get("auto_event_on_rfid_emergency"),
    auto_ptz_on_event: !!fd.get("auto_ptz_on_event"),
    auto_ptz_on_person: !!fd.get("auto_ptz_on_person"),
    auto_light_on_person: !!fd.get("auto_light_on_person"),
    person_ai_enabled: !!fd.get("person_ai_enabled"),
    enabled: !!fd.get("enabled"),
    channel: cctvSettingsChannel || 1,
  }, extra || {});
  if (!body.password) delete body.password;
  const presets = {};
  for (let i = 1; i <= 10; i += 1) {
    const el = form.querySelector(`[name="preset_${i}"]`);
    const tok = el ? String(el.value || "").trim() : "";
    if (tok) presets[String(i)] = tok;
  }
  body.ptz_presets = presets;
  const eventMap = {};
  ["emergency", "unauthorized", "offline", "person", "ops", "manual", "rfid_emergency"].forEach((kind) => {
    const el = form.querySelector(`[name="map_${kind}"]`);
    if (!el) return;
    const n = Number(el.value);
    if (n >= 1 && n <= 10) eventMap[kind] = n;
  });
  body.ptz_event_map = eventMap;
  const lockerMap = {};
  form.querySelectorAll("[data-lk-id]").forEach((input) => {
    const id = input.getAttribute("data-lk-id") || "";
    const kind = input.getAttribute("data-lk-kind") || "";
    const token = String(input.value || "").trim();
    if (!id || (kind !== "emergency" && kind !== "unauthorized" && kind !== "ops" && kind !== "rfid_emergency") || !token) return;
    if (!lockerMap[id]) lockerMap[id] = {};
    lockerMap[id][kind] = token;
  });
  body.ptz_lockers = lockerMap;
  return body;
}

function paintCctvDiagnose(report) {
  const box = $("cctvDiagBox");
  if (!box || !report) return;
  const lines = [];
  lines.push(`결과: ${report.online ? "연결 가능" : "연결 실패"} / 모드=${report.mode || "-"} / HTTPS=${report.use_https ? "ON" : "OFF"}`);
  (report.steps || []).forEach((step) => {
    const flag = step.ok === true ? "OK" : (step.ok === false ? "FAIL" : "—");
    lines.push(`[${flag}] ${step.name}: ${step.detail || ""}`);
  });
  (report.advice || []).forEach((a) => lines.push(`안내: ${a}`));
  box.hidden = false;
  box.textContent = lines.join("\n");
}

async function connectCctvFromForm(showToast) {
  const form = $("cctvConnectForm");
  const msg = $("cctvConnectMsg");
  const btn = $("cctvConnectBtn");
  if (!form) return false;
  const notify = showToast !== false;
  const body = cctvFormBody(form);
  if (!body.host) {
    if (msg) { msg.textContent = "카메라 IP를 입력하세요."; msg.className = "cctv-connect-msg bad"; }
    return false;
  }
  if (btn) { btn.disabled = true; btn.textContent = "연결 중…"; }
  if (msg) { msg.textContent = "카메라에 연결하는 중…"; msg.className = "cctv-connect-msg"; }
  try {
    const result = await post("/api/cctv/test", body);
    const ok = !!(result.ok && result.data && result.data.ok !== false);
    let detail = (result.data && (result.data.detail || result.data.error)) || (ok ? "연결 성공" : "연결 실패");
    if (result.status === 404) {
      detail = "구버전 OMS — 최신 exe를 다시 실행하세요";
    }
    cctvConnected = ok;
    if (msg) {
      msg.textContent = detail;
      msg.className = "cctv-connect-msg " + (ok ? "ok" : "bad");
    }
    if (notify) toast(ok ? "카메라 연결·저장 완료 (다음부터 자동 연결)" : detail);
    if (ok) {
      if (form.password) form.password.value = "";
      const idx = Math.max(0, (Number(body.channel) || 1) - 1);
      if (!cctvChannelCfgs[idx]) cctvChannelCfgs[idx] = {};
      Object.assign(cctvChannelCfgs[idx], body);
      delete cctvChannelCfgs[idx].password;
      cctvChannelCfgs[idx].savedHost = String(body.host || "").trim();
      if (body.password) cctvChannelCfgs[idx].has_password = true;
      if ((Number(body.channel) || 1) === 1) startCctvLive();
      if ($("cctvSetPreview")) startCctvSetPreview();
      await post("/api/cctv/config", body);
    } else {
      stopCctvLiveOnly();
      if (result.data && result.data.diagnose) paintCctvDiagnose(result.data.diagnose);
    }
    refreshCctvMeta();
    return ok;
  } catch (_) {
    cctvConnected = false;
    if (msg) { msg.textContent = "연결 요청 실패"; msg.className = "cctv-connect-msg bad"; }
    if (notify) toast("연결 요청 실패");
    stopCctvLiveOnly();
    return false;
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "연결·저장"; }
  }
}

async function runCctvDiagnose() {
  const form = $("cctvConnectForm");
  const msg = $("cctvConnectMsg");
  if (!form) return;
  const body = cctvFormBody(form, { save: true });
  if (msg) { msg.textContent = "ONVIF/JPEG/RTSP 진단 중…"; msg.className = "cctv-connect-msg"; }
  try {
    const result = await post("/api/cctv/diagnose", body);
    const report = result.data || {};
    paintCctvDiagnose(report);
    if (msg) {
      msg.textContent = report.detail || (report.ok ? "진단 완료 — 연결 가능" : "진단 완료 — 연결 실패");
      msg.className = "cctv-connect-msg " + (report.ok ? "ok" : "bad");
    }
    if (report.ok) startCctvLive();
  } catch (_) {
    if (msg) { msg.textContent = "진단 요청 실패"; msg.className = "cctv-connect-msg bad"; }
  }
}

function stopCctvLiveOnly() {
  if (cctvLiveTimer) {
    clearInterval(cctvLiveTimer);
    cctvLiveTimer = null;
  }
  cctvLiveInFlight = false;
  document.querySelectorAll(".cctv-live-img").forEach((img) => {
    img.hidden = true;
    img.removeAttribute("src");
    const ph = img.parentElement && img.parentElement.querySelector(".cctv-placeholder");
    if (ph) ph.hidden = false;
  });
  const badge = $("cctvLiveBadge");
  if (badge) badge.classList.add("off");
}

function startCctvLive() {
  stopCctvLiveOnly();
  cctvLiveInFlight = false;
  document.querySelectorAll(".cctv-live-img").forEach((img) => {
    img.hidden = false;
    const ph = img.parentElement && img.parentElement.querySelector(".cctv-placeholder");
    if (ph) ph.hidden = true;
  });
  const badge = $("cctvLiveBadge");
  if (badge) badge.classList.remove("off");
  refreshCctvLive();
  cctvLiveTimer = setInterval(refreshCctvLive, 120);
}

function tickCctvClock() {
  const el = $("cctvClock");
  if (!el) return;
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  el.textContent = `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
  if ($("cctvClock")) setTimeout(tickCctvClock, 1000);
}

function refreshCctvLive() {
  // Do not stack snapshot requests — backlog was a major live lag source.
  if (cctvLiveInFlight) return;
  const imgs = Array.from(document.querySelectorAll(".cctv-live-img")).filter((img) => !img.hidden);
  if (!imgs.length) return;
  cctvLiveInFlight = true;
  const bust = Date.now();
  let pending = imgs.length;
  const done = () => {
    pending -= 1;
    if (pending <= 0) cctvLiveInFlight = false;
  };
  imgs.forEach((img) => {
    const ch = img.getAttribute("data-ch") || "1";
    const next = new Image();
    next.onload = function () {
      img.src = next.src;
      img.hidden = false;
      const ph = img.parentElement && img.parentElement.querySelector(".cctv-placeholder");
      if (ph) ph.hidden = true;
      done();
    };
    next.onerror = done;
    next.src = `/api/cctv/snapshot?ch=${ch}&t=${bust}`;
  });
}

async function refreshCctvMeta() {
  if (!$("cctvCamStats") && !$("comboCctvImg")) return;
  try {
    const result = await api("/api/cctv/status");
    if (!result.ok || !result.data) return;
    const data = result.data;
    const cfg = data.config || {};
    const online = !!data.online;
    cctvConnected = online;
    cctvRecordings = Array.isArray(data.recordings) ? data.recordings : [];
    cctvEvents = Array.isArray(data.events) ? data.events : [];
    const cont = data.continuous || {};
    cctvContinuousOn = !!(data.continuous_recording || cont.active);
    cctvContinuousDetail = String(cont.detail || "");
    cctvFfmpegOk = data.ffmpeg !== false && cont.ffmpeg !== false;
    cctvRetentionDays = Number(data.retention_days || 1);
    const recBadge = $("cctvRecBadge");
    if (recBadge) {
      if (!cctvFfmpegOk) {
        recBadge.hidden = false;
        recBadge.textContent = "NO FFMPEG";
        recBadge.className = "cctv-rec-badge bad";
        recBadge.title = cctvContinuousDetail || "ffmpeg 없음 — OMS 폴더에 ffmpeg.exe 필요";
      } else if (cctvContinuousOn) {
        recBadge.hidden = false;
        recBadge.textContent = "REC";
        recBadge.className = "cctv-rec-badge on";
        recBadge.title = cctvContinuousDetail || "전체 연속 녹화 중";
      } else {
        recBadge.textContent = "REC?";
        recBadge.className = "cctv-rec-badge";
        recBadge.title = cctvContinuousDetail || "";
        recBadge.hidden = !cctvContinuousDetail;
      }
    }
    const channels = Array.isArray(data.channels) && data.channels.length ? data.channels.slice(0, 4) : [cfg];
    while (channels.length < 4) channels.push({});
    const nextHosts = channels.map((c) => String((c && c.host) || "").trim());
    const hostsChanged = nextHosts.join("|") !== cctvChannelHosts.join("|");
    cctvChannelHosts = nextHosts;
    const configured = channels.filter((c) => String((c && c.host) || "").trim()).length;
    if ($("cctvCamStats")) {
      $("cctvCamStats").innerHTML = `
        <span>채널 <b>4</b></span>
        <span class="${configured ? "ok" : ""}">등록 <b>${configured}</b></span>
        <span class="${online ? "ok" : "bad"}">1CH ${online ? "연결" : "단절"}</span>`;
    }
    if ($("cctvCamList")) {
      $("cctvCamList").innerHTML = channels.map((c, i) => {
        const host = String((c && c.host) || "").trim();
        const main = i === 0;
        const label = !host ? "미설정" : (main ? (online ? "연결" : "단절") : "저장됨");
        return `<li class="${main && online ? "on" : ""} ${main ? "picked" : ""}">
          <span>${i + 1}CH ${escapeAttr((c && c.name) || ("카메라 " + (i + 1)))}</span>
          <small>${escapeAttr(host || "-")} · ${label}</small>
        </li>`;
      }).join("");
    }
    if ($("cctvTitle")) $("cctvTitle").textContent = cfg.name || "무기고 카메라";
    const label = document.querySelector('.cctv-cell.active .cctv-cell-label') || $("cctvCellLabel");
    if (label) label.textContent = cfg.name || "CAM-01";
    if ($("cctvLiveBadge")) $("cctvLiveBadge").classList.toggle("off", !online);
    if ($("cctvGrid") && cctvViewMode === "live") {
      if (hostsChanged) applyCctvLayout(cctvLayout);
      else if (online && !cctvLiveTimer) startCctvLive();
      else if (!online && cctvLiveTimer) stopCctvLiveOnly();
    }
    if (cctvViewMode === "play") paintCctvPlayList();
    if (cctvViewMode === "event") paintCctvEventList();
    const events = data.events || [];
    if (cctvRecWaitId) {
      const done = events.find((ev) => ev.id === cctvRecWaitId && !ev.recording && Number(ev.video_size || 0) > 1000);
      const btn = $("cctvRec");
      if (done && done.video_url) {
        cctvRecWaitId = "";
        if (btn) {
          btn.classList.remove("is-on");
          btn.title = "이벤트 녹화";
        }
        playCctvClip(done.video_url, "event");
        toast("이벤트 영상이 저장되었습니다.");
      }
    }
    const log = $("cctvEventLog");
    if (log) {
      if (!events.length) log.innerHTML = `<div class="cctv-empty">이벤트 대기 중…</div>`;
      else {
        log.innerHTML = events.slice(0, 40).map((ev) => `
          <div class="cctv-log-row">
            <span>${escapeAttr(cfg.name || "CAM-01")}</span>
            <span>${escapeAttr(ev.locker || "-")}${ev.slot ? ` #${ev.slot}` : ""}</span>
            <span class="k">${escapeAttr(ev.reason || "")}</span>
            <span class="t">${escapeAttr(ev.time || "")}</span>
            <span class="m">${escapeAttr(ev.message || "")}</span>
          </div>`).join("");
      }
    }
    const snaps = $("cctvSnaps");
    if (snaps) {
      const clips = events.filter((ev) => ev.snapshot_url || ev.video_url);
      const sig = clips.slice(0, 16).map((ev) => `${ev.id || ""}:${ev.video_url || ""}:${ev.snapshot_url || ""}:${ev.video_size || 0}`).join("|");
      if (snaps.dataset.sig !== sig) {
        snaps.dataset.sig = sig;
        if (!clips.length) snaps.innerHTML = `<div class="cctv-empty">이벤트 영상 없음</div>`;
        else {
          snaps.innerHTML = clips.slice(0, 16).map((ev) => {
            const canPlay = !!(ev.video_url && !ev.recording && Number(ev.video_size || 0) > 1000);
            const tip = canPlay ? "클릭하면 재생" : (ev.recording ? "녹화 중…" : "재생할 영상 없음");
            return `
            <figure class="cctv-snap${canPlay ? " is-playable" : ""}${ev.recording ? " is-recording" : ""}"
              ${canPlay ? `data-url="${escapeAttr(ev.video_url)}" role="button" tabindex="0"` : ""}
              title="${escapeAttr(tip)}">
              ${ev.snapshot_url ? `<img src="${escapeAttr(ev.snapshot_url)}" alt="" />` : `<div class="cctv-empty">미리보기 없음</div>`}
              ${canPlay ? `<div class="cctv-snap-playmark" aria-hidden="true">▶</div>` : ""}
              <figcaption><span class="dot"></span>${escapeAttr(ev.reason || "")}
                ${ev.slot ? ` · 슬롯 ${escapeAttr(String(ev.slot))}` : ""}<br/>${escapeAttr(ev.time || "")}
                ${ev.recording ? `<br/>녹화 중…` : (canPlay ? `<br/><span class="cctv-snap-hint">클릭하여 재생</span>` : (ev.video_url ? `<br/>저장된 영상 없음` : ""))}</figcaption>
            </figure>`;
          }).join("");
          snaps.querySelectorAll(".cctv-snap.is-playable").forEach((fig) => {
            const play = () => playCctvClip(fig.getAttribute("data-url") || "", "event");
            fig.addEventListener("click", play);
            fig.addEventListener("keydown", (ev) => {
              if (ev.key === "Enter" || ev.key === " ") {
                ev.preventDefault();
                play();
              }
            });
          });
        }
      }
    }
  } catch (_) {}
}

function normalizeCctvChannels(data) {
  const raw = data && Array.isArray(data.channels) && data.channels.length
    ? data.channels
    : [((data && data.config) || {})];
  const out = [];
  for (let i = 0; i < 4; i += 1) {
    const src = raw[i] && typeof raw[i] === "object" ? raw[i] : {};
    const host = String(src.host || "").trim();
    out.push(Object.assign({
      name: `카메라 ${i + 1}`,
      host: "",
      enabled: i === 0,
      has_password: false,
      ptz_presets: {},
      ptz_event_map: {},
    }, src, { savedHost: host }));
  }
  return out;
}

function stashCctvSettingsForm() {
  const form = $("cctvConnectForm");
  if (!form) return;
  const idx = Math.max(0, (cctvSettingsChannel || 1) - 1);
  const prev = cctvChannelCfgs[idx] || {};
  const body = cctvFormBody(form);
  const next = Object.assign({}, prev, body);
  next.savedHost = prev.savedHost || "";
  next.has_password = !!prev.has_password;
  delete next.password;
  cctvChannelCfgs[idx] = next;
}

function stopCctvSetPreview() {
  if (cctvSetPreviewTimer) {
    clearInterval(cctvSetPreviewTimer);
    cctvSetPreviewTimer = null;
  }
}

function startCctvSetPreview() {
  stopCctvSetPreview();
  const tick = () => {
    const img = $("cctvSetPreview");
    const ph = $("cctvSetPreviewPh");
    if (!img) {
      stopCctvSetPreview();
      return;
    }
    const saved = String((cctvChannelCfgs[cctvSettingsChannel - 1] || {}).savedHost || "").trim();
    if (!saved) {
      img.hidden = true;
      img.removeAttribute("src");
      if (ph) {
        ph.hidden = false;
        ph.textContent = "이 채널 IP를 입력하고 연결·저장하면, 영상을 보면서 팬·틸트를 맞출 수 있습니다.";
      }
      return;
    }
    const ch = cctvSettingsChannel;
    const next = new Image();
    next.onload = () => {
      if (cctvSettingsChannel !== ch || !$("cctvSetPreview")) return;
      img.src = next.src;
      img.hidden = false;
      if (ph) ph.hidden = true;
    };
    next.onerror = () => {
      if (cctvSettingsChannel !== ch || img.getAttribute("src")) return;
      img.hidden = true;
      if (ph) {
        ph.hidden = false;
        ph.textContent = "미리보기에 연결하지 못했습니다. IP·비밀번호를 확인한 뒤 다시 연결·저장하세요.";
      }
    };
    next.src = `/api/cctv/snapshot?ch=${ch}&t=${Date.now()}`;
  };
  tick();
  cctvSetPreviewTimer = setInterval(tick, 800);
}

function paintCctvSettingsForm() {
  const layer = $("cctvSettings");
  if (!layer) return;
  stopCctvSetPreview();
  const idx = Math.max(0, Math.min(3, (cctvSettingsChannel || 1) - 1));
  cctvSettingsChannel = idx + 1;
  const cfg = cctvChannelCfgs[idx] || {};
  const hasPw = !!cfg.has_password;
  const presets = (cfg.ptz_presets && typeof cfg.ptz_presets === "object") ? cfg.ptz_presets : {};
  const eventMap = (cfg.ptz_event_map && typeof cfg.ptz_event_map === "object") ? { ...cfg.ptz_event_map } : {};
  if (eventMap.rfid_emergency == null && eventMap.rfid_stored != null) eventMap.rfid_emergency = eventMap.rfid_stored;
  const rfidRecOn = (cfg.auto_event_on_rfid_emergency != null)
    ? cfg.auto_event_on_rfid_emergency !== false
    : cfg.auto_event_on_rfid_stored !== false;
  const sitHints = {
    1: "비상개폐(기본)", 2: "미인가 접근(기본)", 3: "네트워크 스위치", 4: "CCTV 사람 인지", 5: "수동 녹화",
    6: "RFID 태깅 비상", 7: "예비7", 8: "예비8", 9: "예비9", 10: "예비10",
  };
  const lockerBook = (cfg.ptz_lockers && typeof cfg.ptz_lockers === "object") ? cfg.ptz_lockers : {};
  const lockerList = (state.data && Array.isArray(state.data.boards)) ? state.data.boards : [];
  const lockerBinds = lockerList.map((cab, index) => {
    const id = String((cab && cab.id) || "");
    const name = String((cab && cab.name) || `${index + 1}번 보관함`);
    const saved = (lockerBook[id] && typeof lockerBook[id] === "object") ? lockerBook[id] : {};
    const rfidToken = saved.rfid_emergency || saved.rfid_stored || "";
    const row = (kind, label, value) => `<label class="cctv-slot-bind">${escapeAttr(name)} · ${label}
      <span class="cctv-slot-row">
        <input data-lk-id="${escapeAttr(id)}" data-lk-kind="${kind}" value="${escapeAttr(value != null ? value : (saved[kind] || ""))}" placeholder="프리셋번호" />
        <button type="button" class="cctv-bind-btn" data-bind-locker="${escapeAttr(id)}" data-bind-kind="${kind}">지금위치저장</button>
      </span>
    </label>`;
    return row("emergency", "비상개폐")
      + row("unauthorized", "비인가 접근")
      + row("rfid_emergency", "RFID 태깅 비상", rfidToken)
      + row("offline", "네트워크 단절")
      + row("person", "CCTV 사람 인지")
      + row("ops", "보관·불출");
  }).join("");
  const presetInputs = Array.from({ length: 10 }, (_, i) => {
    const n = i + 1;
    return `<label class="cctv-slot-bind">상황 ${n}<small>${escapeAttr(sitHints[n] || "")}</small>
      <span class="cctv-slot-row">
        <input name="preset_${n}" value="${escapeAttr(presets[String(n)] || "")}" placeholder="프리셋번호" />
        <button type="button" class="cctv-bind-btn" data-bind-sit="${n}">지금위치저장</button>
      </span>
    </label>`;
  }).join("");
  const mapSelect = (kind, defSit) => {
    const cur = Number(eventMap[kind] != null ? eventMap[kind] : defSit);
    const opts = Array.from({ length: 10 }, (_, i) => {
      const n = i + 1;
      return `<option value="${n}" ${cur === n ? "selected" : ""}>상황 ${n}</option>`;
    }).join("");
    return `<select name="map_${kind}">${opts}</select>`;
  };
  const tabs = [1, 2, 3, 4].map((n) => {
    const ready = String((cctvChannelCfgs[n - 1] || {}).savedHost || "").trim();
    const on = n === cctvSettingsChannel;
    return `<button type="button" class="cctv-ch-tab${on ? " on" : ""}${ready ? " ready" : ""}" data-cctv-ch="${n}">${n}CH</button>`;
  }).join("");
  layer.hidden = false;
  layer.innerHTML = `
    <div class="cctv-settings-card">
      <div class="cctv-settings-head">
        <div class="win-caption">
          <h3>CCTV 설정 · ${cctvSettingsChannel}CH</h3>
          <div class="win-chrome">
            <button type="button" class="cap cap-min" id="cctvSetMin" title="아래로" aria-label="아래로"></button>
            <button type="button" class="cap cap-max" id="cctvSetMax" title="크게" aria-label="크게"></button>
            <button type="button" class="cap cap-close" id="cctvSetClose" title="닫기" aria-label="닫기"></button>
          </div>
        </div>
        <div class="cctv-ch-tabs">${tabs}</div>
      </div>
      <div class="cctv-set-stage">
          <div class="cctv-set-live">
            <img id="cctvSetPreview" alt="채널 미리보기" hidden />
            <div class="cctv-placeholder" id="cctvSetPreviewPh">이 채널을 연결·저장하면 영상을 보면서 팬·틸트를 맞출 수 있습니다.</div>
          </div>
          <div class="cctv-ptz cctv-set-ptz">
            <h4>${cctvSettingsChannel}CH 팬·틸트</h4>
            <div class="cctv-ptz-pad">
              <span></span>
              <button type="button" data-ptz="up" title="위">▲</button>
              <span></span>
              <button type="button" data-ptz="left" title="왼쪽">◀</button>
              <button type="button" class="ptz-c" data-ptz="stop" title="정지">■</button>
              <button type="button" data-ptz="right" title="오른쪽">▶</button>
              <span></span>
              <button type="button" data-ptz="down" title="아래">▼</button>
              <span></span>
            </div>
            <div class="cctv-ptz-zoom">
              <button type="button" data-ptz="zoomin">줌+</button>
              <button type="button" data-ptz="zoomout">줌−</button>
              <button type="button" class="ptz-home" data-ptz="home" title="원래 위치로 이동">Home</button>
              <button type="button" data-ptz="set_home" title="지금 화면을 Home으로 저장">Home저장</button>
            </div>
            <div class="cctv-ptz-msg" id="cctvSetPtzMsg">누르고 있는 동안 이동 · Home저장으로 이 채널 원위치 등록</div>
          </div>
      </div>
      <form id="cctvConnectForm" class="cctv-form">
        <label>카메라 이름 <input name="name" value="${escapeAttr(cfg.name || ("카메라 " + cctvSettingsChannel))}" /></label>
        <label>카메라 IP <input name="host" value="${escapeAttr(cfg.host || "")}" placeholder="192.168.0.78" /></label>
        <label>연결 방식
          <select name="mode">
            <option value="auto" ${!cfg.mode || cfg.mode === "auto" ? "selected" : ""}>자동 (ONVIF→JPEG)</option>
            <option value="onvif" ${cfg.mode === "onvif" ? "selected" : ""}>ONVIF</option>
            <option value="jpeg" ${cfg.mode === "jpeg" ? "selected" : ""}>JPEG 스냅샷</option>
            <option value="rtsp" ${cfg.mode === "rtsp" ? "selected" : ""}>RTSP</option>
          </select>
        </label>
        <label class="check"><input name="use_https" type="checkbox" ${cfg.use_https !== false ? "checked" : ""}/> HTTPS 사용</label>
        <label>HTTP 포트 <input name="http_port" type="number" value="${escapeAttr(cfg.http_port || 80)}" /></label>
        <label>HTTPS 포트 <input name="https_port" type="number" value="${escapeAttr(cfg.https_port || 443)}" /></label>
        <label>RTSP 포트 <input name="rtsp_port" type="number" value="${escapeAttr(cfg.rtsp_port || 554)}" /></label>
        <label>아이디 <input name="user" value="${escapeAttr(cfg.user || "admin")}" autocomplete="username" /></label>
        <label>비밀번호 <input name="password" type="password" autocomplete="current-password" placeholder="${hasPw ? "저장됨 (변경 시에만 입력)" : "비밀번호"}" /></label>
        <label>스냅샷 경로 <input name="snapshot_path" value="${escapeAttr(cfg.snapshot_path || "/capture/ch1.jpg")}" /></label>
        <label>RTSP 경로 <input name="rtsp_path" value="${escapeAttr(cfg.rtsp_path || "/video1+audio1")}" /></label>
        <label>ONVIF Media <input name="onvif_media_path" value="${escapeAttr(cfg.onvif_media_path || "/onvif/media_service")}" /></label>
        <label>ONVIF PTZ <input name="onvif_ptz_path" value="${escapeAttr(cfg.onvif_ptz_path || "/onvif/PTZ")}" /></label>
        <label>ONVIF Profile <input name="onvif_profile" value="${escapeAttr(cfg.onvif_profile || "")}" placeholder="비우면 자동" /></label>
        <label>Home 프리셋 <input name="ptz_home_preset" value="${escapeAttr(cfg.ptz_home_preset || "")}" placeholder="Home저장 후 자동 입력" /></label>
        <label>보관 일수 <input name="retention_days" type="number" min="1" max="365" value="${escapeAttr(cfg.retention_days || 1)}" /> <small>1이면 하루가 지난 전체 영상과 이벤트 영상을 지웁니다.</small></label>
        <label class="check"><input name="continuous_record" type="checkbox" ${cfg.continuous_record !== false ? "checked" : ""}/> 전체 영상 계속 녹화 (연속 저장 · 정시마다 파일 구분)</label>
        <div class="span2 meta">다른 PC에서 녹화가 안 되면 OMS 폴더에 <b>ffmpeg.exe</b>를 같이 두세요. (라이브 보기만 되고 저장이 안 되는 경우가 많습니다)</div>
        <label>이벤트 녹화(초) <input name="event_record_sec" type="number" min="5" max="300" value="${escapeAttr(cfg.event_record_sec || 30)}" /></label>
        <label class="check"><input name="monitor_inside" type="checkbox" ${cfg.monitor_inside !== false ? "checked" : ""}/> 무기고 내부 접근 감시</label>
        <label class="check"><input name="monitor_outside" type="checkbox" ${cfg.monitor_outside !== false ? "checked" : ""}/> 무기고 외부 접근 감시</label>
        <label class="check"><input name="auto_event_on_emergency" type="checkbox" ${cfg.auto_event_on_emergency !== false ? "checked" : ""}/> 비상개폐 시 자동 녹화</label>
        <label class="check"><input name="auto_event_on_unauthorized" type="checkbox" ${cfg.auto_event_on_unauthorized !== false ? "checked" : ""}/> 비인가 접근 시 자동 녹화</label>
        <label class="check"><input name="auto_event_on_rfid_emergency" type="checkbox" ${rfidRecOn ? "checked" : ""}/> RFID 태깅 비상 시 자동 녹화</label>
        <label class="check"><input name="auto_ptz_on_event" type="checkbox" ${cfg.auto_ptz_on_event !== false ? "checked" : ""}/> 이벤트 시 상황별 PTZ 이동</label>
        <label class="check"><input name="auto_ptz_on_person" type="checkbox" ${cfg.auto_ptz_on_person !== false ? "checked" : ""}/> CCTV 사람 인지 시 PTZ(상황4)</label>
        <label class="check"><input name="auto_light_on_person" type="checkbox" ${cfg.auto_light_on_person !== false ? "checked" : ""}/> CCTV 사람 인지 시 황색 경광등</label>
        <label class="check"><input name="person_ai_enabled" type="checkbox" ${cfg.person_ai_enabled !== false ? "checked" : ""}/> PC 온디바이스 AI 사람 감지 (MediaPipe)</label>
        <div class="span2 meta" id="cctvPersonAiStatus">AI 상태 확인 중…</div>
        <label class="check"><input name="enabled" type="checkbox" ${cfg.enabled ? "checked" : ""}/> 이 채널 사용</label>
        <div class="span2 cctv-howto">
          <b>${cctvSettingsChannel}CH 팬·틸트</b><br/>
          1. 위에서 영상을 보면서 방향 버튼을 누른 채로 각도를 맞춥니다.<br/>
          2. 보관함을 추가하면 비상개폐, 비인가 접근, RFID 태깅 비상, 보관·불출 위치가 각각 하나씩 늘어납니다. 카메라로 그 보관함을 비춘 뒤 <b>지금위치저장</b>을 누르세요.<br/>
          3. 접속 정보를 바꾼 뒤에는 <b>연결·저장</b>을 눌러야 그 카메라로 팬·틸트가 동작합니다.<br/>
          4. PC 온디바이스 AI가 스냅샷에서 사람을 보면(표 4) 로그·황색 경광등·상황4 PTZ가 동작합니다. 수동 테스트는 아래 버튼을 사용하세요.
        </div>
        <div class="span2 cctv-event-map">
          <b>보관함별 팬틸트 · 카메라 1대</b>
          <div class="cctv-map-grid">
            ${lockerBinds || `<span>등록된 보관함이 없습니다.</span>`}
            <label>네트워크 스위치 ${mapSelect("offline", 3)}</label>
            <label>CCTV 사람 인지 ${mapSelect("person", 4)}</label>
            <label>수동 녹화 ${mapSelect("manual", 5)}</label>
            <label>RFID 태깅 비상 ${mapSelect("rfid_emergency", 6)}</label>
          </div>
          <div class="win-actions" style="margin-top:8px;">
            <button type="button" class="gb" id="cctvPersonTest">사람 인지 테스트</button>
          </div>
        </div>
        <div class="cctv-preset-grid">${presetInputs}</div>
        <div class="cctv-connect-msg" id="cctvConnectMsg">한 번 연결·저장하면 다음부터 자동으로 영상이 표시됩니다.</div>
        <pre class="cctv-diag" id="cctvDiagBox" hidden></pre>
        <div class="win-actions">
          <button type="submit" class="gb primary" id="cctvConnectBtn">${cctvSettingsChannel}CH 연결·저장</button>
          <button type="button" class="gb" id="cctvDiagnoseBtn">진단</button>
          <button type="button" class="gb" id="cctvLoadPresets">카메라 프리셋 불러오기</button>
          <button type="button" class="gb" id="cctvPurge">30일 이전 정리</button>
          <button type="button" class="gb" id="cctvSetClose2">닫기</button>
        </div>
      </form>
    </div>`;
  const close = () => {
    stopCctvSetPreview();
    layer.classList.remove("is-min", "is-max");
    layer.hidden = true;
    layer.innerHTML = "";
  };
  const syncSetChrome = () => {
    const maxBtn = $("cctvSetMax");
    if (maxBtn) maxBtn.classList.toggle("is-restore", layer.classList.contains("is-max"));
  };
  $("cctvSetClose").onclick = close;
  $("cctvSetClose2").onclick = close;
  $("cctvSetMin").onclick = () => {
    if (layer.classList.contains("is-min")) layer.classList.remove("is-min");
    else {
      layer.classList.remove("is-max");
      layer.classList.add("is-min");
    }
    syncSetChrome();
  };
  $("cctvSetMax").onclick = () => {
    layer.classList.remove("is-min");
    layer.classList.toggle("is-max");
    syncSetChrome();
  };
  syncSetChrome();
  layer.querySelectorAll("[data-cctv-ch]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const next = Number(btn.getAttribute("data-cctv-ch"));
      if (!next || next === cctvSettingsChannel) return;
      stashCctvSettingsForm();
      cctvSettingsChannel = next;
      paintCctvSettingsForm();
    });
  });
  $("cctvDiagnoseBtn").onclick = () => runCctvDiagnose();
  const personTest = $("cctvPersonTest");
  if (personTest) {
    personTest.onclick = async () => {
      const result = await post("/api/cctv/person", {
        message: "무기고에 사람이 출입했습니다.",
        source: "manual-test",
      });
      toast(result.ok ? "사람 인지 이벤트 반영 (로그·황색등)" : ((result.data && result.data.error) || "실패"));
    };
  }
  $("cctvPurge").onclick = async () => {
    const result = await post("/api/cctv/purge", {});
    toast(result.ok ? `정리 완료 (${(result.data && result.data.removed) || 0}건)` : "정리 실패");
  };
  $("cctvLoadPresets").onclick = async () => {
    const result = await post("/api/cctv/ptz", { action: "presets", channel: cctvSettingsChannel });
    if (!result.ok) {
      toast((result.data && result.data.detail) || "프리셋을 불러오지 못했습니다. (고정형 카메라면 PTZ 없음)");
      return;
    }
    const list = (result.data && result.data.presets) || [];
    if (!list.length) { toast("카메라에 저장된 프리셋이 없습니다."); return; }
    toast(`프리셋 ${list.length}개: ${list.map((p) => p.token).slice(0, 8).join(", ")}`);
    const msg = $("cctvConnectMsg");
    if (msg) {
      msg.className = "cctv-connect-msg ok";
      msg.textContent = "카메라 프리셋: " + list.map((p) => `${p.name || p.token}=${p.token}`).join(" · ");
    }
  };
  layer.querySelectorAll("[data-bind-locker]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const lockerId = btn.getAttribute("data-bind-locker") || "";
      const kind = btn.getAttribute("data-bind-kind") || "emergency";
      btn.disabled = true;
      try {
        const result = await post("/api/cctv/ptz", {
          action: "bind_locker",
          locker_id: lockerId,
          kind,
          channel: cctvSettingsChannel,
        });
        if (!result.ok) {
          toast((result.data && result.data.detail) || "보관함 위치 저장 실패");
          return;
        }
        const token = (result.data && result.data.token) || "";
        const input = layer.querySelector(`[data-lk-id="${lockerId}"][data-lk-kind="${kind}"]`);
        if (input) input.value = token;
        toast((result.data && result.data.detail) || "보관함 위치 등록 완료");
      } finally {
        btn.disabled = false;
      }
    });
  });
  layer.querySelectorAll("[data-bind-sit]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const sit = Number(btn.getAttribute("data-bind-sit"));
      btn.disabled = true;
      try {
        const result = await post("/api/cctv/ptz", { action: "bind_situation", situation: sit, channel: cctvSettingsChannel });
        if (!result.ok) {
          toast((result.data && result.data.detail) || "상황 위치 저장 실패");
          return;
        }
        const token = (result.data && result.data.token) || "";
        const input = layer.querySelector(`[name="preset_${sit}"]`);
        if (input) input.value = token;
        toast(result.data.detail || `상황 ${sit} 등록 완료`);
      } finally {
        btn.disabled = false;
      }
    });
  });
  $("cctvConnectForm").onsubmit = async (ev) => {
    ev.preventDefault();
    await connectCctvFromForm(true);
  };
  bindCctvPtzControls(layer, {
    channel: () => cctvSettingsChannel,
    msgId: "cctvSetPtzMsg",
  });
  startCctvSetPreview();
}

async function refreshPersonAiStatus() {
  const el = $("cctvPersonAiStatus");
  if (!el) return;
  try {
    const result = await api("/api/cctv/person-ai");
    const d = (result && result.data) || {};
    if (!result.ok) {
      el.textContent = "AI 상태를 가져오지 못했습니다.";
      return;
    }
    if (d.error) {
      el.textContent = `AI: 오류 — ${d.error}`;
      return;
    }
    const score = typeof d.last_score === "number" ? `${Math.round(d.last_score * 100)}%` : "-";
    el.textContent = d.running
      ? `AI: 동작 중 (${d.backend || "-"}) · 최근 점수 ${score} · 감지 ${d.hits || 0}회`
      : "AI: 대기/중지 (모델·_deps 확인)";
  } catch (_) {
    el.textContent = "AI 상태 확인 실패";
  }
}

async function openCctvSettingsPanel() {
  const layer = $("cctvSettings");
  if (!layer) return;
  let data = {};
  try {
    const result = await api("/api/cctv/status");
    if (result.ok && result.data) data = result.data;
  } catch (_) {}
  cctvChannelCfgs = normalizeCctvChannels(data);
  if (cctvSettingsChannel < 1 || cctvSettingsChannel > 4) cctvSettingsChannel = 1;
  layer.hidden = false;
  paintCctvSettingsForm();
  refreshPersonAiStatus();
}

function openModal(html, mode) {
  const extra = mode === true ? "narrow" : (mode || "");
  const modal = $("modal");
  modal.className = "modal";
  modal.innerHTML = `<div class="win ${extra}">${html}</div>`;
  modal.hidden = false;
}

function closeModal() {
  stopCctvTimers();
  const modalEl = $("modal");
  if (modalEl) modalEl.classList.remove("cctv-max", "history-max", "history-min", "patlite-max", "patlite-min");
  state.popup = null;
  const modal = $("modal");
  modal.hidden = true;
  modal.className = "modal";
  modal.innerHTML = "";
  refresh.seen = "";
  if (state.data) paintFrame();
}

function placeAlertLayer() {
  const layer = $("alert");
  if (!layer) return layer;
  const nativeFs = document.fullscreenElement;
  const full = $("fullScreen");
  const host = nativeFs || (full && !full.hidden ? full : document.body);
  if (layer.parentElement !== host) host.appendChild(layer);
  // Cover OMS 총기현황(#fullScreen) as well as native fullscreen hosts.
  if (host !== document.body) {
    layer.style.position = "absolute";
    layer.style.zIndex = "2147483000";
  } else {
    layer.style.position = "";
    layer.style.zIndex = "";
  }
  return layer;
}

function alertBox(message, onOk) {
  const layer = placeAlertLayer();
  layer.innerHTML = `<div class="alert-box"><p>${escapeAttr(message)}</p><button type="button" class="gb" id="alertOk">확인</button></div>`;
  layer.hidden = false;
  $("alertOk").onclick = () => {
    layer.hidden = true;
    layer.innerHTML = "";
    if (layer.parentElement !== document.body) document.body.appendChild(layer);
    if (onOk) onOk();
  };
}

function setAlarmBorder(on, meta) {
  const root = document.documentElement;
  const oms = document.querySelector(".oms");
  const full = $("fullScreen");
  const cctvWin = document.querySelector(".win.cctv");
  const comboStage = document.querySelector(".combo-cctv-stage");
  const liveCell = document.querySelector(".cctv-cell.active") || document.querySelector(".cctv-cell");
  const targets = [oms, full, cctvWin, comboStage, liveCell].filter(Boolean);
  const person = !!(on && meta && meta.kind === "person");
  targets.forEach((el) => {
    el.classList.toggle("alarm-flash", !!on && !person);
    el.classList.toggle("person-flash", person);
  });
  root.classList.toggle("alarm-active", !!on && !person);
  root.classList.toggle("person-active", person);
  if (on && meta) {
    const label = document.querySelector(".cctv-cell.active .cctv-cell-label");
    if (label) {
      const kindLabel = {
        emergency: "비상개폐",
        unauthorized: "미인가",
        offline: "단절",
        rfid_emergency: "RFID태깅비상",
        unreturned: "미반납",
        person: "출입",
      }[meta.kind] || (meta.kind || "알람");
      const slotBit = meta.slot != null ? ` · #${meta.slot}` : "";
      label.textContent = `${kindLabel}${slotBit}`;
    }
  }
}

function isWarnAlertVisible(id) {
  const layer = $("alert");
  return !!(
    warnShowing
    && warnQueue[0]
    && Number(warnQueue[0].id) === Number(id)
    && layer
    && !layer.hidden
    && layer.querySelector("#alertOk")
  );
}

function hideWarnLayer() {
  const layer = $("alert");
  if (!layer) return;
  layer.hidden = true;
  layer.style.display = "";
  layer.innerHTML = "";
  if (layer.parentElement !== document.body) document.body.appendChild(layer);
  layer.style.position = "";
  layer.style.zIndex = "";
}

function dropPersonWarnsFromQueue() {
  const head = warnQueue[0];
  const droppedHead = !!(head && head.kind === "person");
  warnQueue = warnQueue.filter((row) => row.kind !== "person");
  if (droppedHead) {
    warnShowing = false;
    hideWarnLayer();
  }
  if (!warnQueue.length) setAlarmBorder(false);
  else showNextWarn();
}

function syncWarnAlerts(alerts) {
  const serverItems = (alerts || []).filter((item) => item && item.kind !== "unreturned");
  const serverIds = new Set(
    serverItems.map((item) => Number(item && item.id)).filter((id) => id > 0)
  );
  // Drop client copies the server already cleared (출입중 / ack / expiry).
  const head = warnQueue[0];
  const headId = head ? Number(head.id) : 0;
  warnQueue = warnQueue.filter((row) => {
    const id = Number(row.id);
    if (id < 0) return true; // local-only warns
    return serverIds.has(id);
  });
  if (warnShowing && headId > 0 && !serverIds.has(headId)) {
    warnShowing = false;
    hideWarnLayer();
  }
  if (warnShowing) {
    const layer = $("alert");
    if (!layer || layer.hidden || !layer.querySelector("#alertOk")) warnShowing = false;
  }
  enqueueWarnAlerts(serverItems);
  if (!warnQueue.length) setAlarmBorder(false);
}

function enqueueWarnAlerts(alerts) {
  (alerts || []).forEach((item) => {
    if (item && item.kind === "unreturned") return;
    const id = Number(item && item.id);
    if (!id) return;
    if (isWarnAlertVisible(id)) return;
    if (warnQueue.some((row) => Number(row.id) === id)) return;
    const message = (item && item.message) || "인가되지 않은 접근입니다!";
    if (warnQueue.some((row) => row.message === message)) return;
    seenWarnIds.add(id);
    const row = {
      id,
      message,
      kind: (item && item.kind) || "",
      slot: item && item.slot != null ? item.slot : null,
      locker: (item && item.locker) || "",
    };
    // Person alerts jump the queue — OMS popup must appear with yellow tower.
    if (row.kind === "person") warnQueue.unshift(row);
    else warnQueue.push(row);
  });
  showNextWarn();
}

function showNextWarn() {
  const layerNow = $("alert");
  // Recover stuck state: flag says showing but popup was overwritten/hidden (ESC 등).
  if (warnShowing) {
    const head = warnQueue[0];
    const personWaiting = warnQueue.some((row) => row.kind === "person");
    if (!layerNow || layerNow.hidden || !layerNow.querySelector("#alertOk")) {
      warnShowing = false;
    } else if (personWaiting && head && head.kind !== "person") {
      // Prempt non-person dialog so person OMS popup can appear.
      warnShowing = false;
    } else {
      placeAlertLayer();
      layerNow.hidden = false;
      layerNow.style.display = "grid";
      return;
    }
  }
  while (warnQueue.length && warnQueue[0].kind === "unreturned") warnQueue.shift();
  // Keep person alerts at the front.
  const personIdx = warnQueue.findIndex((row) => row.kind === "person");
  if (personIdx > 0) {
    const [personRow] = warnQueue.splice(personIdx, 1);
    warnQueue.unshift(personRow);
  }
  if (!warnQueue.length) {
    setAlarmBorder(false);
    return;
  }
  warnShowing = true;
  const item = warnQueue[0];
  setAlarmBorder(true, item);
  const layer = placeAlertLayer();
  if (!layer) {
    warnShowing = false;
    setAlarmBorder(false);
    return;
  }
  const isPerson = item.kind === "person";
  const rawMessage = String(item.message || "").trim();
  const lines = rawMessage.split("\n").map((part) => part.trim()).filter(Boolean);
  const fallback = isPerson ? "무기고에 사람이 출입했습니다." : "인가되지 않은 접근입니다!";
  const warnHtml = lines.length
    ? lines.map((line) => `<span class="warn-line">${escapeAttr(line)}</span>`).join("")
    : `<span class="warn-line">${escapeAttr(fallback)}</span>`;
  // Long RFID / mismatch text needs a wider box; short classic alarms stay compact.
  const longest = Math.max(0, ...(lines.length ? lines.map((line) => line.length) : [fallback.length]));
  const isLong = !isPerson && (longest >= 28 || rawMessage.length >= 36 || lines.length >= 2);
  const boxClass = [
    "alert-box",
    "alert-box--warn",
    isPerson ? "alert-box--person" : "",
    isLong ? "alert-box--long" : "",
  ].filter(Boolean).join(" ");
  const insideChecked = !!(state.data && state.data.armory_inside);
  const insideHtml = isPerson
    ? `<label class="alert-inside${insideChecked ? " on" : ""}">
        <input type="checkbox" id="alertArmoryInside" ${insideChecked ? "checked" : ""} />
        <span>무기고에 출입중입니다</span>
      </label>`
    : "";
  layer.hidden = false;
  layer.style.display = "grid";
  layer.innerHTML = `<div class="${boxClass}"><p class="warn-text">${warnHtml}</p>${insideHtml}<button type="button" class="gb" id="alertOk">확인</button></div>`;
  if (isPerson) toast("사람 출입 감지 — 확인을 눌러 주세요.");
  const insideBox = $("alertArmoryInside");
  if (insideBox) {
    insideBox.onchange = () => {
      const wrap = insideBox.closest(".alert-inside");
      if (wrap) wrap.classList.toggle("on", !!insideBox.checked);
    };
  }
  const ok = $("alertOk");
  if (!ok) {
    warnShowing = false;
    setAlarmBorder(false);
    return;
  }
  ok.onclick = async () => {
    warnQueue.shift();
    warnShowing = false;
    hideWarnLayer();
    // Clear border immediately so ack/network wait never leaves "테두리만" 상태.
    if (!warnQueue.length) setAlarmBorder(false);
    else setAlarmBorder(true, warnQueue[0]);
    // Ack first so the server still sees the person alert and can Home when idle.
    // (출입중을 먼저 보내면 서버가 person을 지워 ack가 no-op이 되던 버그가 있었음.)
    try {
      await post("/api/alerts/ack", { ids: [item.id] });
    } catch (_) {}
    if (isPerson) {
      const wantInside = !!(insideBox && insideBox.checked);
      try {
        const insideResult = await post("/api/armory-inside", { armory_inside: wantInside });
        if (insideResult.ok && state.data) {
          state.data.armory_inside = wantInside;
          refresh.seen = "";
        }
        if (wantInside) {
          toast("출입중 — 사람감지·황색 경광등 알림을 끕니다. (경광등에서 알림켜기로 다시 켤 수 있습니다)");
        } else {
          toast("출입중 해제 — 사람감지·황색 알림을 다시 켭니다.");
        }
      } catch (_) {}
      try { closeModal(); } catch (_) {}
    }
    showNextWarn();
  };
}

function askBox(message, yesLabel, onYes) {
  const layer = placeAlertLayer();
  layer.innerHTML = `<div class="alert-box"><p>${message}</p><div class="win-actions"><button type="button" class="gb" id="askYes">${yesLabel}</button><button type="button" class="gb" id="askNo">취소</button></div></div>`;
  layer.hidden = false;
  const restore = () => {
    layer.hidden = true;
    if (layer.parentElement !== document.body) document.body.appendChild(layer);
  };
  $("askYes").onclick = () => { restore(); onYes(); };
  $("askNo").onclick = () => { restore(); };
}

function formValues(form) {
  return Object.fromEntries(new FormData(form).entries());
}

function dateText(date) {
  const week = ["일", "월", "화", "수", "목", "금", "토"];
  const p = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}년 ${p(date.getMonth() + 1)}월 ${p(date.getDate())}일 (${week[date.getDay()]}) ${p(date.getHours())} : ${p(date.getMinutes())}`;
}

function fsTriNums(rifleVal, pistolVal, mode) {
  if (mode && mode.split) {
    return `<div class="fs-tri-nums">
      <span><em>소총</em><b>${rifleVal}</b></span>
      <span><em>권총</em><b>${pistolVal}</b></span>
      <span><em>합계</em><b>${rifleVal + pistolVal}</b></span>
    </div>`;
  }
  const value = mode && mode.pistol && !mode.rifle ? pistolVal : rifleVal;
  return `<div class="fs-tri-nums fs-tri-nums--one"><span><b>${value}</b></span></div>`;
}

function fsSignalState(data) {
  /** Match Patlite priority: red(alarm) > green(ops) > yellow(person) > off. */
  const pl = (data && data.patlite) || {};
  const alerts = Array.isArray(data && data.alerts) ? data.alerts : [];
  const redAlert = alerts.some((row) => ["emergency", "unauthorized", "offline", "rfid_emergency"].includes(String(row.kind || "")));
  const yellowAlert = alerts.some((row) => String(row.kind || "") === "person");
  const mode = String(pl.mode || "").toLowerCase();
  if (pl.alarm || mode === "alarm" || redAlert) return "red";
  if (pl.ops || mode === "ops") return "green";
  if (pl.yellow || mode === "yellow" || yellowAlert) return "yellow";
  return "off";
}

function fullScreenHtml(data, opts) {
  const embed = !!(opts && opts.embed);
  const fleet = fleetOf(data);
  const mode = {
    rifle: fleet.rifle.lockers > 0,
    pistol: fleet.pistol.lockers > 0,
  };
  mode.split = mode.rifle && mode.pistol;
  const heads = fleet.names.map((name) => `
    <div>
      <span>${escapeAttr(purposeTitle(data, name))}</span>
      ${fsTriNums(fleet.riflePurposes[name] || 0, fleet.pistolPurposes[name] || 0, mode)}
    </div>`).join("");
  const signal = fsSignalState(data);
  const signalOn = signal !== "off";
  const signalTitle = signal === "red"
    ? "비상상황"
    : (signal === "yellow" ? "사람 감지" : (signal === "green" ? "정상 불출·보관" : "대기"));
  const stored = mode.split ? fleet.total.stored : (mode.pistol ? fleet.pistol.stored : fleet.rifle.stored);
  const issued = mode.split ? fleet.total.issued : (mode.pistol ? fleet.pistol.issued : fleet.rifle.issued);
  const kindStored = mode.split ? `
            <div class="fs-kind-row">
              <div class="fs-kind-card"><h3>소총</h3><b>${fleet.rifle.stored}</b></div>
              <div class="fs-kind-card"><h3>권총</h3><b>${fleet.pistol.stored}</b></div>
            </div>` : "";
  const kindIssued = mode.split ? `
            <div class="fs-kind-row">
              <div class="fs-kind-card"><h3>소총</h3><b>${fleet.rifle.issued}</b></div>
              <div class="fs-kind-card"><h3>권총</h3><b>${fleet.pistol.issued}</b></div>
            </div>` : "";
  const closeBtn = embed
    ? ""
    : `<button type="button" class="gb fs-close no-print" id="closeFull">닫기 (Ctrl + M)</button>`;
  return `
    <div class="fs${embed ? " fs--embed" : ""}">
      <div class="fs-top">
        <img class="fs-logo" src="/assets/zellootec.png" alt="ZELLOOTEC" />
        <h2>총기 보관현황</h2>
        <i class="fs-dot fs-dot--${escapeAttr(signal)}${signalOn ? " is-on" : ""}" title="${escapeAttr(signalTitle)}" aria-label="${escapeAttr(signalTitle)}"></i>
      </div>
      <div class="fs-dash">
        <div class="fs-side">
          <div class="fs-line"><span class="fs-pill">등록</span>${fsTriNums(fleet.rifle.registered, fleet.pistol.registered, mode)}</div>
        </div>
        <div class="fs-mid">
          <div class="fs-hero">
            <span>보관 수량</span>
            <strong>${stored}</strong>${kindStored}
          </div>
          <div class="fs-hero fs-hero--out">
            <span>불출 수량</span>
            <strong>${issued}</strong>${kindIssued}
          </div>
        </div>
        <div class="fs-side right">
          <div class="fs-line">${fsTriNums(fleet.rifle.unpaid, fleet.pistol.unpaid, mode)}<span class="fs-pill">미지급</span></div>
        </div>
      </div>
      <div class="fs-date" id="fullDate">${dateText(new Date())}</div>
      <hr class="fs-rule" />
      <div class="fs-issue">${heads}</div>
      <div class="fs-legend" aria-label="경광등 신호 안내">
        <span class="fs-legend-item"><i class="fs-legend-dot red"></i>적색 · 비상상황</span>
        <span class="fs-legend-item"><i class="fs-legend-dot yellow"></i>황색 · 사람감지</span>
        <span class="fs-legend-item"><i class="fs-legend-dot green"></i>녹색 · 정상불출·보관</span>
      </div>
      ${closeBtn}
    </div>`;
}

function openFullScreen() {
  if (!state.data) return;
  const layer = $("fullScreen");
  layer.innerHTML = fullScreenHtml(state.data);
  layer.hidden = false;
  $("closeFull").onclick = closeFullScreen;
}

function closeFullScreen() {
  const layer = $("fullScreen");
  layer.hidden = true;
  layer.innerHTML = "";
  if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
}

function loginModal() {
  state.popup = loginModal;
  openModal(`
    <h3 class="win-title">로그인</h3>
    <div class="win-body">
      <form id="loginForm">
        <div class="login-line"><span>USER ID :</span><input name="login_id" maxlength="10" /></div>
        <div class="login-line"><span>PASSWORD :</span><input name="password" type="password" /></div>
        <div class="win-actions">
          <button class="gb" type="submit" name="how" value="save">저장</button>
          <button class="gb" type="submit" name="how" value="login">로그인</button>
          <button class="gb" type="button" id="closeLogin">취소</button>
        </div>
      </form>
    </div>`, true);
  $("closeLogin").onclick = closeModal;
  $("loginForm").onsubmit = async (ev) => {
    ev.preventDefault();
    const body = formValues(ev.target);
    const result = await post("/api/login", body);
    if (result.ok) {
      closeModal();
      await refresh(false);
      return;
    }
    if (result.data.code === "empty") {
      askBox("등록된 ID가 없습니다.<br>최초 마스터 관리자를 등록하시겠습니까?", "YES", () => bootstrapForm());
      return;
    }
    alertBox(result.data.code === "missing" ? "ID가 존재하지 않습니다." : "ID 또는 Password가 일치하지 않습니다.");
  };
}

function bootstrapForm() {
  state.popup = bootstrapForm;
  openModal(`
    <h3 class="win-title">관리자정보</h3>
    <form id="bootForm">
      <div class="win-body">
        <table class="form-table">
          <tr><th>관리자</th><td>마스터1</td></tr>
          <tr><th>계 급</th><td><input name="rank" maxlength="4" /></td></tr>
          <tr><th>군 번</th><td><input name="sn" maxlength="10" /></td></tr>
          <tr><th>성 명</th><td><input name="name" maxlength="5" /></td></tr>
          <tr><th>User ID</th><td><input name="login_id" maxlength="10" /></td></tr>
          <tr><th>New Password</th><td><input name="password" type="password" /></td></tr>
          <tr><th>Check Password</th><td class="check-row"><input name="password2" type="password" /><button type="button" class="gb" id="bootCheck">Check</button></td></tr>
        </table>
        <div class="win-actions">
          <button class="gb" type="button" disabled>삭제</button>
          <button class="gb" type="submit">저장</button>
          <button class="gb" type="button" id="closeBoot">취소</button>
        </div>
      </div>
    </form>`);
  $("closeBoot").onclick = closeModal;
  $("bootCheck").onclick = () => checkPasswords($("bootForm"));
  $("bootForm").onsubmit = async (ev) => {
    ev.preventDefault();
    const body = formValues(ev.target);
    if ((body.password || "") !== (body.password2 || "")) {
      alertBox("Password가 일치하지 않습니다.");
      return;
    }
    const result = await post("/api/bootstrap", body);
    if (!result.ok) {
      alertBox(result.data.error || "등록하지 못했습니다.");
      return;
    }
    closeModal();
    toast("마스터 1 관리자를 등록했습니다.");
    await refresh(false);
  };
}

function checkPasswords(form) {
  const body = formValues(form);
  if ((body.password || "") === (body.password2 || "") && body.password) alertBox("Password가 일치합니다.");
  else alertBox("Password가 일치하지 않습니다.");
}

function openGroups() {
  state.popup = openGroups;
  const data = state.data;
  const masterOn = (data.masters || []).some((row) => row.has_login);
  const subOn = (data.subs || []).some((row) => row.person);
  const opOn = data.operator && data.operator.has_login;
  openModal(`
    <h3 class="win-title">관리자정보</h3>
    <div class="win-body">
      <div class="picks">
        <button type="button" class="pick ${masterOn ? "on" : ""}" data-group="master">마스터</button>
        <button type="button" class="pick ${subOn ? "on" : ""}" data-group="sub">서브</button>
        <button type="button" class="pick ${opOn ? "on" : ""}" data-group="operator">운영자</button>
      </div>
      <div class="win-actions"><button type="button" class="gb" id="closeGroups">취소</button></div>
    </div>`, true);
  $("closeGroups").onclick = closeModal;
  document.querySelectorAll("[data-group]").forEach((btn) => {
    btn.onclick = () => {
      const group = btn.dataset.group;
      if (group === "master") openMasterPicks();
      else if (group === "sub") openSubDialog(1);
      else openPerson("operator", 0);
    };
  });
}

function openMasterPicks() {
  state.popup = openMasterPicks;
  const data = state.data;
  const mine = data.who && data.who.role === "master" ? data.who.slot : 0;
  const buttons = data.masters.map((row) => {
    const title = row.person && row.person.name ? `${escapeAttr(row.person.rank || "")} ${escapeAttr(row.person.name)}` : `마스터<br>${row.slot}`;
    const login = mine === row.slot ? `<small>로그인</small>` : "";
    const disabled = row.has_login && mine !== row.slot;
    return `<button type="button" class="pick ${mine === row.slot ? "on" : ""}" data-slot="${row.slot}" ${disabled ? "disabled" : ""}>${title}${login}</button>`;
  }).join("");
  openModal(`
    <h3 class="win-title">관리자정보</h3>
    <div class="win-body">
      <div class="picks">${buttons}</div>
      <div class="win-actions"><button type="button" class="gb" id="closeMasters">취소</button></div>
    </div>`);
  $("closeMasters").onclick = () => openGroups();
  document.querySelectorAll("[data-slot]").forEach((btn) => {
    btn.onclick = () => openPerson("master", Number(btn.dataset.slot));
  });
}

function openPerson(kind, slot) {
  state.selected = slot || 1;
  state.popup = () => openPerson(kind, slot);
  const data = state.data;
  const row = kind === "operator" ? data.operator : (kind === "master" ? data.masters : data.subs).find((item) => item.slot === slot);
  const person = (row && row.person) || {};
  const locked = !!(row && row.locked);
  const title = kind === "operator" ? "운영자" : `${kind === "master" ? "마스터" : "서브"}${slot}`;
  const loginRows = kind === "sub" ? "" : `
    <tr><th>User ID</th><td><input name="login_id" maxlength="10" value="${escapeAttr(person.login_id || "")}" ${locked ? "readonly" : ""} /></td></tr>
    <tr><th>New Password</th><td><input name="password" type="password" ${locked ? "readonly" : ""} /></td></tr>
    <tr><th>Check Password</th><td class="check-row"><input name="password2" type="password" ${locked ? "readonly" : ""} /><button type="button" class="gb" id="pwCheck">Check</button></td></tr>`;
  openModal(`
    <h3 class="win-title">관리자정보</h3>
    <form id="personForm">
      <div class="win-body">
        <input type="hidden" name="kind" value="${kind}" />
        <input type="hidden" name="slot" value="${kind === "operator" ? 0 : slot}" />
        <table class="form-table">
          <tr><th>관리자</th><td>${title}</td></tr>
          <tr><th>계 급</th><td><input name="rank" maxlength="4" value="${escapeAttr(person.rank || "")}" ${locked ? "readonly" : ""} /></td></tr>
          <tr><th>군 번</th><td><input name="sn" maxlength="10" value="${escapeAttr(person.sn || "")}" ${locked ? "readonly" : ""} /></td></tr>
          <tr><th>성 명</th><td><input name="name" maxlength="5" value="${escapeAttr(person.name || "")}" ${locked ? "readonly" : ""} /></td></tr>
          ${loginRows}
        </table>
        <div class="win-actions">
          <button class="gb" type="button" id="deletePerson" ${locked || !(row && (row.person || row.has_login)) ? "disabled" : ""}>삭제</button>
          <button class="gb" type="submit" ${locked ? "disabled" : ""}>저장</button>
          <button class="gb" type="button" id="closePerson">취소</button>
        </div>
      </div>
    </form>`);
  $("closePerson").onclick = () => (kind === "master" ? openMasterPicks() : openGroups());
  const check = $("pwCheck");
  if (check) check.onclick = () => checkPasswords($("personForm"));
  $("personForm").onsubmit = async (ev) => {
    ev.preventDefault();
    const body = formValues(ev.target);
    if (kind !== "sub" && (body.password || "") !== (body.password2 || "")) {
      alertBox("Password가 일치하지 않습니다.");
      return;
    }
    const result = await post("/api/person", body);
    if (!result.ok) {
      alertBox(result.data.error || "저장하지 못했습니다.");
      return;
    }
    toast("저장했습니다.");
    await refresh(false);
  };
  const del = $("deletePerson");
  if (del) del.onclick = () => confirmDelete("/api/person/delete", { kind, slot: kind === "operator" ? 0 : slot });
}

function openSubDialog(slot) {
  state.selected = slot;
  state.popup = () => openSubDialog(state.selected);
  const data = state.data;
  const row = data.subs.find((item) => item.slot === state.selected) || data.subs[0];
  const person = (row && row.person) || {};
  const locked = !!(row && row.locked);
  const list = data.subs.map((item) => {
    const p = item.person || {};
    return `<tr class="${item.slot === row.slot ? "on" : ""}" data-slot="${item.slot}"><td>${item.slot}</td><td>${escapeAttr(p.rank || "")}</td><td>${escapeAttr(p.sn || "")}</td><td>${escapeAttr(p.name || "")}</td><td></td></tr>`;
  }).join("");
  openModal(`
    <h3 class="win-title">관리자정보</h3>
    <form id="personForm">
      <div class="win-body">
        <input type="hidden" name="kind" value="sub" />
        <input type="hidden" name="slot" value="${row.slot}" />
        <table class="form-table">
          <tr><th>관리자</th><td>서브${row.slot}</td></tr>
          <tr><th>계 급</th><td><input name="rank" maxlength="4" value="${escapeAttr(person.rank || "")}" ${locked ? "readonly" : ""} /></td></tr>
          <tr><th>군 번</th><td><input name="sn" maxlength="10" value="${escapeAttr(person.sn || "")}" ${locked ? "readonly" : ""} /></td></tr>
          <tr><th>성 명</th><td><input name="name" maxlength="5" value="${escapeAttr(person.name || "")}" ${locked ? "readonly" : ""} /></td></tr>
        </table>
        <div class="win-actions">
          <button class="gb" type="button" id="deletePerson" ${locked || !row.person ? "disabled" : ""}>삭제</button>
          <button class="gb" type="submit" ${locked ? "disabled" : ""}>저장</button>
          <button class="gb" type="button" id="closePerson">취소</button>
        </div>
        <div class="scroll"><table class="grid-table"><thead><tr><th>No.</th><th>계 급</th><th>군 번</th><th>성 명</th><th>구 분</th></tr></thead><tbody>${list}</tbody></table></div>
      </div>
    </form>`);
  $("closePerson").onclick = () => openGroups();
  document.querySelectorAll("[data-slot]").forEach((tr) => {
    tr.onclick = () => openSubDialog(Number(tr.dataset.slot));
  });
  $("personForm").onsubmit = async (ev) => {
    ev.preventDefault();
    const result = await post("/api/person", formValues(ev.target));
    if (!result.ok) {
      alertBox(result.data.error || "저장하지 못했습니다.");
      return;
    }
    toast("저장했습니다.");
    await refresh(false);
  };
  $("deletePerson").onclick = () => confirmDelete("/api/person/delete", { kind: "sub", slot: row.slot });
}

function gunModels(data, current) {
  const models = [];
  (data.catalog || []).forEach((item) => {
    if (item.model && !models.includes(item.model)) models.push(item.model);
  });
  if (current && !models.includes(current)) models.unshift(current);
  return models;
}

function openUsers() {
  const data = state.data;
  if (!data || !data.locker) {
    toast("보관함을 먼저 등록하세요.");
    return;
  }
  state.popup = () => openUsers();
  const row = data.slots.find((slot) => slot.slot === state.selected) || data.slots[0];
  state.selected = row.slot;
  const person = row.person || {};
  const gun = row.gun || {};
  const rfidUid = String(gun.rfid_uid || "");
  const rfidShort = rfidUid ? (rfidUid.length > 12 ? rfidUid.slice(0, 8) + "…" + rfidUid.slice(-4) : rfidUid) : "미등록";
  const options = [`<option value="">총기종류</option>`].concat(gunModels(data, gun.model).map((model) => `<option ${model === gun.model ? "selected" : ""}>${escapeAttr(model)}</option>`)).join("");
  const list = data.slots.map((slot) => {
    const p = slot.person || {};
    const g = slot.gun || {};
    const uid = String(g.rfid_uid || "").trim();
    // 등록된 칸만 동그라미. 미등록은 빈칸.
    const mark = uid ? "○" : "";
    return `<tr class="${slot.slot === row.slot ? "on" : ""}" data-slot="${slot.slot}"><td>${slot.slot}</td><td>${escapeAttr(p.rank || "")}</td><td>${escapeAttr(p.sn || "")}</td><td>${escapeAttr(p.name || "")}</td><td>${escapeAttr(g.model || "")}</td><td>${escapeAttr(g.serial || "")}</td><td class="rfid-mark" title="${escapeAttr(uid || "미등록")}">${mark}</td></tr>`;
  }).join("");
  openModal(`
    <h3 class="win-title">사용자정보</h3>
    <form id="userForm">
      <div class="win-body">
        <input type="hidden" name="kind" value="user" />
        <input type="hidden" name="slot" value="${row.slot}" />
        <table class="form-table">
          <tr><th>보관함</th><td>${escapeAttr(data.locker.name)}</td></tr>
          <tr><th>계 급</th><td><input name="rank" maxlength="4" value="${escapeAttr(person.rank || "")}" /></td></tr>
          <tr><th>군 번</th><td><input name="sn" maxlength="10" value="${escapeAttr(person.sn || "")}" /></td></tr>
          <tr><th>성 명</th><td><input name="name" maxlength="5" value="${escapeAttr(person.name || "")}" /></td></tr>
          <tr><th>총기종류</th><td><select name="model">${options}</select></td></tr>
          <tr><th>총기번호</th><td><input name="serial" maxlength="20" value="${escapeAttr(gun.serial || "")}" /></td></tr>
          <tr><th>RFID</th><td><span id="rfidUidView" title="${escapeAttr(rfidUid)}">${escapeAttr(rfidShort)}</span></td></tr>
        </table>
        <div class="win-actions">
          <button class="gb" type="button" id="rfidBindGun">RFID 등록</button>
          <button class="gb" type="button" id="rfidUnbindGun" ${!rfidUid ? "disabled" : ""}>RFID 해제</button>
          <button class="gb" type="button" id="deleteUser" ${row.enrolled || !row.person ? "disabled" : ""} title="${row.enrolled ? "지문이 등록되어 있으면 보관함에서 지문을 먼저 삭제하세요." : ""}">삭제</button>
          <button class="gb" type="submit">저장</button>
          <button class="gb" type="button" id="closeUsers">취소</button>
        </div>
        <div class="scroll"><table class="grid-table"><thead><tr><th>No.</th><th>계 급</th><th>군 번</th><th>성 명</th><th>총기종류</th><th>총기번호</th><th>RFID</th></tr></thead><tbody>${list}</tbody></table></div>
      </div>
    </form>`);
  $("closeUsers").onclick = closeModal;
  document.querySelectorAll("[data-slot]").forEach((tr) => {
    tr.onclick = () => { state.selected = Number(tr.dataset.slot); openUsers(); };
  });
  $("userForm").onsubmit = saveUser;
  const del = $("deleteUser");
  if (del) del.onclick = () => confirmDelete("/api/person/delete", { kind: "user", slot: row.slot });
  const bindBtn = $("rfidBindGun");
  if (bindBtn) bindBtn.onclick = () => registerGunRfid(row.slot);
  const unbindBtn = $("rfidUnbindGun");
  if (unbindBtn) unbindBtn.onclick = () => unbindGunRfid(row.slot);
}

async function registerGunRfid(slot) {
  const form = $("userForm");
  const row = (state.data && state.data.slots || []).find((item) => Number(item.slot) === Number(slot));
  const gun = (row && row.gun) || {};
  // locked(지문 등록됨)면 select가 disabled라 form 값이 비는 경우가 있어 저장된 총기정보도 사용
  let serial = form && form.serial ? String(form.serial.value || "").trim() : "";
  let model = form && form.model ? String(form.model.value || "").trim() : "";
  if (!serial) serial = String(gun.serial || "").trim();
  if (!model) model = String(gun.model || "").trim();
  if (!serial && !model) {
    toast("먼저 총기종류·총기번호를 입력하고 저장하세요.");
    return;
  }
  const category = String(gun.category || "소총");
  const saved = await post("/api/gun", { slot, model, serial, category });
  if (!saved.ok) {
    alertBox((saved.data && saved.data.error) || "총기 정보를 먼저 저장하지 못했습니다.");
    return;
  }
  toast("스티커를 RFID 리더에 가까이 대세요…");
  const result = await post("/api/gun/rfid", { action: "bind", slot });
  if (!result.ok) {
    alertBox((result.data && result.data.error) || "RFID 등록에 실패했습니다.");
    return;
  }
  toast(`RFID 등록 완료 · ${result.data.rfid_uid || ""}`);
  await refresh(false);
  openUsers();
}

async function unbindGunRfid(slot) {
  askBox("이 칸의 RFID 등록을 해제하겠습니까?", "YES", async () => {
    const result = await post("/api/gun/rfid", { action: "unbind", slot });
    if (!result.ok) {
      alertBox((result.data && result.data.error) || "RFID 해제에 실패했습니다.");
      return;
    }
    toast("RFID 등록을 해제했습니다.");
    await refresh(false);
    openUsers();
  });
}

async function saveUser(ev) {
  ev.preventDefault();
  const body = formValues(ev.target);
  const person = await post("/api/person", { kind: "user", slot: body.slot, rank: body.rank, name: body.name, sn: body.sn });
  if (!person.ok) {
    alertBox(person.data.error || "저장하지 못했습니다.");
    return;
  }
  if (body.model || body.serial) {
    const gun = await post("/api/gun", { slot: body.slot, model: body.model, serial: body.serial });
    if (!gun.ok) {
      alertBox(gun.data.error || "총기 정보를 저장하지 못했습니다.");
      return;
    }
  }
  toast("저장했습니다.");
  await refresh(false);
}

function openGuns() {
  state.popup = () => openGuns();
  const data = state.data;
  const catalog = data.catalog || [];
  const picked = Number.isInteger(state.gunIndex) ? catalog[state.gunIndex] : null;
  const category = picked ? picked.category : "권총";
  const options = ["권총", "소총", "기타"].map((name) => `<option ${name === category ? "selected" : ""}>${name}</option>`).join("");
  const order = picked ? picked.no : ((data.locker && data.locker.name) || "");
  const list = catalog.map((item) => `<tr class="${picked && item.index === picked.index ? "on" : ""}" data-index="${item.index}"><td>${item.no}</td><td>${escapeAttr(item.model)}</td></tr>`).join("");
  openModal(`
    <h3 class="win-title">총기목록</h3>
    <form id="gunForm">
      <div class="win-body">
        <input type="hidden" name="index" value="${picked ? picked.index : ""}" />
        <table class="form-table">
          <tr><th>순번</th><td id="gunOrder">${escapeAttr(order)}</td></tr>
          <tr><th>분류</th><td><select name="category">${options}</select></td></tr>
          <tr><th>모델명</th><td><input id="gunModel" name="model" maxlength="40" value="${escapeAttr(picked ? picked.model : "")}" /></td></tr>
        </table>
        <div class="win-actions">
          <button class="gb" type="button" id="deleteGun" ${picked ? "" : "disabled"}>삭제</button>
          <button class="gb" type="submit" id="saveGun" disabled>저장</button>
          <button class="gb" type="button" id="closeGuns">취소</button>
        </div>
        <div class="scroll gun-list"><table class="grid-table"><thead><tr><th>순번</th><th>모델명</th></tr></thead><tbody>${list}</tbody></table></div>
      </div>
    </form>`, "catalog");
  $("closeGuns").onclick = closeModal;
  document.querySelectorAll("[data-index]").forEach((tr) => {
    tr.onclick = () => { state.gunIndex = Number(tr.dataset.index); openGuns(); };
  });
  const model = $("gunModel");
  const save = $("saveGun");
  const syncSave = () => { save.disabled = !model.value.trim(); };
  model.oninput = syncSave;
  syncSave();
  $("gunForm").onsubmit = async (ev) => {
    ev.preventDefault();
    const creating = !Number.isInteger(state.gunIndex);
    const result = await post("/api/catalog", formValues(ev.target));
    if (!result.ok) {
      alertBox(result.data.error || "저장하지 못했습니다.");
      return;
    }
    state.gunIndex = creating ? null : result.data.index;
    toast("저장했습니다.");
    await refresh(false);
  };
  const del = $("deleteGun");
  if (del && picked) {
    del.onclick = () => askBox("이 번호의 총기 목록을 삭제하겠습니까?", "YES", async () => {
      const result = await post("/api/catalog/delete", { index: picked.index });
      if (!result.ok) {
        alertBox(result.data.error || "삭제하지 못했습니다.");
        return;
      }
      state.gunIndex = null;
      toast("삭제했습니다.");
      await refresh(false);
    });
  }
}

function openIssue() {
  state.popup = openIssue;
  const label = purposeTitle(state.data, "젤루텍");
  const fixed = (name) => `<th>${escapeAttr(name)}</th><td><input value="${escapeAttr(name)}" readonly /></td>`;
  openModal(`
    <h3 class="win-title">불출내용</h3>
    <div class="win-body">
      <h3 style="text-align:center;letter-spacing:.4em;margin:0 0 12px">불 출 현 황</h3>
      <table class="form-table">
        <tr>${fixed("사격")}${fixed("훈련")}</tr>
        <tr>${fixed("정비")}${fixed("근무")}</tr>
        <tr>${fixed("기타")}<th class="issue-name-cell"><input id="issueLabel" class="purpose-free" type="text" maxlength="10" autocomplete="off" spellcheck="false" value="${escapeAttr(label)}" /></th><td><input value="${escapeAttr(label)}" readonly tabindex="-1" /></td></tr>
      </table>
      <div class="win-actions">
        <button type="button" class="gb" id="saveIssue">저장</button>
        <button type="button" class="gb" id="closeIssue">취소</button>
      </div>
    </div>`, true);
  const issueField = $("issueLabel");
  issueField.readOnly = false;
  issueField.disabled = false;
  issueField.removeAttribute("readonly");
  issueField.focus();
  issueField.select();
  $("closeIssue").onclick = closeModal;
  const saveLabel = async () => {
    const result = await post("/api/purpose", { label: issueField.value });
    if (!result.ok) {
      alertBox(result.data.error || "저장하지 못했습니다.");
      return;
    }
    if (state.data) state.data.purpose_label = result.data.label || "젤루텍";
    toast("저장했습니다.");
    await refresh(false);
  };
  $("saveIssue").onclick = saveLabel;
  issueField.onkeydown = (ev) => {
    if (ev.key === "Enter") {
      ev.preventDefault();
      saveLabel();
    }
  };
}

function openUnitDialog() {
  if (!state.data || !state.data.can || !state.data.can.manage) {
    toast(state.data && state.data.who ? "이 계정으로는 이 화면을 열 수 없습니다." : "로그인한 뒤에 사용할 수 있습니다.");
    return;
  }
  const name = (state.data.unit_name || "지문인식").trim() || "지문인식";
  state.popup = openUnitDialog;
  openModal(`
    <h3 class="win-title">부대이름</h3>
    <form id="unitForm">
      <div class="win-body">
        <table class="form-table">
          <tr><th>부대이름</th><td><input id="unitName" name="name" maxlength="20" autocomplete="off" value="${escapeAttr(name)}" /></td></tr>
        </table>
        <div class="win-actions">
          <button class="gb" type="submit">저장</button>
          <button class="gb" type="button" id="addFromUnit">보관함 추가</button>
          <button class="gb" type="button" id="closeUnit">취소</button>
        </div>
      </div>
    </form>`, true);
  $("closeUnit").onclick = closeModal;
  const field = $("unitName");
  field.focus();
  field.select();
  $("addFromUnit").onclick = () => openLockerDialog(null);
  $("unitForm").onsubmit = async (ev) => {
    ev.preventDefault();
    const result = await post("/api/unit", { name: field.value });
    if (!result.ok) {
      alertBox(result.data.error || "저장하지 못했습니다.");
      return;
    }
    if (state.data) state.data.unit_name = result.data.name || "지문인식";
    closeModal();
    toast("저장했습니다.");
    await refresh(false);
  };
}

function openLockerDialog(id) {
  const adding = !id;
  const board = adding ? null : (state.data.boards || []).find((item) => item.id === id) || (state.data.locker && state.data.locker.id === id ? state.data.locker : null);
  if (!adding && !board) {
    toast("보관함을 찾지 못했습니다.");
    return;
  }
  state.popup = () => openLockerDialog(id);
  openModal(`
    <h3 class="win-title">보관함 설정</h3>
    <form id="lockerForm">
      <div class="win-body">
        <input type="hidden" name="id" value="${adding ? "" : escapeAttr(board.id)}" />
        <p style="text-align:center;font-size:28px;margin:0 0 12px">10형</p>
        <table class="form-table">
          <tr><th>보관함 배열(행X열)</th><td><input value="1" readonly style="width:48px" /> X <input value="10" readonly style="width:48px" /></td></tr>
          <tr><th>모니터 형</th><td><select disabled><option>5인치</option></select></td></tr>
          <tr><th>지문 인식기 수량</th><td><input value="1" readonly /></td></tr>
          <tr><th>보관함 Name :</th><td><input name="name" maxlength="20" value="${adding ? "" : escapeAttr(board.name)}" /></td></tr>
          <tr><th>보관함 IP :</th><td><input name="host" value="${adding ? "" : escapeAttr(board.host)}" /></td></tr>
          <tr><th>Port :</th><td><input name="port" value="${adding ? "8080" : escapeAttr(board.port)}" /></td></tr>
        </table>
        <div class="win-actions">
          <button class="gb" type="button" id="deleteLocker" ${adding ? "disabled" : ""}>삭제</button>
          <button class="gb" type="submit">저장</button>
          <button class="gb" type="button" id="closeLocker">취소</button>
        </div>
      </div>
    </form>`, true);
  $("closeLocker").onclick = closeModal;
  $("lockerForm").onsubmit = async (ev) => {
    ev.preventDefault();
    const body = formValues(ev.target);
    const result = await post(adding ? "/api/locker/add" : "/api/locker", body);
    if (!result.ok) {
      alertBox(result.data.error || "저장하지 못했습니다.");
      return;
    }
    closeModal();
    toast("저장했습니다.");
    await refresh(false);
  };
  const del = $("deleteLocker");
  if (del && !adding) del.onclick = () => askBox("이 보관함 등록을 지우겠습니까?", "YES", async () => {
    const result = await post("/api/locker/delete", { id: board.id });
    if (!result.ok) {
      alertBox(result.data.error || "삭제하지 못했습니다.");
      return;
    }
    state.popup = null;
    closeModal();
    toast("삭제했습니다.");
    await refresh(false);
  });
}

function selectedLocker() {
  return (state.data && state.data.locker) || null;
}

function openHistory(scope) {
  historyScope = scope === "all" ? "all" : "gun";
  state.popup = () => openHistory(historyScope);
  const locker = selectedLocker();
  const scopeLabel = historyScope === "all" ? "전체이력" : "총기수불";
  const title = locker && locker.name ? `${locker.name} ${scopeLabel}` : scopeLabel;
  const timeHead = historyScope === "all" ? "발생 시간" : "총기 수불 시간";
  openModal(`
    <div class="win-caption">
      <h3>${escapeAttr(title)}</h3>
      <div class="win-chrome">
        <button type="button" class="cap cap-min" id="histMin" aria-label="최소화"></button>
        <button type="button" class="cap cap-max" id="histMax" aria-label="최대화"></button>
        <button type="button" class="cap cap-close" id="histClose" aria-label="닫기"></button>
      </div>
    </div>
    <div class="win-body">
      <form class="filters" id="historyForm">
        <div class="history-scope" role="group" aria-label="조회 이력">
          <button type="button" class="gb history-scope-btn${historyScope === "all" ? " on" : ""}" data-scope="all">전체이력</button>
          <button type="button" class="gb history-scope-btn${historyScope === "gun" ? " on" : ""}" data-scope="gun">총기수불</button>
        </div>
        <span>기간</span>
        <input name="from" type="date" /> ~ <input name="to" type="date" />
        <label><input type="radio" name="find" value="type" checked /> 타입</label>
        <label><input type="radio" name="find" value="sn" /> 군번</label>
        <label><input type="radio" name="find" value="name" /> 이름</label>
        <input name="word" />
        <button class="gb" type="submit">검색</button>
        <button class="gb" type="button" id="printBtn" disabled>인쇄</button>
        <button class="gb" type="button" id="csvBtn" disabled>저장</button>
        <button class="gb" type="button" id="closeHistory">닫기 (ESC)</button>
      </form>
      <div class="scroll" id="historyTable"><table class="history-table"><thead><tr><th>No.</th><th>${timeHead}</th><th>구분</th><th>타입</th><th>군번</th><th>이름</th><th>내용</th></tr></thead><tbody></tbody></table></div>
    </div>`, "history");
  const modal = $("modal");
  const maxBtn = $("histMax");
  const syncMax = () => maxBtn.classList.toggle("is-restore", modal.classList.contains("history-max"));
  $("histClose").onclick = closeModal;
  $("histMin").onclick = () => {
    if (modal.classList.contains("history-min")) {
      modal.classList.remove("history-min");
      return;
    }
    modal.classList.remove("history-max");
    modal.classList.add("history-min");
    syncMax();
  };
  maxBtn.onclick = () => {
    modal.classList.remove("history-min");
    modal.classList.toggle("history-max");
    syncMax();
  };
  $("closeHistory").onclick = closeModal;
  document.querySelectorAll(".history-scope-btn").forEach((btn) => {
    btn.onclick = () => setHistoryScope(btn.dataset.scope);
  });
  $("historyForm").onsubmit = (ev) => { ev.preventDefault(); loadHistory(); };
  $("csvBtn").onclick = () => downloadHistory(historyView);
  $("printBtn").onclick = () => printHistory();
  paintHistoryRows(historyScope === "gun" ? ((state.data && state.data.history) || []) : [], true);
  loadHistory(true);
}

function setHistoryScope(scope) {
  const next = scope === "all" ? "all" : "gun";
  if (!$("historyForm") || next === historyScope) return;
  historyScope = next;
  document.querySelectorAll(".history-scope-btn").forEach((btn) => {
    btn.classList.toggle("on", btn.dataset.scope === historyScope);
  });
  const heading = document.querySelector("#modal .win-caption h3");
  const locker = selectedLocker();
  const scopeLabel = historyScope === "all" ? "전체이력" : "총기수불";
  if (heading) heading.textContent = locker && locker.name ? `${locker.name} ${scopeLabel}` : scopeLabel;
  paintHistoryRows([], false);
  loadHistory(false);
}

function printHistory() {
  const heading = document.querySelector("#modal .win-caption h3");
  const title = heading ? heading.textContent : "이력관리";
  const table = document.querySelector("#historyTable table");
  if (!table) return;
  const frame = document.createElement("iframe");
  frame.setAttribute("aria-hidden", "true");
  frame.style.cssText = "position:fixed;left:-10000px;top:0;width:194mm;height:240mm;border:0;";
  document.body.appendChild(frame);
  const doc = frame.contentDocument;
  doc.open();
  doc.write(`<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8"><title>${escapeAttr(title)}</title><style>
    @page { size: A4 portrait; margin: 10mm 8mm; }
    html, body { margin: 0; background: #fff; color: #222; font-family: "Malgun Gothic", "Segoe UI", sans-serif; }
    h1 { margin: 0 0 8px; text-align: center; font-size: 14pt; font-weight: 700; }
    table { width: 100%; border-collapse: collapse; font-size: 8pt; background: #fff; }
    th, td { border: 1px solid #b5b5b5; padding: 3px 4px; text-align: center; vertical-align: middle; line-height: 1.2; white-space: nowrap; }
    th:nth-child(2), td:nth-child(2) { white-space: normal; }
    thead th { background: #f5f5f5; }
    tr, th, td { break-inside: avoid; }
  </style></head><body><h1>${escapeAttr(title)}</h1>${table.outerHTML}</body></html>`);
  doc.close();
  const printed = frame.contentDocument.querySelector("table");
  if (printed) {
    const limit = frame.contentDocument.documentElement.clientWidth;
    let size = 8;
    while (size > 6) {
      printed.style.fontSize = size + "pt";
      const wrapped = Array.from(printed.querySelectorAll("th, td")).some((cell) => cell.cellIndex !== 1 && cell.scrollWidth > cell.clientWidth + 1);
      if (!wrapped && printed.scrollWidth <= limit + 1) break;
      size -= 0.5;
    }
  }
  const win = frame.contentWindow;
  const cleanup = () => { if (frame.parentNode) frame.remove(); };
  win.addEventListener("afterprint", cleanup);
  setTimeout(() => {
    win.focus();
    win.print();
  }, 60);
  setTimeout(cleanup, 2000);
}

function historyTableHtml(rows, note) {
  const timeHead = historyScope === "all" ? "발생 시간" : "총기 수불 시간";
  const body = rows.length
    ? rows.map((row, index) => `<tr><td>${index + 1}</td><td>${escapeAttr(row.time)}</td><td>${escapeAttr(row.locker)}</td><td>${escapeAttr(row.model || row.category || "")}</td><td>${escapeAttr(row.sn)}</td><td>${escapeAttr(row.name)}</td><td>${escapeAttr(row.detail)}</td></tr>`).join("")
    : `<tr><td colspan="7">${escapeAttr(note || "표시할 이력이 없습니다.")}</td></tr>`;
  return `<table class="history-table"><thead><tr><th>No.</th><th>${timeHead}</th><th>구분</th><th>타입</th><th>군번</th><th>이름</th><th>내용</th></tr></thead><tbody>${body}</tbody></table>`;
}

function paintHistoryRows(rows, ready) {
  const table = $("historyTable");
  if (!table) return;
  historyView = rows;
  const locker = selectedLocker();
  const note = ready
    ? (historyScope === "all"
      ? `${(locker && locker.name) || "이 보관함"}에 기록된 전체 이력이 없습니다.`
      : `${(locker && locker.name) || "이 보관함"}에 기록된 불출·보관 이력이 없습니다.`)
    : "이력을 불러오는 중입니다.";
  table.innerHTML = historyTableHtml(rows, note);
  const csv = $("csvBtn");
  const print = $("printBtn");
  if (csv) csv.disabled = !ready;
  if (print) print.disabled = !ready;
}

async function loadHistory(initial) {
  const form = $("historyForm");
  if (!form) return;
  const body = formValues(form);
  const locker = selectedLocker();
  const heading = document.querySelector("#modal .win-caption h3");
  const scopeLabel = historyScope === "all" ? "전체이력" : "총기수불";
  if (heading && locker && locker.name) heading.textContent = `${locker.name} ${scopeLabel}`;
  else if (heading) heading.textContent = scopeLabel;
  historyQuery = { from: body.from || "", to: body.to || "", scope: historyScope };
  if (locker && locker.id) historyQuery.locker_id = locker.id;
  if (!initial) {
    if (body.find === "sn") historyQuery.sn = body.word || "";
    if (body.find === "name") historyQuery.name = body.word || "";
  }
  const result = await api("/api/history?" + new URLSearchParams(historyQuery).toString());
  if (!$("historyTable")) return;
  if (!result.ok) {
    paintHistoryRows([], false);
    const table = $("historyTable");
    if (table) table.innerHTML = historyTableHtml([], result.data.error || "이력을 불러오지 못했습니다. 로그인한 뒤 다시 열어 주세요.");
    return;
  }
  let rows = result.data.rows || [];
  if (!initial && body.find === "type" && body.word) {
    const word = body.word;
    rows = rows.filter((row) => `${row.model || ""} ${row.detail || ""} ${row.action || ""} ${row.purpose || ""} ${row.category || ""}`.includes(word));
  }
  if (state.data && historyScope === "gun") state.data.history = rows;
  paintHistoryRows(rows, true);
}

function historyCells(rows) {
  const header = ["No.", historyScope === "all" ? "발생 시간" : "총기 수불 시간", "구분", "타입", "군번", "이름", "내용"];
  const body = rows.map((row, index) => [
    String(index + 1),
    row.time || "",
    row.locker || "",
    row.model || row.category || "",
    row.sn || "",
    row.name || "",
    row.detail || "",
  ]);
  return { header, body };
}

function fitCanvasText(ctx, text, width) {
  let value = String(text == null ? "" : text);
  if (ctx.measureText(value).width <= width) return value;
  while (value.length > 1 && ctx.measureText(value + "…").width > width) value = value.slice(0, -1);
  return value + "…";
}

function renderHistoryPages(title, header, body) {
  const width = 1240;
  const height = 1754;
  const margin = 64;
  const cols = [80, 230, 180, 90, 190, 140, 202];
  const tableWidth = cols.reduce((sum, n) => sum + n, 0);
  const rowH = 46;
  const headH = 52;
  const pages = [];
  let index = 0;
  const drawHeader = (ctx, y) => {
    ctx.font = "700 18px Malgun Gothic, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillStyle = "#f5f5f5";
    ctx.fillRect(margin, y, tableWidth, headH);
    ctx.strokeStyle = "#666";
    ctx.lineWidth = 2;
    ctx.strokeRect(margin, y, tableWidth, headH);
    let x = margin;
    ctx.fillStyle = "#222";
    header.forEach((label, i) => {
      if (i) ctx.beginPath(), ctx.moveTo(x, y), ctx.lineTo(x, y + headH), ctx.stroke();
      ctx.fillText(label, x + cols[i] / 2, y + headH / 2);
      x += cols[i];
    });
    return y + headH;
  };
  while (index < body.length || pages.length === 0) {
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#fff";
    ctx.fillRect(0, 0, width, height);
    ctx.fillStyle = "#222";
    ctx.font = "700 36px Malgun Gothic, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(title, width / 2, 78);
    let y = drawHeader(ctx, 120);
    ctx.font = "15px Malgun Gothic, sans-serif";
    const limit = height - 64;
    while (index < body.length && y + rowH <= limit) {
      const cells = body[index];
      ctx.strokeStyle = "#666";
      ctx.lineWidth = 2;
      ctx.strokeRect(margin, y, tableWidth, rowH);
      let x = margin;
      ctx.fillStyle = "#222";
      ctx.textAlign = "center";
      cells.forEach((cell, i) => {
        if (i) ctx.beginPath(), ctx.moveTo(x, y), ctx.lineTo(x, y + rowH), ctx.stroke();
        ctx.fillText(fitCanvasText(ctx, cell, cols[i] - 16), x + cols[i] / 2, y + rowH / 2);
        x += cols[i];
      });
      y += rowH;
      index += 1;
    }
    if (!body.length) {
      ctx.font = "15px Malgun Gothic, sans-serif";
      ctx.fillText("표시할 이력이 없습니다.", width / 2, y + 40);
    }
    pages.push(canvas);
    if (!body.length) break;
  }
  return pages;
}

function pdfFromJpegs(jpegs) {
  const encoder = new TextEncoder();
  const chunks = [];
  let length = 0;
  const pushStr = (text) => {
    const bytes = encoder.encode(text);
    chunks.push(bytes);
    length += bytes.length;
  };
  const pushBin = (bytes) => {
    chunks.push(bytes);
    length += bytes.length;
  };
  const offsets = [];
  pushStr("%PDF-1.4\n");
  offsets.push(length);
  pushStr("1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n");
  const kids = jpegs.map((_, i) => `${3 + i * 3} 0 R`).join(" ");
  offsets.push(length);
  pushStr(`2 0 obj << /Type /Pages /Count ${jpegs.length} /Kids [${kids}] >> endobj\n`);
  jpegs.forEach((jpeg, i) => {
    const pageId = 3 + i * 3;
    const content = `q 571 0 0 817 12 12 cm /Im${i} Do Q\n`;
    offsets.push(length);
    pushStr(`${pageId} 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 595.28 841.89] /Contents ${pageId + 1} 0 R /Resources << /XObject << /Im${i} ${pageId + 2} 0 R >> >> >> endobj\n`);
    offsets.push(length);
    pushStr(`${pageId + 1} 0 obj << /Length ${content.length} >> stream\n${content}endstream endobj\n`);
    offsets.push(length);
    pushStr(`${pageId + 2} 0 obj << /Type /XObject /Subtype /Image /Width ${jpeg.width} /Height ${jpeg.height} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ${jpeg.bytes.length} >> stream\n`);
    pushBin(jpeg.bytes);
    pushStr("\nendstream endobj\n");
  });
  const xrefAt = length;
  let xref = `xref\n0 ${offsets.length + 1}\n0000000000 65535 f \n`;
  offsets.forEach((offset) => { xref += `${String(offset).padStart(10, "0")} 00000 n \n`; });
  pushStr(xref);
  pushStr(`trailer << /Size ${offsets.length + 1} /Root 1 0 R >>\nstartxref\n${xrefAt}\n%%EOF`);
  const out = new Uint8Array(length);
  let pos = 0;
  chunks.forEach((chunk) => { out.set(chunk, pos); pos += chunk.length; });
  return out;
}

async function downloadHistory(rows) {
  const heading = document.querySelector("#modal .win-caption h3");
  const title = heading ? heading.textContent : "이력관리";
  const { header, body } = historyCells(rows || []);
  const canvases = renderHistoryPages(title, header, body);
  const jpegs = [];
  for (const canvas of canvases) {
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.92));
    jpegs.push({ width: canvas.width, height: canvas.height, bytes: new Uint8Array(await blob.arrayBuffer()) });
  }
  const pdf = pdfFromJpegs(jpegs);
  const url = URL.createObjectURL(new Blob([pdf], { type: "application/pdf" }));
  const link = document.createElement("a");
  link.href = url;
  const lockerName = (selectedLocker() && selectedLocker().name) || "보관함";
  link.download = lockerName + " 이력관리.pdf";
  link.click();
  URL.revokeObjectURL(url);
}

function confirmDelete(path, body, message) {
  askBox(message || "삭제하시겠습니까?", "YES", async () => {
    const result = await post(path, body);
    if (!result.ok) {
      alertBox(result.data.error || "삭제하지 못했습니다.");
      return;
    }
    toast("삭제했습니다.");
    await refresh(false);
  });
}

async function selectLocker(id) {
  const result = await post("/api/locker/select", { id });
  if (!result.ok) {
    toast(result.data.error || "보관함을 선택하지 못했습니다.");
    return;
  }
  await refresh(true);
}

async function saveMemo() {
  if (!state.data || !state.data.can || !state.data.can.manage) {
    toast(state.data && state.data.who ? "이 계정으로는 저장할 수 없습니다." : "로그인한 뒤에 사용할 수 있습니다.");
    return;
  }
  const result = await post("/api/memo", { text: $("memoText").value });
  toast(result.ok ? "저장했습니다." : (result.data.error || "저장하지 못했습니다."));
}

async function refresh(soft) {
  const typing = document.activeElement && /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName);
  let result;
  try {
    result = await api("/api/state");
  } catch (err) {
    toast("프로그램 연결이 끊겼습니다. 창을 닫고 '스마트 무기고 시스템.exe'를 다시 실행하세요.");
    return;
  }
  if (!result.data || !Array.isArray(result.data.boards)) return;
  const prevData = state.data;
  const prevLocker = prevData && prevData.locker && prevData.locker.id;
  watchLiveSecurity(prevData, result.data);
  state.data = result.data;
  syncWarnAlerts(result.data.alerts || []);
  // 로그는 모달/입력 중에도 갱신 (본 화면 레이아웃은 건드리지 않음)
  paintLogs(result.data.logs);
  const nextLocker = result.data.locker && result.data.locker.id;
  if (!$("modal").hidden && $("historyForm") && prevLocker !== nextLocker) loadHistory(false);
  const layer = $("fullScreen");
  if (layer && !layer.hidden) {
    const next = fullScreenHtml(state.data);
    if (layer.dataset.view !== next) {
      layer.dataset.view = next;
      layer.innerHTML = next;
      const close = $("closeFull");
      if (close) close.onclick = closeFullScreen;
    }
  }
  if ($("comboGuns")) paintComboGuns();
  if (!$("modal").hidden || (soft && typing)) return;
  const key = visibleKey(state.data);
  if (key === refresh.seen) return;
  refresh.seen = key;
  paintFrame();
  if (!soft && state.popup) state.popup();
}

async function toggleLogin() {
  if (state.data && state.data.who) {
    await post("/api/logout");
    closeModal();
    await refresh(false);
    return;
  }
  loginModal();
}

document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape") {
    if (!$("alert").hidden) {
      // 경고 팝업은 ESC로 숨기만 하면 노란/빨간 테두리만 남는 상태가 됨 → 확인과 동일 처리.
      if (warnShowing && $("alertOk")) {
        $("alertOk").click();
        return;
      }
      $("alert").hidden = true;
      return;
    }
    if (!$("fullScreen").hidden) closeFullScreen();
    else if (!$("modal").hidden) closeModal();
  }
  if (ev.ctrlKey && (ev.key === "m" || ev.key === "M")) closeFullScreen();
});

function bindToolbar() {
  const nav = $("toolbar");
  if (!nav || nav.dataset.bound) return;
  nav.dataset.bound = "1";
  nav.addEventListener("pointerdown", (ev) => {
    if (ev.button != null && ev.button !== 0) return;
    const btn = ev.target.closest("[data-nav]");
    if (!btn) return;
    const item = NAV.find((entry) => entry.id === btn.dataset.nav);
    if (!item) return;
    ev.preventDefault();
    openMenu(item);
  });
}

bindToolbar();
$("memoSave").onclick = saveMemo;
$("memoLoad").onclick = async () => { await refresh(true); toast("메모를 다시 읽었습니다."); };
$("memoMenu").onclick = () => $("memoText").focus();
window.addEventListener("resize", alignLeftColumn);

refresh(false);
setInterval(() => refresh(true), 400);
setInterval(() => {
  const date = $("fullDate");
  if (date) date.textContent = dateText(new Date());
}, 1000);
