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
    btn.onclick = () => openMenu(item);
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
  const rows = (data.boards || []).map((board) => `
    <button type="button" class="net-item ${board.online ? "online" : "offline"} ${board.selected ? "picked" : ""}" data-locker="${escapeAttr(board.id)}" title="${escapeAttr(board.host)}:${board.port}"><img src="/assets/locker.png" alt="" /><span>${escapeAttr(board.name)}</span></button>`).join("");
  box.innerHTML = `<div class="net-tree"><button type="button" class="net-head" id="netAdd"><img src="/assets/zellootec.png" alt="" /><span>지문인식</span></button><div class="net-children">${rows}</div></div>`;
  $("netAdd").onclick = () => {
    if (!state.data || !state.data.can || !state.data.can.manage) {
      toast(state.data && state.data.who ? "이 계정으로는 이 화면을 열 수 없습니다." : "로그인한 뒤에 사용할 수 있습니다.");
      return;
    }
    openLockerDialog(null);
  };
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

function fleetOf(data) {
  const names = ["사격", "훈련", "정비", "근무", "기타", "젤루텍"];
  const purposes = Object.fromEntries(names.map((name) => [name, 0]));
  const total = { registered: 0, issued: 0, stored: 0, empty: 0, unpaid: 0, bays: 0 };
  (data.boards || []).filter((item) => item.online).forEach((board) => {
    total.bays += 10;
    total.registered += Number(board.registered) || 0;
    total.issued += Number(board.issued) || 0;
    total.stored += Number(board.stored) || 0;
    total.empty += Number(board.empty) || 0;
    total.unpaid += Number(board.unpaid) || 0;
    (board.purposes || []).forEach((item) => {
      if (Object.prototype.hasOwnProperty.call(purposes, item.name)) purposes[item.name] += Number(item.count) || 0;
    });
  });
  return { total, purposes, names };
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
  const fleet = fleetOf(data);
  const countOf = (name) => fleet.purposes[name] || 0;
  const names = fleet.names;
  const sum = names.reduce((n, name) => n + countOf(name), 0);
  const n = (value) => value;
  const pair = (a, b) => `<tr><th>${escapeAttr(purposeTitle(data, a))}</th><td>${a ? n(countOf(a)) : ""}</td><th>${escapeAttr(purposeTitle(data, b))}</th><td>${b ? n(countOf(b)) : ""}</td></tr>`;
  const sensor = data.locker && data.locker.sensor;
  const scanners = [1, 2, 3, 4].map((no) => `
    <button type="button" class="scanner ${no === 1 && sensor ? "on" : ""}" data-scanner="${no}">${scannerIcon()}<span>스캐너 #${no}</span></button>`).join("");
  box.innerHTML = `
    <table class="box"><caption>소총 보관 현황</caption>
      <tr><th>보관함</th><td colspan="3">${fleet.total.bays}</td></tr>
      <tr><th>보관</th><td>${n(fleet.total.stored)}</td><th>불출</th><td>${n(fleet.total.issued)}</td></tr>
      <tr><th>미지급</th><td>${n(fleet.total.unpaid)}</td><th>미등록</th><td>${n(fleet.total.empty)}</td></tr>
      <tr><th>전체 수량</th><td colspan="3">${n(fleet.total.registered)}</td></tr>
    </table>
    <table class="box"><caption>불 출 현 황</caption>
      ${pair(names[0], names[1])}
      ${pair(names[2], names[3])}
      ${pair(names[4], names[5])}
      <tr><th>계</th><td colspan="3">${n(sum)}</td></tr>
    </table>
    <div class="scanners">${scanners}</div>`;
  box.querySelectorAll("[data-scanner]").forEach((btn) => {
    btn.onclick = () => {
      const no = Number(btn.dataset.scanner);
      if (no === 1) toast(sensor ? "스캐너 #1 이 연결되어 있습니다." : "스캐너 #1 이 연결되어 있지 않습니다.");
      else toast("이 보관함의 지문인식기는 1대입니다.");
    };
  });
}

function slotLabel(slot) {
  const person = slot.person;
  if (person && (person.rank || person.name)) return `${person.rank || ""} ${person.name || ""}`.trim();
  if (slot.live_name) return slot.live_name;
  return `사용자${slot.slot}`;
}

function paintGrid() {
  const data = state.data;
  const panel = $("grid");
  if (!data) {
    panel.innerHTML = "";
    return;
  }
  const names = [];
  const rifles = [];
  (data.slots || []).forEach((slot) => {
    const stored = slot.enrolled && slot.present;
    const img = slot.enrolled
      ? `<img class="pi-rifle ${stored ? "is-in" : "is-out"}" src="/assets/rifle.png" alt="" />`
      : "";
    names.push(`<div class="pi-name">${escapeAttr(slotLabel(slot))}</div>`);
    rifles.push(`<div class="pi-cell">${img}</div>`);
  });
  panel.innerHTML = `
    <div class="pi-lockers">
      <div class="pi-names">${names.join("")}</div>
      <div class="pi-rifles">${rifles.join("")}</div>
    </div>
    <div class="sheet"></div>`;
  panel.onclick = () => openFullScreen();
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
  else if (item.id === "settings") openLockerDialog(state.data.locker.id);
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
  modal.innerHTML = `<div class="win ${extra}">${html}</div>`;
  modal.hidden = false;
}

function closeModal() {
  state.popup = null;
  $("modal").hidden = true;
  $("modal").innerHTML = "";
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

function fullScreenHtml(data) {
  const fleet = fleetOf(data);
  const heads = fleet.names.map((name) => `<div><span>${escapeAttr(purposeTitle(data, name))}</span><b>${fleet.purposes[name] || 0}</b></div>`).join("");
  const connected = fleet.total.bays / 10;
  return `
    <div class="fs">
      <div class="fs-top">
        <img class="fs-logo" src="/assets/zellootec.png" alt="ZELLOOTEC" />
        <h2>소 총 보 관 현 황</h2>
        <i class="fs-dot"></i>
      </div>
      <p class="no-print" style="text-align:center;margin:8px 0 0;color:#666">연결된 보관함 ${connected}대</p>
      <div class="fs-dash">
        <div class="fs-side">
          <div class="fs-line"><span class="fs-pill">등록</span><b class="fs-num">${fleet.total.registered}</b></div>
          <div class="fs-line"><span class="fs-pill">불출</span><b class="fs-num">${fleet.total.issued}</b></div>
        </div>
        <div class="fs-hero"><span>보관 수량</span><strong>${fleet.total.stored}</strong></div>
        <div class="fs-side right">
          <div class="fs-line"><b class="fs-num">${fleet.total.empty}</b><span class="fs-pill">미등록</span></div>
          <div class="fs-line"><b class="fs-num">${fleet.total.unpaid}</b><span class="fs-pill">미지급</span></div>
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
  state.popup = () => openUsers();
  const data = state.data;
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
          <tr><th>계 급</th><td><input name="rank" maxlength="4" value="${escapeAttr(person.rank || "")}" ${row.locked ? "readonly" : ""} /></td></tr>
          <tr><th>군 번</th><td><input name="sn" maxlength="10" value="${escapeAttr(person.sn || "")}" ${row.locked ? "readonly" : ""} /></td></tr>
          <tr><th>성 명</th><td><input name="name" maxlength="5" value="${escapeAttr(person.name || "")}" ${row.locked ? "readonly" : ""} /></td></tr>
          <tr><th>총기종류</th><td><select name="model" ${row.locked ? "disabled" : ""}>${options}</select></td></tr>
          <tr><th>총기번호</th><td><input name="serial" maxlength="20" value="${escapeAttr(gun.serial || "")}" ${row.locked ? "readonly" : ""} /></td></tr>
        </table>
        <div class="win-actions">
          <button class="gb" type="button" id="deleteUser" ${row.locked || !row.person ? "disabled" : ""}>삭제</button>
          <button class="gb" type="submit" ${row.locked ? "disabled" : ""}>저장</button>
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
  const order = picked ? picked.no : data.locker.name;
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
        <tr>${fixed("기타")}<th>젤루텍</th><td><input id="issueLabel" maxlength="10" value="${escapeAttr(label)}" /></td></tr>
      </table>
      <div class="win-actions">
        <button type="button" class="gb" id="saveIssue">저장</button>
        <button type="button" class="gb" id="closeIssue">취소</button>
      </div>
    </div>`, true);
  $("closeIssue").onclick = closeModal;
  $("saveIssue").onclick = async () => {
    const result = await post("/api/purpose", { label: $("issueLabel").value });
    if (!result.ok) {
      alertBox(result.data.error || "저장하지 못했습니다.");
      return;
    }
    toast("저장했습니다.");
    await refresh(false);
  };
}

function openLockerDialog(id) {
  const adding = !id;
  const board = adding ? null : (state.data.boards || []).find((item) => item.id === id) || state.data.locker;
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
  if (del && !adding) del.onclick = () => confirmDelete("/api/locker/delete", { id: board.id }, "이 보관함 등록을 지우겠습니까?");
}

function openHistory() {
  state.popup = openHistory;
  openModal(`
    <h3 class="win-title">이력관리</h3>
    <div class="win-body">
      <form class="filters" id="historyForm">
        <span>기간</span>
        <input name="from" type="date" /> ~ <input name="to" type="date" />
        <label><input type="radio" name="find" value="type" checked /> 타입</label>
        <label><input type="radio" name="find" value="sn" /> 군번</label>
        <label><input type="radio" name="find" value="name" /> 이름</label>
        <input name="word" />
        <button class="gb" type="button" id="csvBtn" disabled>저장</button>
        <button class="gb" type="button" id="printBtn" disabled>인쇄</button>
        <button class="gb" type="submit">검색</button>
        <button class="gb" type="button" id="closeHistory">닫기 (ESC)</button>
      </form>
      <div class="scroll" id="historyTable"><table class="history-table"><thead><tr><th>No.</th><th>이벤트 시간</th><th>구분</th><th>타입</th><th>군번</th><th>이름</th><th>이벤트 로그</th></tr></thead><tbody></tbody></table></div>
    </div>`);
  $("closeHistory").onclick = closeModal;
  $("historyForm").onsubmit = (ev) => { ev.preventDefault(); loadHistory(); };
  $("csvBtn").onclick = () => downloadHistory(historyView);
  $("printBtn").onclick = () => window.print();
  loadHistory(true);
}

async function loadHistory(initial) {
  const form = $("historyForm");
  if (!form) return;
  const body = formValues(form);
  historyQuery = { from: body.from || "", to: body.to || "" };
  if (!initial) {
    if (body.find === "sn") historyQuery.sn = body.word || "";
    if (body.find === "name") historyQuery.name = body.word || "";
  }
  const result = await api("/api/history?" + new URLSearchParams(historyQuery).toString());
  let rows = result.data.rows || [];
  if (!initial && body.find === "type" && body.word) {
    const word = body.word;
    rows = rows.filter((row) => `${row.model || ""} ${row.detail || ""} ${row.action || ""} ${row.purpose || ""}`.includes(word));
  }
  historyView = rows;
  const table = $("historyTable");
  if (!table) return;
  table.innerHTML = `<table class="history-table"><thead><tr><th>No.</th><th>이벤트 시간</th><th>구분</th><th>타입</th><th>군번</th><th>이름</th><th>이벤트 로그</th></tr></thead><tbody>
    ${rows.map((row, index) => `<tr><td>${index + 1}</td><td>${escapeAttr(row.time)}</td><td>${escapeAttr(row.locker)}</td><td>${escapeAttr(row.model || row.category || "")}</td><td>${escapeAttr(row.sn)}</td><td>${escapeAttr(row.name)}</td><td>${escapeAttr(row.detail)}</td></tr>`).join("")}
  </tbody></table>`;
  const ready = Boolean(result.ok);
  const csv = $("csvBtn");
  const print = $("printBtn");
  if (csv) csv.disabled = !ready;
  if (print) print.disabled = !ready;
}

function csvCell(value) {
  const text = String(value == null ? "" : value);
  return `"${text.replaceAll('"', '""')}"`;
}

function downloadHistory(rows) {
  const header = ["No.", "이벤트 시간", "구분", "타입", "군번", "이름", "이벤트 로그"];
  const lines = [header.map(csvCell).join(",")];
  rows.forEach((row, index) => {
    lines.push([
      index + 1,
      row.time,
      row.locker,
      row.model || row.category || "",
      row.sn,
      row.name,
      row.detail,
    ].map(csvCell).join(","));
  });
  const blob = new Blob(["\uFEFF" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "이력관리.csv";
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
  if (!result.data || !result.data.locker) return;
  state.data = result.data;
  const layer = $("fullScreen");
  if (layer && !layer.hidden) {
    layer.innerHTML = fullScreenHtml(state.data);
    const close = $("closeFull");
    if (close) close.onclick = closeFullScreen;
  }
  if (soft && (typing || !$("modal").hidden)) {
    paintChrome();
    paintStats();
    paintGrid();
    return;
  }
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

$("memoSave").onclick = saveMemo;
$("memoLoad").onclick = async () => { await refresh(true); toast("메모를 다시 읽었습니다."); };
$("memoMenu").onclick = () => $("memoText").focus();
window.addEventListener("resize", alignLeftColumn);

refresh(false);
setInterval(() => refresh(true), 2000);
setInterval(() => {
  const date = $("fullDate");
  if (date) date.textContent = dateText(new Date());
}, 1000);
