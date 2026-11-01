const NAV = [
  { id: "login", label: "로그인" },
  { id: "settings", label: "보관함 설정", need: "manage" },
  { id: "people", label: "관리자 정보", need: "manage" },
  { id: "users", label: "사용자 정보", need: "manage" },
  { id: "status", label: "총기 현황", need: "status" },
  { id: "guns", label: "총기 목록", need: "manage" },
  { id: "issue", label: "불출 내용", need: "manage" },
  { id: "history", label: "이력 관리", need: "history" },
];

const state = { data: null, selected: 1, popup: null, gunIndex: null };
let historyQuery = {};
let historyView = [];

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
  return { ok: res.ok && data.ok !== false, data };
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
  if (item.id === "login") return true;
  const can = state.data && state.data.can;
  if (!can || !item.need) return false;
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
  return `<span class="scanner-icon"></span>`;
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
  const sensor = locker.sensor;
  const scanners = [1, 2, 3, 4].map((no) => `
    <button type="button" class="scanner ${no === 1 && sensor ? "on" : ""}" data-scanner="${no}">${scannerIcon()}<span>스캐너 #${no}</span></button>`).join("");
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
      <div class="scanners">${scanners}</div>
`;
  box.querySelectorAll("[data-scanner]").forEach((btn) => {
    btn.onclick = () => {
      const no = Number(btn.dataset.scanner);
      if (no === 1) toast(sensor ? "스캐너 #1 이 연결되어 있습니다." : "스캐너 #1 이 연결되어 있지 않습니다.");
      else toast("이 보관함의 지문인식기는 1대입니다.");
    };
  });
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

function paintGrid() {
  const data = state.data;
  const panel = $("grid");
  if (!data) {
    panel.innerHTML = "";
    return;
  }
  const kind = data.locker && data.locker.kind === "pistol" ? "pistol" : "rifle";
  if (kind === "pistol") {
    panel.innerHTML = paintPistolGrid(data);
  } else {
    const names = [];
    const rifles = [];
    (data.slots || []).forEach((slot) => {
      const stored = slot.enrolled && slot.present;
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

function paintPistolGrid(data) {
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
      const stored = slot.enrolled && slot.present;
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
    data.memo || "",
    (data.locker && data.locker.kind) || "",
    (data.locker && data.locker.id) || "",
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
  const memo = $("memoText");
  if (memo && document.activeElement !== memo && state.data) memo.value = state.data.memo || "";
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
  else if (item.id === "settings") openLockerDialog(state.data.locker && state.data.locker.id);
  else if (item.id === "people") openGroups();
  else if (item.id === "users") openUsers();
  else if (item.id === "guns") {
    state.gunIndex = null;
    openGuns();
  }
  else if (item.id === "issue") openIssue();
  else if (item.id === "history") openHistory();
}

function openModal(html, mode) {
  const extra = mode === true ? "narrow" : (mode || "");
  const modal = $("modal");
  modal.className = "modal";
  modal.innerHTML = `<div class="win ${extra}">${html}</div>`;
  modal.hidden = false;
}

function closeModal() {
  state.popup = null;
  const modal = $("modal");
  modal.hidden = true;
  modal.className = "modal";
  modal.innerHTML = "";
  refresh.seen = "";
  if (state.data) paintFrame();
}

function alertBox(message, onOk) {
  const layer = $("alert");
  layer.innerHTML = `<div class="alert-box"><p>${escapeAttr(message)}</p><button type="button" class="gb" id="alertOk">확인</button></div>`;
  layer.hidden = false;
  $("alertOk").onclick = () => {
    layer.hidden = true;
    layer.innerHTML = "";
    if (onOk) onOk();
  };
}

function askBox(message, yesLabel, onYes) {
  const layer = $("alert");
  layer.innerHTML = `<div class="alert-box"><p>${message}</p><div class="win-actions"><button type="button" class="gb" id="askYes">${yesLabel}</button><button type="button" class="gb" id="askNo">취소</button></div></div>`;
  layer.hidden = false;
  $("askYes").onclick = () => { layer.hidden = true; onYes(); };
  $("askNo").onclick = () => { layer.hidden = true; };
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

function fullScreenHtml(data) {
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
  const link = data.link || {};
  const talking = !!link.talking;
  const pulse = Number(link.pulse) || 0;
  const blink = talking && pulse !== fullScreenHtml.seen;
  fullScreenHtml.seen = talking ? pulse : -1;
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
  return `
    <div class="fs">
      <div class="fs-top">
        <img class="fs-logo" src="/assets/zellootec.png" alt="ZELLOOTEC" />
        <h2>총기 보관현황</h2>
        <i class="fs-dot${talking ? "" : " off"}${blink ? " blink" : ""}"></i>
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
      <button type="button" class="gb fs-close no-print" id="closeFull">닫기 (Ctrl + M)</button>
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
  const options = [`<option value="">총기종류</option>`].concat(gunModels(data, gun.model).map((model) => `<option ${model === gun.model ? "selected" : ""}>${escapeAttr(model)}</option>`)).join("");
  const list = data.slots.map((slot) => {
    const p = slot.person || {};
    const g = slot.gun || {};
    return `<tr class="${slot.slot === row.slot ? "on" : ""}" data-slot="${slot.slot}"><td>${slot.slot}</td><td>${escapeAttr(p.rank || "")}</td><td>${escapeAttr(p.sn || "")}</td><td>${escapeAttr(p.name || "")}</td><td>${escapeAttr(g.model || "")}</td><td>${escapeAttr(g.serial || "")}</td></tr>`;
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
        </table>
        <div class="win-actions">
          <button class="gb" type="button" id="deleteUser" ${row.enrolled || !row.person ? "disabled" : ""} title="${row.enrolled ? "지문이 등록되어 있으면 보관함에서 지문을 먼저 삭제하세요." : ""}">삭제</button>
          <button class="gb" type="submit">저장</button>
          <button class="gb" type="button" id="closeUsers">취소</button>
        </div>
        <div class="scroll"><table class="grid-table"><thead><tr><th>No.</th><th>계 급</th><th>군 번</th><th>성 명</th><th>총기종류</th><th>총기번호</th></tr></thead><tbody>${list}</tbody></table></div>
      </div>
    </form>`);
  $("closeUsers").onclick = closeModal;
  document.querySelectorAll("[data-slot]").forEach((tr) => {
    tr.onclick = () => { state.selected = Number(tr.dataset.slot); openUsers(); };
  });
  $("userForm").onsubmit = saveUser;
  const del = $("deleteUser");
  if (del) del.onclick = () => confirmDelete("/api/person/delete", { kind: "user", slot: row.slot });
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

function openHistory() {
  state.popup = openHistory;
  const locker = selectedLocker();
  const title = locker && locker.name ? `${locker.name} 이력관리` : "이력관리";
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
      <div class="scroll" id="historyTable"><table class="history-table"><thead><tr><th>No.</th><th>총기 수불 시간</th><th>구분</th><th>타입</th><th>군번</th><th>이름</th><th>내용</th></tr></thead><tbody></tbody></table></div>
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
  $("historyForm").onsubmit = (ev) => { ev.preventDefault(); loadHistory(); };
  $("csvBtn").onclick = () => downloadHistory(historyView);
  $("printBtn").onclick = () => printHistory();
  paintHistoryRows((state.data && state.data.history) || [], true);
  loadHistory(true);
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
  const body = rows.length
    ? rows.map((row, index) => `<tr><td>${index + 1}</td><td>${escapeAttr(row.time)}</td><td>${escapeAttr(row.locker)}</td><td>${escapeAttr(row.model || row.category || "")}</td><td>${escapeAttr(row.sn)}</td><td>${escapeAttr(row.name)}</td><td>${escapeAttr(row.detail)}</td></tr>`).join("")
    : `<tr><td colspan="7">${escapeAttr(note || "표시할 이력이 없습니다.")}</td></tr>`;
  return `<table class="history-table"><thead><tr><th>No.</th><th>총기 수불 시간</th><th>구분</th><th>타입</th><th>군번</th><th>이름</th><th>내용</th></tr></thead><tbody>${body}</tbody></table>`;
}

function paintHistoryRows(rows, ready) {
  const table = $("historyTable");
  if (!table) return;
  historyView = rows;
  const locker = selectedLocker();
  const note = ready
    ? `${(locker && locker.name) || "이 보관함"}에 기록된 불출·보관 이력이 없습니다.`
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
  if (heading && locker && locker.name) heading.textContent = `${locker.name} 이력관리`;
  historyQuery = { from: body.from || "", to: body.to || "" };
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
    rows = rows.filter((row) => `${row.model || ""} ${row.detail || ""} ${row.action || ""} ${row.purpose || ""}`.includes(word));
  }
  if (state.data) state.data.history = rows;
  paintHistoryRows(rows, true);
}

function historyCells(rows) {
  const header = ["No.", "총기 수불 시간", "구분", "타입", "군번", "이름", "내용"];
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
    toast("프로그램 연결이 끊겼습니다. 창을 닫고 OMS.bat 을 다시 실행하세요.");
    return;
  }
  if (!result.data || !Array.isArray(result.data.boards)) return;
  const prevLocker = state.data && state.data.locker && state.data.locker.id;
  state.data = result.data;
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
