(function () {
  const NOTICE_MS = 1000;

  const app = document.querySelector(".app");
  const clockEl = document.getElementById("clock");
  const settingsBtn = document.getElementById("settingsBtn");
  const rifleBtn = document.getElementById("rifleBtn");
  const approverBadge = document.getElementById("approverBadge");
  const approverBadgeText = document.getElementById("approverBadgeText");
  const screens = Array.from(document.querySelectorAll(".screen[data-screen]"));
  const subSlotGrid = document.getElementById("subSlotGrid");
  const userSlotGrid = document.getElementById("userSlotGrid");
  const lockerBox = document.getElementById("lockerBox");
  const kbOverlay = document.getElementById("kbOverlay");
  const kbInput = document.getElementById("kbInput");
  const kbKeys = document.getElementById("kbKeys");

  let mode = "standby";
  let adminTarget = null;
  let slotIntent = null;
  let opsKind = null;
  let opsPurpose = null;
  let approverUserId = null;
  let approverLabel = "";

  let current = "standby";
  let masters = { 1: null, 2: null, 3: null, 4: null, 5: null };
  let subs = {};
  let users = {};
  let stats = { issue_counts: {} };
  let enrollUserId = 1;
  let selectedSlot = 1;
  let selectedSubSlot = 1;
  let subSlotPage = 0;
  let selectedUserSlot = 1;
  let authArmed = false;
  let noticeTimer = null;
  let noticeReturn = null;
  let lockerSens = "";
  let lockerSensTimer = null;
  let renameSlot = null;
  /** @type {'master'|'sub'|'user'|null} */
  let renameKind = null;
  let lockerKind = "rifle";
  let logoHoldTimer = null;
  let logoHoldPointerId = null;

  const DESIGN_W = 1280;
  const DESIGN_H = 800;

  function fitDesignStage() {
    if (!app) return;
    const vw = window.innerWidth || document.documentElement.clientWidth || DESIGN_W;
    const vh = window.innerHeight || document.documentElement.clientHeight || DESIGN_H;
    let scale = Math.min(vw / DESIGN_W, vh / DESIGN_H);
    if (!isFinite(scale) || scale <= 0.05) scale = 1;
    app.style.width = DESIGN_W + "px";
    app.style.height = DESIGN_H + "px";
    app.style.transform = "translate(-50%, -50%) scale(" + scale + ")";
    document.documentElement.style.setProperty("--stage-scale", String(scale));
  }

  let sensorConnected = null; // null=unknown
  let boardConnected = null;

  function interruptTo(screen) {
    clearNoticeTimer();
    noticeReturn = null;
    mode = "standby";
    authArmed = false;
    showScreen(screen);
  }

  function setSensorConnected(ok) {
    const next = !!ok;
    const prev = sensorConnected;
    sensorConnected = next;
    if (!next) {
      interruptTo("sensor-missing");
      return;
    }
    if (prev === false && current === "sensor-missing") {
      if (boardConnected === false) interruptTo("board-missing");
      else goStandby();
    }
  }

  function setBoardConnected(ok) {
    const next = !!ok;
    const prev = boardConnected;
    boardConnected = next;
    if (sensorConnected === false) return;
    if (!next) {
      if (current !== "board-missing") interruptTo("board-missing");
      return;
    }
    if (prev === false && current === "board-missing") goStandby();
  }

  function applySensorState(data) {
    if (!data) return;
    if (data.sensor && typeof data.sensor.connected === "boolean") {
      setSensorConnected(data.sensor.connected);
      return;
    }
    if (data.status && typeof data.status.connected === "boolean") {
      setSensorConnected(data.status.connected);
    }
  }

  let kbLang = "ko";
  let composer = new window.HangulComposer();
  let keyboard = window.buildKeyboard(kbKeys, onVirtualKey);

  function pad(n) {
    return String(n).padStart(2, "0");
  }

  let omsClockOffset = 0;

  function clockNow() {
    return new Date(Date.now() + omsClockOffset);
  }

  function pullOmsTime() {
    fetch("/api/time")
      .then(function (res) { return res.json(); })
      .then(function (data) {
        if (data && data.synced && typeof data.epoch === "number") {
          omsClockOffset = data.epoch * 1000 - Date.now();
        }
      })
      .catch(function () {});
  }

  function tick() {
    const now = clockNow();
    const parts = {};
    new Intl.DateTimeFormat("ko-KR", {
      timeZone: "Asia/Seoul",
      year: "numeric",
      month: "numeric",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hourCycle: "h23",
    }).formatToParts(now).forEach(function (part) {
      parts[part.type] = part.value;
    });
    clockEl.textContent =
      parts.year +
      "년 " +
      Number(parts.month) +
      "월 " +
      Number(parts.day) +
      "일 " +
      parts.hour +
      ":" +
      parts.minute +
      ":" +
      parts.second;
    clockEl.setAttribute("datetime", now.toISOString());
  }

  function setHint(screenName, text) {
    const screen = document.querySelector('.screen[data-screen="' + screenName + '"]');
    if (!screen) return;
    const hint = screen.querySelector(".prompt-hint");
    if (hint) hint.textContent = text || "";
  }

  function clearNoticeTimer() {
    if (noticeTimer) {
      clearTimeout(noticeTimer);
      noticeTimer = null;
    }
  }

  const scanCountdownEl = document.getElementById("scanCountdown");
  let scanLeft = 0;
  let scanTimer = null;

  function stopScanCountdown() {
    if (scanTimer) {
      clearInterval(scanTimer);
      scanTimer = null;
    }
    scanLeft = 0;
    if (scanCountdownEl) scanCountdownEl.hidden = true;
  }

  function startScanCountdown() {
    stopScanCountdown();
    if (!scanCountdownEl) return;
    scanLeft = 10;
    scanCountdownEl.hidden = false;
    scanCountdownEl.textContent = String(scanLeft);
    scanTimer = setInterval(function () {
      scanLeft -= 1;
      if (scanLeft <= 0) {
        stopScanCountdown();
        if (mode === "enroll") {
          postJson("/api/enroll/abort").catch(function () {});
        }
        goStandby();
        return;
      }
      scanCountdownEl.textContent = String(scanLeft);
    }, 1000);
  }

  function showScreen(name) {
    current = name;
    app.setAttribute("data-screen", name);
    screens.forEach(function (screen) {
      const active = screen.getAttribute("data-screen") === name;
      screen.classList.toggle("is-active", active);
      if (active) screen.removeAttribute("hidden");
      else screen.setAttribute("hidden", "");
    });
    updateFooterChrome();
    if (name === "scan1" || name === "scan2") startScanCountdown();
    else stopScanCountdown();
    if (name === "locker-status") startLockerSensorPoll();
  }

  function showNotice(name, next, ms) {
    clearNoticeTimer();
    noticeReturn = next || null;
    showScreen(name);
    noticeTimer = setTimeout(function () {
      noticeTimer = null;
      const fn = noticeReturn;
      noticeReturn = null;
      if (fn) fn();
    }, ms == null ? NOTICE_MS : ms);
  }

  function updateFooterChrome() {
    const accessModes = {
      ops: true,
      purpose: true,
      "ops-scan": true,
      "door-wait": true,
      admin: true,
    };
    const accessOpen = !!approverUserId && !!approverLabel && !!accessModes[mode];
    app.classList.toggle("is-access-open", accessOpen);
    if (accessOpen) {
      approverBadge.hidden = false;
      approverBadge.removeAttribute("hidden");
      approverBadgeText.textContent = approverLabel + " 접근 허용중";
    } else {
      approverBadge.hidden = true;
      approverBadge.setAttribute("hidden", "");
    }
    if (rifleBtn) {
      rifleBtn.hidden = current !== "standby";
    }
  }

  function goStandby() {
    mode = "standby";
    adminTarget = null;
    slotIntent = null;
    opsKind = null;
    opsPurpose = null;
    approverUserId = null;
    approverLabel = "";
    authArmed = false;
    clearNoticeTimer();
    noticeReturn = null;
    if (sensorConnected === false) {
      showScreen("sensor-missing");
      return;
    }
    showScreen("standby");
  }

  function slotInfo(slot) {
    return masters[String(slot)] || masters[slot] || null;
  }

  function subInfo(slot) {
    return subs[String(slot)] || subs[slot] || null;
  }

  function userInfo(slot) {
    return users[String(slot)] || users[slot] || null;
  }

  function isSlotRegistered(slot) {
    const m = slotInfo(slot);
    return !!(m && m.user_id);
  }

  function isSubRegistered(slot) {
    const s = subInfo(slot);
    return !!(s && s.user_id);
  }

  function isUserRegistered(slot) {
    const u = userInfo(slot);
    return !!(u && u.user_id);
  }

  function userDisplayName(slot) {
    const u = userInfo(slot);
    if (u && u.name) return u.name;
    return "사용자" + slot;
  }

  function splitRankName(text) {
    const raw = String(text || "").trim();
    const cut = raw.indexOf(" ");
    if (cut > 0 && cut <= 4) {
      return { rank: raw.slice(0, cut), name: raw.slice(cut + 1).trim() };
    }
    return { rank: "", name: raw };
  }

  function fillPersonLabel(parent, text) {
    const parts = splitRankName(text);
    parent.textContent = "";
    if (parts.rank) {
      const rank = document.createElement("span");
      rank.className = "person-rank";
      rank.textContent = parts.rank;
      parent.appendChild(rank);
    }
    const name = document.createElement("span");
    name.className = "person-name";
    name.textContent = parts.name;
    parent.appendChild(name);
  }

  function masterDisplayName(slot) {
    const m = slotInfo(slot);
    if (m && m.name) return m.name;
    return "마스터" + slot + "관리자";
  }

  function subDisplayName(slot) {
    const s = subInfo(slot);
    if (s && s.name) return s.name;
    return "서브" + slot + "관리자";
  }

  function hasAnyMaster() {
    return [1, 2, 3, 4, 5].some(isSlotRegistered);
  }

  function collectIds(mapObj) {
    const ids = [];
    Object.keys(mapObj || {}).forEach(function (k) {
      const item = mapObj[k];
      if (item && item.user_id) ids.push(Number(item.user_id));
    });
    return ids;
  }

  function registeredMasterUserIds() {
    const ids = [];
    [1, 2, 3, 4, 5].forEach(function (slot) {
      const m = slotInfo(slot);
      if (m && m.user_id) ids.push(Number(m.user_id));
    });
    return ids;
  }

  function isRegisteredMasterUser(userId) {
    return registeredMasterUserIds().indexOf(Number(userId)) !== -1;
  }

  function isRegisteredSubUser(userId) {
    return collectIds(subs).indexOf(Number(userId)) !== -1;
  }

  function isRegisteredLockerUser(userId) {
    return collectIds(users).indexOf(Number(userId)) !== -1;
  }

  function isAdminOperator(userId) {
    return isRegisteredMasterUser(userId) || isRegisteredSubUser(userId);
  }

  function findUserSlotById(userId) {
    const uid = Number(userId);
    for (let i = 1; i <= 10; i++) {
      const u = userInfo(i);
      if (u && Number(u.user_id) === uid) return i;
    }
    return null;
  }

  function findMasterSlotById(userId) {
    const uid = Number(userId);
    for (let i = 1; i <= 5; i++) {
      const m = slotInfo(i);
      if (m && Number(m.user_id) === uid) return i;
    }
    return null;
  }

  function findSubSlotById(userId) {
    const uid = Number(userId);
    for (let i = 1; i <= 30; i++) {
      const s = subInfo(i);
      if (s && Number(s.user_id) === uid) return i;
    }
    return null;
  }

  function labelForAdmin(userId) {
    const ms = findMasterSlotById(userId);
    if (ms) return masterDisplayName(ms);
    const ss = findSubSlotById(userId);
    if (ss) return subDisplayName(ss);
    return "관리자";
  }

  function subUserIdForSlot(slot) {
    return 100 + Number(slot);
  }

  function lockerUserIdForSlot(slot) {
    return 200 + Number(slot);
  }

  function applyStore(data) {
    if (!data) return;
    // Prefer wrapped store, then nested snapshot (masters=full file), then top-level fields
    const nested = data.store || (data.masters && data.masters.masters ? data.masters : null);
    if (nested) {
      if (nested.masters) masters = nested.masters;
      if (nested.subs) subs = nested.subs;
      if (nested.users) users = nested.users;
      if (nested.stats) stats = nested.stats;
    } else if (data.masters) {
      masters = data.masters;
    }
    if (data.subs) subs = data.subs;
    if (data.users) users = data.users;
    if (data.stats) stats = data.stats;
    const label = (data && data.purpose_label) || (nested && nested.purpose_label) || "젤루텍";
    document.querySelectorAll('[data-purpose="젤루텍"]').forEach(function (btn) {
      btn.textContent = String(label).trim() || "젤루텍";
    });
    const kind = (data && data.locker_kind) || (nested && nested.locker_kind) || lockerKind;
    if (kind === "rifle" || kind === "pistol") lockerKind = kind;
    applyLockerKindUi();
    updateMasterMenuStyles();
    updateSubSlotStyles();
    updateUserSlotStyles();
    renderLocker();
    applySensorState(data);
  }

  function weaponImageSrc() {
    return lockerKind === "pistol" ? "/static/img/pistol.png" : "/static/img/rifle.png";
  }

  function applyLockerKindUi() {
    const pistol = lockerKind === "pistol";
    const title = document.getElementById("lockerStatusTitle");
    document.title = pistol ? "지문 인식 스마트 권총 보관함" : "지문 인식 스마트 소총 보관함";
    if (title) title.textContent = pistol ? "권총보관함 보관현황" : "소총보관함 보관현황";
    const statusScreen = document.querySelector('.screen[data-screen="locker-status"]');
    if (statusScreen) statusScreen.setAttribute("aria-label", pistol ? "권총보관함 보관현황" : "소총보관함 보관현황");
    const sub = document.querySelector(".scan-sub");
    if (sub) sub.textContent = pistol ? "지문 인식 스마트 권총 보관함" : "지문 인식 스마트 소총 보관함";
    const btn = document.getElementById("rifleBtn");
    if (btn) btn.classList.toggle("rifle-btn--pistol", pistol);
    const app = document.querySelector(".app");
    if (app) app.classList.toggle("is-pistol", pistol);
    const btnImg = document.getElementById("rifleBtnImg");
    if (btnImg) {
      btnImg.src = weaponImageSrc();
      btnImg.alt = pistol ? "권총 보관현황" : "소총 보관현황";
    }
    document.querySelectorAll(".locker-rifle-img").forEach(function (img) {
      img.src = weaponImageSrc();
    });
  }

  function updateMasterMenuStyles() {
    document.querySelectorAll('[data-action="pick-master"]').forEach(function (btn) {
      const slot = Number(btn.getAttribute("data-slot") || "0");
      const reg = isSlotRegistered(slot);
      btn.classList.toggle("is-unregistered", !reg);
      btn.classList.toggle("is-registered", reg);
      btn.textContent = masterDisplayName(slot);
    });
  }

  function buildSubSlotGrid() {
    if (!subSlotGrid || subSlotGrid.childElementCount) return;
    for (let i = 1; i <= 30; i++) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "slot-btn";
      btn.textContent = "S" + i;
      btn.setAttribute("data-action", "pick-sub-slot");
      btn.setAttribute("data-slot", String(i));
      subSlotGrid.appendChild(btn);
    }
  }

  function buildUserSlotGrid() {
    if (!userSlotGrid) return;
    if (!userSlotGrid.childElementCount) {
      for (let i = 1; i <= 10; i++) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "user-slot-btn";
        btn.setAttribute("data-action", "pick-user-slot");
        btn.setAttribute("data-slot", String(i));
        const label = document.createElement("span");
        label.className = "user-slot-label";
        btn.appendChild(label);
        userSlotGrid.appendChild(btn);
      }
    }
    updateUserSlotStyles();
  }

  function updateSubSlotStyles() {
    document.querySelectorAll('[data-action="pick-sub-slot"]').forEach(function (btn) {
      const slot = Number(btn.getAttribute("data-slot") || "0");
      const reg = isSubRegistered(slot);
      btn.classList.toggle("is-unregistered", !reg);
      btn.classList.toggle("is-registered", reg);
      let label = btn.querySelector(".user-slot-label");
      if (!label) {
        label = document.createElement("span");
        label.className = "user-slot-label";
        btn.textContent = "";
        btn.appendChild(label);
      }
      if (reg) fillPersonLabel(label, subDisplayName(slot));
      else {
        label.textContent = "";
        const name = document.createElement("span");
        name.className = "person-name";
        name.textContent = "S" + slot;
        label.appendChild(name);
      }
    });
    renderSubSlotPage();
  }

  function renderSubSlotPage() {
    const start = subSlotPage * 15;
    document.querySelectorAll('[data-action="pick-sub-slot"]').forEach(function (btn) {
      const slot = Number(btn.getAttribute("data-slot") || "0");
      btn.classList.toggle("is-off-page", slot <= start || slot > start + 15);
    });
    const prev = document.getElementById("subPagePrev");
    const next = document.getElementById("subPageNext");
    if (prev) prev.hidden = subSlotPage <= 0;
    if (next) next.hidden = subSlotPage >= 1;
  }

  function updateUserSlotStyles() {
    document.querySelectorAll('[data-action="pick-user-slot"]').forEach(function (btn) {
      const slot = Number(btn.getAttribute("data-slot") || "0");
      const reg = isUserRegistered(slot);
      btn.classList.toggle("is-unregistered", !reg);
      btn.classList.toggle("is-registered", reg);
      const label = btn.querySelector(".user-slot-label");
      if (label) fillPersonLabel(label, userDisplayName(slot));
    });
  }

  function renderLocker() {
    const box = lockerBox || document.getElementById("lockerBox");
    const nameRow = document.getElementById("lockerNameRow");
    const rifleRow = document.getElementById("lockerRifleRow");
    if (box && nameRow && rifleRow && !nameRow.childElementCount) {
      for (let i = 1; i <= 10; i++) {
        const nameCell = document.createElement("th");
        nameCell.className = "locker-board__name";
        nameCell.setAttribute("data-slot", String(i));
        nameCell.scope = "col";
        const nameSpan = document.createElement("span");
        nameSpan.className = "locker-name";
        nameCell.appendChild(nameSpan);
        nameRow.appendChild(nameCell);

        const rifleCell = document.createElement("td");
        rifleCell.className = "locker-board__cell";
        rifleCell.setAttribute("data-slot", String(i));
        const img = document.createElement("img");
        img.className = "locker-rifle-img";
        img.src = weaponImageSrc();
        img.alt = "";
        img.hidden = true;
        rifleCell.appendChild(img);
        rifleRow.appendChild(rifleCell);
      }
    }

    let stored = 0;
    let issued = 0;
    let total = 0;
    for (let i = 1; i <= 10; i++) {
      const u = userInfo(i);
      const reg = !!(u && u.user_id);
      const nameCell = nameRow && nameRow.querySelector('.locker-board__name[data-slot="' + i + '"]');
      const rifleCell = rifleRow && rifleRow.querySelector('.locker-board__cell[data-slot="' + i + '"]');
      const img = rifleCell && rifleCell.querySelector(".locker-rifle-img");

      if (nameCell) {
        const nameSpan = nameCell.querySelector(".locker-name");
        if (nameSpan) fillPersonLabel(nameSpan, userDisplayName(i));
        else nameCell.textContent = userDisplayName(i);
      }
      if (rifleCell) rifleCell.classList.toggle("is-empty", !reg);
      const present = gunInSlot(i);
      if (img) {
        img.src = weaponImageSrc();
        if (reg) {
          img.hidden = false;
          img.classList.toggle("is-issued", !present);
          img.classList.toggle("is-stored", present);
        } else {
          img.hidden = true;
          img.classList.remove("is-issued", "is-stored");
        }
      }
      if (reg) {
        total += 1;
        if (present) stored += 1;
        else issued += 1;
      }
    }

    const counts = {
      "사격": 0,
      "훈련": 0,
      "정비": 0,
      "근무": 0,
      "기타": 0,
      "젤루텍": 0,
    };
    for (let i = 1; i <= 10; i += 1) {
      const u = users[String(i)];
      if (u && u.user_id && !gunInSlot(i)) {
        const p = counts.hasOwnProperty(u.purpose) ? u.purpose : "기타";
        counts[p] += 1;
      }
    }
    const shoot = counts["사격"];
    const train = counts["훈련"];
    const maint = counts["정비"];
    const duty = counts["근무"];
    const other = counts["기타"] + counts["젤루텍"];
    const sum = shoot + train + maint + duty + other;

    fitLockerNames();

    setText("statReg", total);
    setText("statIssued", issued);
    setText("statStored", stored);
    setText("statUnreg", 10 - total);
    setText("statUnissued", 10 - total);
    setText("statShoot", shoot);
    setText("statTrain", train);
    setText("statMaint", maint);
    setText("statDuty", duty);
    setText("statOther", other);
    setText("statSum", sum);
  }

  function gunInSlot(slot) {
    // Missing board SENS must NOT look like "all stored" — that hid pistol board faults
    // while OMS (which requires SENS) looked empty / frozen.
    if (lockerSens.length !== 10) return false;
    return lockerSens.charAt(slot - 1) === "1";
  }

  function startLockerSensorPoll() {
    stopLockerSensorPoll();
    refreshLockerSensors();
    lockerSensTimer = setInterval(refreshLockerSensors, 1000);
  }

  function stopLockerSensorPoll() {
    if (lockerSensTimer) {
      clearInterval(lockerSensTimer);
      lockerSensTimer = null;
    }
  }

  function refreshLockerSensors() {
    fetch("/api/locker/status")
      .then(function (res) {
        if (!res.ok) return null;
        return res.json();
      })
      .then(function (data) {
        if (!data || typeof data.sens !== "string" || data.sens.length !== 10) {
          setBoardConnected(false);
          return;
        }
        setBoardConnected(true);
        if (data.sens === lockerSens) return;
        lockerSens = data.sens;
        if (current === "locker-status") renderLocker();
      })
      .catch(function () { setBoardConnected(false); });
  }

  function setText(id, val) {
    const el = document.getElementById(id);
    if (el) el.textContent = String(val);
  }

  function openMasterMenu() {
    updateMasterMenuStyles();
    showScreen("master-menu");
  }

  function openSubSlotMenu() {
    buildSubSlotGrid();
    subSlotPage = 0;
    updateSubSlotStyles();
    showScreen("sub-slot-menu");
  }

  function openUserSlotMenu() {
    buildUserSlotGrid();
    updateUserSlotStyles();
    showScreen("user-slot-menu");
  }

  function fitLockerNames() {
    const nameRow = document.getElementById("lockerNameRow");
    if (!nameRow) return;
    nameRow.querySelectorAll(".person-rank, .person-name").forEach(function (line) {
      const cell = line.closest(".locker-board__name");
      if (!cell || cell.clientWidth < 8) return;
      const max = cell.clientWidth - 8;
      let size = line.classList.contains("person-rank") ? 16 : 20;
      line.style.fontSize = size + "px";
      while (size > 14 && line.scrollWidth > max) {
        size -= 1;
        line.style.fontSize = size + "px";
      }
    });
  }

  function openLockerStatus() {
    showScreen("locker-status");
    refreshStore().finally(function () {
      renderLocker();
    });
  }

  function openOpsMenu(userId) {
    clearNoticeTimer();
    mode = "ops";
    approverUserId = Number(userId);
    approverLabel = labelForAdmin(userId);
    opsKind = null;
    opsPurpose = null;
    updateFooterChrome();
    showScreen("ops-menu");
  }

  function startOpsUserScan() {
    mode = "ops-scan";
    updateFooterChrome();
    showScreen("scan1");
    setHint("scan1", "등록된 사용자 지문을 스캔하세요");
  }

  function startDoorWait() {
    mode = "door-wait";
    updateFooterChrome();
    showScreen("scan1");
    setHint("scan1", "");
  }

  function setOpsDoneText() {
    const line1 = document.getElementById("opsDoneLine1");
    const line2 = document.getElementById("opsDoneLine2");
    const weapon = lockerKind === "pistol" ? "권총" : "소총";
    if (opsKind === "store") {
      if (line1) line1.textContent = weapon + "을 보관하였습니다.";
    } else {
      if (line1) line1.textContent = weapon + "을 불출하였습니다.";
    }
    if (line2) line2.textContent = "문을 닫아주세요!";
  }

  let opsBusy = false;

  function afterUserMatched(slot, showDoneMessage) {
    const resolvedSlot = Number(slot) || 0;
    const line = document.getElementById("doorOpenedLine");
    if (line) {
      line.textContent =
        (resolvedSlot ? userDisplayName(resolvedSlot) : "사용자") + "의 보관함문이 열렸습니다.";
    }

    function finishUi() {
      showNotice("door-opened", function () {
        if (showDoneMessage) {
          setOpsDoneText();
          showNotice("ops-done", startDoorWait, 2000);
        } else {
          startDoorWait();
        }
      }, 2000);
    }

    if (!showDoneMessage) {
      postJson("/api/locker/open", { slot: resolvedSlot }).catch(function () {});
      finishUi();
      return;
    }

    if (opsBusy) return;
    if (!resolvedSlot || !isUserRegistered(resolvedSlot)) {
      flashNoAccessThen(function () {
        mode = "ops-scan";
        showScreen("scan1");
        setHint("scan1", "등록된 사용자 지문을 스캔하세요");
      });
      return;
    }

    const kind = opsKind === "store" ? "store" : "issue";
    const purpose = opsKind === "issue" ? opsPurpose || "기타" : "";
    opsBusy = true;
    // Leave ops-scan immediately so a held finger can't double-fire identify
    mode = "door-wait";
    postJson("/api/user/ops", { slot: resolvedSlot, kind: kind, purpose: purpose })
      .then(function (data) {
        applyStore(data);
      })
      .catch(function () {
        return refreshStore();
      })
      .finally(function () {
        opsBusy = false;
        finishUi();
      });
  }

  function flashAlreadyRegistered(backFn) {
    const title = document.querySelector('.screen[data-screen="no-access"] .prompt-title');
    const prev = title ? title.textContent : "";
    if (title) title.textContent = "이미 다른 사용자로 등록된 지문입니다.";
    showNotice("no-access", function () {
      if (title) title.textContent = prev || "접근 권한이 없습니다.";
      if (backFn) backFn();
    });
  }

  function defaultRenameName(kind, slot) {
    if (kind === "master") return masterDisplayName(slot);
    if (kind === "sub") return subDisplayName(slot);
    return userDisplayName(slot);
  }

  function openRename(kind, slot) {
    if (kind === "master" && !isSlotRegistered(slot)) return;
    if (kind === "sub" && !isSubRegistered(slot)) return;
    if (kind === "user" && !isUserRegistered(slot)) return;
    renameKind = kind;
    renameSlot = slot;
    composer.set(defaultRenameName(kind, slot));
    kbInput.value = composer.result();
    kbLang = "ko";
    keyboard.render(kbLang);
    kbOverlay.hidden = false;
    setTimeout(function () {
      kbInput.focus();
      kbInput.select();
    }, 50);
  }

  function closeRename() {
    renameSlot = null;
    renameKind = null;
    kbOverlay.hidden = true;
  }

  function syncKbInput() {
    kbInput.value = composer.result();
  }

  function onVirtualKey(ch) {
    composer.input(ch);
    syncKbInput();
  }

  async function commitRename() {
    if (!renameSlot || !renameKind) return;
    const name = (kbInput.value || "").trim() || defaultRenameName(renameKind, renameSlot);
    try {
      const data = await postJson("/api/rename", {
        kind: renameKind,
        slot: renameSlot,
        name: name,
      });
      applyStore(data);
      closeRename();
    } catch (err) {
      alert("이름 변경 실패: " + err.message);
    }
  }

  async function postJson(url, body) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    let data = null;
    try {
      data = await res.json();
    } catch (_) {}
    if (!res.ok) {
      const detail = (data && data.detail) || res.statusText;
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return data;
  }

  async function refreshStore() {
    try {
      const res = await fetch("/api/status");
      const data = await res.json();
      applyStore(data);
      applySensorState(data);
    } catch (_) {}
  }

  async function onSettings() {
    if (mode === "ops" || mode === "purpose" || mode === "ops-scan" || mode === "door-wait") {
      return;
    }
    // Show scan UI immediately so the gear button never feels "dead"
    showScreen("scan1");
    setHint("scan1", "준비 중...");
    try {
      await Promise.race([
        refreshStore(),
        new Promise(function (_, reject) {
          setTimeout(function () {
            reject(new Error("timeout"));
          }, 2500);
        }),
      ]);
    } catch (_) {}

    if (!hasAnyMaster()) {
      mode = "enroll";
      adminTarget = "master";
      enrollUserId = 1;
      authArmed = false;
      setHint("scan1", "등록 준비 중... 잠시만 기다려주세요");
      try {
        await postJson("/api/enroll/start", { user_id: enrollUserId });
        setHint("scan1", "센서 램프가 켜지면 손가락을 올려주세요");
      } catch (err) {
        setHint("scan1", "인식기 연결 실패: " + err.message);
        showNotice("denied", goStandby);
      }
      return;
    }

    mode = "auth";
    authArmed = true;
    setHint("scan1", "등록된 마스터 관리자 지문을 스캔하세요");
  }

  function openAdmin(userId) {
    authArmed = false;
    mode = "admin";
    if (userId) {
      approverUserId = Number(userId);
      approverLabel = labelForAdmin(userId);
    }
    showScreen("role-menu");
  }

  function startEnrollScan(userId, afterFailScreen) {
    enrollUserId = userId;
    showScreen("scan1");
    setHint("scan1", "등록 준비 중...");
    postJson("/api/enroll/start", { user_id: enrollUserId })
      .then(function () {
        setHint("scan1", "센서 램프가 켜지면 손가락을 올려주세요");
      })
      .catch(function (err) {
        setHint("scan1", "실패: " + err.message);
        setTimeout(function () {
          showScreen(afterFailScreen || "action-menu");
        }, NOTICE_MS);
      });
  }

  function flashNoAccessThen(resumeFn) {
    showNotice("no-access", resumeFn || goStandby);
  }

  function flashNameLocked(resumeFn) {
    const title = document.querySelector('.screen[data-screen="no-access"] .prompt-title');
    const prev = title ? title.textContent : "";
    if (title) title.textContent = "이름은 OMS에서만 변경됩니다.";
    showNotice("no-access", function () {
      if (title) title.textContent = prev || "접근 권한이 없습니다.";
      if (resumeFn) resumeFn();
    });
  }

  function flashDeniedThen(resumeFn) {
    showNotice("denied", resumeFn || goStandby);
  }

  function onDaemonEvent(msg) {
    const ev = msg.event;
    if (ev === "snapshot" || ev === "store_update") applyStore(msg);
    if (ev === "status" || ev === "connected" || ev === "snapshot") applySensorState(msg);
    if (ev === "error" && (msg.message === "no_ttyACM" || msg.message === "open_fail")) {
      setSensorConnected(false);
    }
    if (ev === "connected") setSensorConnected(true);

    if (mode === "enroll" || (mode === "admin" && (current === "scan1" || current === "scan2"))) {
      if (ev === "enroll_waiting" && (current === "scan1" || current === "scan2")) {
        setHint(current, "센서 램프가 켜지면 손가락을 올려주세요");
      }
      if (ev === "enroll_scan") {
        const count = Number(msg.count || 0);
        if (count >= 1 && (current === "scan1" || current === "scan2")) {
          showScreen("scan2");
          setHint("scan2", "같은 손가락을 다시 올려주세요");
        }
        if (count >= 2) setHint("scan2", "등록 처리 중...");
      }
      if (ev === "enroll_retry" && (current === "scan1" || current === "scan2")) {
        setHint(current, "다시 시도하세요. 손가락을 올려주세요");
      }
      if (ev === "enroll_done") {
        if (msg.ok) showScreen("confirm");
        else {
          const err = String(msg.error || "");
          if (err === "EXIST_FINGER" || err === "EXIST_ID") {
            flashAlreadyRegistered(function () {
              if (mode === "enroll") goStandby();
              else if (adminTarget === "sub") openSubSlotMenu();
              else if (adminTarget === "user") openUserSlotMenu();
              else showScreen("action-menu");
            });
          } else {
            setHint(current === "scan2" ? "scan2" : "scan1", "등록 실패 (" + err + ")");
            setTimeout(function () {
              if (mode === "enroll") goStandby();
              else if (adminTarget === "sub") showScreen("sub-action-menu");
              else if (adminTarget === "user") showScreen("user-action-menu");
              else showScreen("action-menu");
            }, NOTICE_MS);
          }
        }
      }
      if (mode === "enroll") return;
    }

    if (ev !== "identify") return;
    if (kbOverlay && !kbOverlay.hidden) return;

    if (mode === "auth" && authArmed && current === "scan1") {
      if (msg.ok && isRegisteredMasterUser(msg.user_id)) openAdmin(msg.user_id);
      else if (msg.ok && (isRegisteredSubUser(msg.user_id) || isRegisteredLockerUser(msg.user_id))) {
        flashNoAccessThen(goStandby);
      } else flashDeniedThen(goStandby);
      return;
    }

    if ((mode === "standby" && current === "standby") || current === "locker-status") {
      if (msg.ok && isAdminOperator(msg.user_id)) openOpsMenu(msg.user_id);
      else if (msg.ok && isRegisteredLockerUser(msg.user_id)) flashNoAccessThen(goStandby);
      else flashDeniedThen(goStandby);
      return;
    }

    if (mode === "ops-scan" && current === "scan1") {
      if (msg.ok && Number(msg.user_id) === Number(approverUserId)) {
        goStandby();
        return;
      }
      if (msg.ok && isRegisteredLockerUser(msg.user_id)) {
        const slot = findUserSlotById(msg.user_id);
        if (!slot) {
          flashNoAccessThen(function () {
            mode = "ops-scan";
            showScreen("scan1");
            setHint("scan1", "등록된 사용자 지문을 스캔하세요");
          });
          return;
        }
        afterUserMatched(slot, true);
      } else if (msg.ok) {
        flashNoAccessThen(function () {
          mode = "ops-scan";
          showScreen("scan1");
          setHint("scan1", "등록된 사용자 지문을 스캔하세요");
        });
      } else {
        flashDeniedThen(function () {
          mode = "ops-scan";
          showScreen("scan1");
          setHint("scan1", "등록된 사용자 지문을 스캔하세요");
        });
      }
      return;
    }

    if (mode === "door-wait" && current === "scan1") {
      if (msg.ok && isRegisteredLockerUser(msg.user_id)) {
        const slot = findUserSlotById(msg.user_id);
        if (!slot) {
          flashNoAccessThen(startDoorWait);
          return;
        }
        afterUserMatched(slot, false);
      } else if (msg.ok && Number(msg.user_id) === Number(approverUserId)) {
        goStandby();
      } else if (msg.ok) {
        flashNoAccessThen(startDoorWait);
      } else {
        flashDeniedThen(startDoorWait);
      }
    }
  }

  function connectEvents() {
    const es = new EventSource("/api/events");
    es.onmessage = function (e) {
      try {
        onDaemonEvent(JSON.parse(e.data));
      } catch (_) {}
    };
  }

  buildSubSlotGrid();
  buildUserSlotGrid();
  renderLocker();
  keyboard.render(kbLang);
  tick();
  pullOmsTime();
  setInterval(tick, 1000);
  setInterval(pullOmsTime, 15000);
  startLockerSensorPoll();
  fitDesignStage();
  window.addEventListener("resize", fitDesignStage);
  requestAnimationFrame(fitDesignStage);
  setTimeout(fitDesignStage, 120);
  setTimeout(fitDesignStage, 600);
  connectEvents();
  refreshStore();
  updateFooterChrome();
  setInterval(function () {
    if (sensorConnected === false || sensorConnected === null) {
      refreshStore();
    }
  }, 3000);

  settingsBtn.addEventListener("click", function (e) {
    e.stopPropagation();
    settingsBtn.classList.add("is-pressed");
    setTimeout(function () {
      settingsBtn.classList.remove("is-pressed");
    }, 150);
    onSettings();
  });

  if (rifleBtn) {
    rifleBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      openLockerStatus();
    });
  }

  function clearLogoHold() {
    if (logoHoldTimer) {
      clearTimeout(logoHoldTimer);
      logoHoldTimer = null;
    }
    logoHoldPointerId = null;
  }

  function openEngineerPassword() {
    mode = "engineer";
    authArmed = false;
    const field = document.getElementById("engineerPassword");
    if (field) field.value = "";
    showScreen("engineer-password");
    if (field) {
      setTimeout(function () {
        field.focus();
      }, 40);
    }
  }

  function showEngineerDone(message, next) {
    const title = document.getElementById("engineerDoneTitle");
    if (title) {
      title.textContent = "";
      const line = document.createElement("span");
      line.className = "prompt-line";
      line.textContent = message || "완료되었습니다.";
      title.appendChild(line);
    }
    showNotice("engineer-done", next || goStandby, 1800);
  }

  async function submitEngineerPassword() {
    const field = document.getElementById("engineerPassword");
    const password = field ? field.value : "";
    try {
      await postJson("/api/engineer/login", { password: password });
      mode = "engineer";
      showScreen("engineer-menu");
    } catch (err) {
      showEngineerDone("비밀번호가 올바르지 않습니다.", openEngineerPassword);
    }
  }

  async function setEngineerKind(kind) {
    try {
      const data = await postJson("/api/engineer/kind", { kind: kind });
      applyStore(data);
      showEngineerDone(kind === "pistol" ? "권총보관함으로 적용되었습니다." : "소총보관함으로 적용되었습니다.");
    } catch (err) {
      showEngineerDone("적용하지 못했습니다.");
    }
  }

  let engineerResetBusy = false;

  async function runEngineerReset() {
    if (engineerResetBusy) return;
    engineerResetBusy = true;
    clearNoticeTimer();
    const title = document.getElementById("engineerDoneTitle");
    if (title) {
      title.textContent = "";
      const line = document.createElement("span");
      line.className = "prompt-line";
      line.textContent = "보관함 초기화 중입니다.";
      title.appendChild(line);
    }
    showScreen("engineer-done");
    try {
      const data = await postJson("/api/engineer/reset", {});
      applyStore(data);
      showEngineerDone("보관함 초기화가 완료되었습니다.");
    } catch (err) {
      showEngineerDone("초기화하지 못했습니다.", function () {
        mode = "engineer";
        showScreen("engineer-menu");
      });
    } finally {
      engineerResetBusy = false;
    }
  }

  const brandLogo = document.querySelector(".brand-logo");
  if (brandLogo) {
    brandLogo.setAttribute("draggable", "false");
    brandLogo.style.touchAction = "none";
    brandLogo.addEventListener("pointerdown", function (e) {
      if (e.pointerType === "mouse" && e.button != null && e.button !== 0) return;
      if (e.cancelable) e.preventDefault();
      clearLogoHold();
      logoHoldPointerId = e.pointerId;
      try {
        brandLogo.setPointerCapture(e.pointerId);
      } catch (_) {}
      logoHoldTimer = setTimeout(function () {
        logoHoldTimer = null;
        logoHoldPointerId = null;
        openEngineerPassword();
      }, 5000);
    });
    function endLogoHold(e) {
      if (logoHoldPointerId != null && e.pointerId != null && e.pointerId !== logoHoldPointerId) return;
      clearLogoHold();
      try {
        if (e.pointerId != null) brandLogo.releasePointerCapture(e.pointerId);
      } catch (_) {}
    }
    // pointerleave/out cancel long-press on kiosk touch; only end on up/cancel.
    brandLogo.addEventListener("pointerup", endLogoHold);
    brandLogo.addEventListener("pointercancel", endLogoHold);
    brandLogo.addEventListener("contextmenu", function (e) {
      e.preventDefault();
    });
  }

  const engineerPassword = document.getElementById("engineerPassword");
  if (engineerPassword) {
    engineerPassword.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter") {
        ev.preventDefault();
        submitEngineerPassword();
      }
    });
  }

  kbInput.addEventListener("input", function () {
    composer.set(kbInput.value);
  });

  document.addEventListener("click", function (e) {
    const kbAct = e.target.closest("[data-kb]");
    if (kbAct) {
      const act = kbAct.getAttribute("data-kb");
      if (act === "lang") {
        kbLang = kbLang === "ko" ? "en" : "ko";
        keyboard.render(kbLang);
      } else if (act === "space") {
        composer.input(" ");
        syncKbInput();
      } else if (act === "back") {
        composer.backspace();
        syncKbInput();
      } else if (act === "done") {
        commitRename();
      } else if (act === "cancel") {
        closeRename();
      }
      return;
    }

    const renameEl = e.target.closest('[data-action="rename-user"]');
    if (renameEl) {
      e.preventDefault();
      e.stopPropagation();
      flashNameLocked(function () {
        showScreen("user-slot-menu");
      });
      return;
    }

    const btn = e.target.closest("button[data-action], [data-action]");
    if (!btn || btn.disabled || btn.getAttribute("aria-disabled") === "true") return;
    e.preventDefault();
    const action = btn.getAttribute("data-action");
    if (!action) return;

    if (action === "close-locker") {
      goStandby();
      return;
    }
    if (action === "engineer-cancel" || action === "engineer-close") {
      goStandby();
      return;
    }
    if (action === "eng-digit") {
      const field = document.getElementById("engineerPassword");
      const digit = btn.getAttribute("data-digit") || "";
      if (field && digit && field.value.length < 20) {
        field.value += digit;
      }
      return;
    }
    if (action === "eng-back") {
      const field = document.getElementById("engineerPassword");
      if (field && field.value) {
        field.value = field.value.slice(0, -1);
      }
      return;
    }
    if (action === "eng-clear") {
      const field = document.getElementById("engineerPassword");
      if (field) field.value = "";
      return;
    }
    if (action === "engineer-login") {
      submitEngineerPassword();
      return;
    }
    if (action === "engineer-kind-rifle") {
      setEngineerKind("rifle");
      return;
    }
    if (action === "engineer-kind-pistol") {
      setEngineerKind("pistol");
      return;
    }
    if (action === "engineer-reset") {
      runEngineerReset();
      return;
    }

    if (action === "ops-issue") {
      opsKind = "issue";
      mode = "purpose";
      updateFooterChrome();
      showScreen("purpose-menu");
      return;
    }
    if (action === "ops-store") {
      opsKind = "store";
      opsPurpose = null;
      startOpsUserScan();
      return;
    }
    if (action === "ops-cancel") {
      goStandby();
      return;
    }
    if (action === "pick-purpose") {
      opsPurpose = btn.getAttribute("data-purpose") || "";
      startOpsUserScan();
      return;
    }
    if (action === "purpose-cancel") {
      mode = "ops";
      showScreen("ops-menu");
      return;
    }

    if (action === "save") {
      if (adminTarget === "user" && mode === "admin") {
        postJson("/api/user/assign", { slot: selectedUserSlot, user_id: enrollUserId })
          .then(function (data) {
            applyStore(data);
            showNotice("updated", openUserSlotMenu);
          })
          .catch(function (err) {
            alert("저장 실패: " + err.message);
          });
        return;
      }
      if (adminTarget === "sub" && mode === "admin") {
        postJson("/api/sub/assign", { slot: selectedSubSlot, user_id: enrollUserId })
          .then(function (data) {
            applyStore(data);
            showNotice("updated", openSubSlotMenu);
          })
          .catch(function (err) {
            alert("저장 실패: " + err.message);
          });
        return;
      }
      if (mode === "admin" && adminTarget === "master") {
        postJson("/api/master/assign", { slot: selectedSlot, user_id: enrollUserId })
          .then(function (data) {
            applyStore(data);
            showNotice("updated", openMasterMenu);
          })
          .catch(function (err) {
            alert("저장 실패: " + err.message);
          });
        return;
      }
      postJson("/api/enroll/commit", { user_id: enrollUserId })
        .then(function (data) {
          applyStore(data);
          showNotice("updated", goStandby);
        })
        .catch(function (err) {
          alert("저장 실패: " + err.message);
        });
      return;
    }

    if (action === "cancel-confirm") {
      postJson("/api/enroll/discard", { user_id: enrollUserId })
        .catch(function () {})
        .finally(function () {
          if (mode === "admin" && adminTarget === "sub") openSubSlotMenu();
          else if (mode === "admin" && adminTarget === "user") openUserSlotMenu();
          else if (mode === "admin") showScreen("action-menu");
          else goStandby();
        });
      return;
    }

    if (action === "cancel-menu") {
      goStandby();
      return;
    }
    if (action === "back-role") {
      showScreen("role-menu");
      return;
    }
    if (action === "role-master") {
      adminTarget = "master";
      openMasterMenu();
      return;
    }
    if (action === "role-sub") {
      adminTarget = "sub";
      showScreen("sub-action-menu");
      return;
    }
    if (action === "role-user") {
      adminTarget = "user";
      showScreen("user-action-menu");
      return;
    }
    if (action === "pick-master") {
      selectedSlot = Number(btn.getAttribute("data-slot") || "1");
      const hint = document.getElementById("actionSlotHint");
      if (hint) {
        hint.textContent =
          masterDisplayName(selectedSlot) +
          (isSlotRegistered(selectedSlot) ? " (등록됨)" : " (미등록)");
      }
      showScreen("action-menu");
      return;
    }
    if (action === "back-master") {
      openMasterMenu();
      return;
    }
    if (action === "back-sub-action") {
      mode = "admin";
      adminTarget = "sub";
      showScreen("sub-action-menu");
      return;
    }
    if (action === "sub-page-prev" || action === "sub-page-next") {
      subSlotPage = action === "sub-page-next" ? 1 : 0;
      renderSubSlotPage();
      return;
    }
    if (action === "back-user-action") {
      mode = "admin";
      adminTarget = "user";
      showScreen("user-action-menu");
      return;
    }
    if (action === "do-enroll") {
      adminTarget = "master";
      if (isSlotRegistered(selectedSlot)) {
        flashNameLocked(function () {
          showScreen("action-menu");
        });
        return;
      }
      startEnrollScan(selectedSlot, "action-menu");
      return;
    }
    if (action === "do-delete") {
      adminTarget = "master";
      showScreen("delete-confirm");
      return;
    }
    if (action === "sub-enroll") {
      adminTarget = "sub";
      slotIntent = "enroll";
      openSubSlotMenu();
      return;
    }
    if (action === "sub-delete") {
      adminTarget = "sub";
      slotIntent = "delete";
      openSubSlotMenu();
      return;
    }
    if (action === "user-enroll") {
      adminTarget = "user";
      slotIntent = "enroll";
      openUserSlotMenu();
      return;
    }
    if (action === "user-delete") {
      adminTarget = "user";
      slotIntent = "delete";
      openUserSlotMenu();
      return;
    }
    if (action === "pick-sub-slot") {
      selectedSubSlot = Number(btn.getAttribute("data-slot") || "1");
      if (slotIntent === "delete") {
        if (!isSubRegistered(selectedSubSlot)) {
          flashDeniedThen(openSubSlotMenu);
          return;
        }
        showScreen("delete-confirm");
        return;
      }
      if (isSubRegistered(selectedSubSlot)) {
        flashNameLocked(openSubSlotMenu);
        return;
      }
      startEnrollScan(subUserIdForSlot(selectedSubSlot), "sub-slot-menu");
      return;
    }
    if (action === "pick-user-slot") {
      selectedUserSlot = Number(btn.getAttribute("data-slot") || "1");
      if (slotIntent === "delete") {
        if (!isUserRegistered(selectedUserSlot)) {
          flashDeniedThen(openUserSlotMenu);
          return;
        }
        showScreen("delete-confirm");
        return;
      }
      if (isUserRegistered(selectedUserSlot)) {
        flashNameLocked(openUserSlotMenu);
        return;
      }
      startEnrollScan(lockerUserIdForSlot(selectedUserSlot), "user-slot-menu");
      return;
    }
    if (action === "delete-no") {
      if (adminTarget === "sub") openSubSlotMenu();
      else if (adminTarget === "user") openUserSlotMenu();
      else showScreen("action-menu");
      return;
    }
    if (action === "delete-yes") {
      if (adminTarget === "user") {
        postJson("/api/user/delete", {
          slot: selectedUserSlot,
          user_id: lockerUserIdForSlot(selectedUserSlot),
        })
          .then(function (data) {
            applyStore(data);
            showNotice("deleted", openUserSlotMenu);
          })
          .catch(function (err) {
            alert("삭제 실패: " + err.message);
            openUserSlotMenu();
          });
        return;
      }
      if (adminTarget === "sub") {
        postJson("/api/sub/delete", {
          slot: selectedSubSlot,
          user_id: subUserIdForSlot(selectedSubSlot),
        })
          .then(function (data) {
            applyStore(data);
            showNotice("deleted", openSubSlotMenu);
          })
          .catch(function (err) {
            alert("삭제 실패: " + err.message);
            openSubSlotMenu();
          });
        return;
      }
      postJson("/api/master/delete", { slot: selectedSlot, user_id: selectedSlot })
        .then(function (data) {
          applyStore(data);
          showNotice("deleted", openMasterMenu);
        })
        .catch(function (err) {
          alert("삭제 실패: " + err.message);
          showScreen("action-menu");
        });
    }
  });
})();
