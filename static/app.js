const state = {
  index: null,
  save: null,
  selectedId: null,
  catalog: [],
  backups: [],
  busy: false,
};

const els = {};

document.addEventListener("DOMContentLoaded", () => {
  [
    "connectionBadge", "refreshButton", "saveCount", "saveList", "syncLinked",
    "saveDirectory", "emptyState", "editor", "saveKind", "saveTitle", "savePath",
    "dirtyBadge", "metrics", "cleanupButton", "goldAccessoryButton", "inventorySearch",
    "inventoryRare", "inventoryBody", "inventoryFoot", "catalogSearch", "catalogRare",
    "catalogType", "catalogGrid", "catalogFoot", "backupList", "reloadBackups",
    "confirmDialog", "confirmTitle", "confirmMessage", "confirmCancel", "confirmAccept",
    "toastRegion", "overviewTab", "handTab", "inventoryTab", "catalogTab", "backupsTab",
    "parameterSearch", "parameterBody", "parameterFoot", "collectionGrid",
    "handSearch", "handBag", "handGroups", "handFoot",
  ].forEach((id) => { els[id] = document.getElementById(id); });

  els.refreshButton.addEventListener("click", () => loadIndex(true));
  els.cleanupButton.addEventListener("click", cleanupSlander);
  els.goldAccessoryButton.addEventListener("click", () => addCard(2000818, 1));
  els.inventorySearch.addEventListener("input", renderInventory);
  els.inventoryRare.addEventListener("change", renderInventory);
  els.parameterSearch.addEventListener("input", renderParameters);
  els.handSearch.addEventListener("input", renderHandPreview);
  els.handBag.addEventListener("change", renderHandPreview);
  els.catalogSearch.addEventListener("input", debounce(loadCatalog, 180));
  els.catalogRare.addEventListener("change", loadCatalog);
  els.catalogType.addEventListener("change", loadCatalog);
  els.reloadBackups.addEventListener("click", loadBackups);
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => selectTab(tab.dataset.tab));
  });

  loadIndex();
});

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  let payload;
  try {
    payload = await response.json();
  } catch {
    throw new Error(`服务返回了无法识别的响应（HTTP ${response.status}）`);
  }
  if (!response.ok || payload.ok === false) {
    throw new Error(payload.error || `请求失败（HTTP ${response.status}）`);
  }
  return payload;
}

async function loadIndex(preserveSelection = false) {
  setConnection("loading", "正在连接");
  try {
    const payload = await api("/api/state");
    state.index = payload;
    els.saveDirectory.textContent = payload.save_base;
    els.saveCount.textContent = payload.saves.length;
    renderSaveList();
    setConnection("connected", `本地服务 v${payload.version}`);
    if (payload.warning) toast(payload.warning, true);
    const previous = preserveSelection && payload.saves.some((save) => save.id === state.selectedId)
      ? state.selectedId
      : null;
    const preferred = previous
      || payload.saves.find((save) => save.kind === "auto")?.id
      || payload.saves[0]?.id;
    if (preferred) await selectSave(preferred);
  } catch (error) {
    setConnection("error", "连接失败");
    toast(error.message, true);
  }
}

function renderSaveList() {
  const saves = state.index?.saves || [];
  if (!saves.length) {
    els.saveList.innerHTML = '<p class="table-foot">未找到可读取的存档。</p>';
    return;
  }
  els.saveList.innerHTML = saves.map((save) => {
    const summary = save.summary || {};
    const flags = [
      `<span class="mini-flag">回合 ${escapeHtml(summary.round ?? "—")}</span>`,
      summary.slander ? `<span class="mini-flag alert">谗言 ${summary.slander}</span>` : "",
      summary.teasing ? `<span class="mini-flag alert">戏弄 ${summary.teasing}</span>` : "",
    ].join("");
    return `
      <button class="save-item ${save.id === state.selectedId ? "active" : ""}"
        type="button" data-save-id="${escapeAttr(save.id)}">
        <strong>${escapeHtml(save.label)}</strong>
        <small>${escapeHtml(summary.name || "无法读取")} · ${formatDate(summary.save_time)}</small>
        <span class="save-flags">${flags}</span>
      </button>`;
  }).join("");
  els.saveList.querySelectorAll("[data-save-id]").forEach((button) => {
    button.addEventListener("click", () => selectSave(button.dataset.saveId));
  });
}

async function selectSave(saveId) {
  if (!saveId || state.busy) return;
  state.selectedId = saveId;
  renderSaveList();
  try {
    const payload = await api(`/api/save?id=${encodeURIComponent(saveId)}`);
    state.save = payload.save;
    els.emptyState.classList.add("hidden");
    els.editor.classList.remove("hidden");
    renderSave();
    if (document.querySelector(".tab.active")?.dataset.tab === "backups") {
      loadBackups();
    }
  } catch (error) {
    toast(error.message, true);
  }
}

function renderSave() {
  if (!state.save) return;
  const descriptor = state.index.saves.find((save) => save.id === state.save.id);
  const summary = state.save.summary;
  els.saveKind.textContent = (descriptor?.kind || "archive").toUpperCase();
  els.saveTitle.textContent = descriptor?.label || summary.name;
  els.savePath.textContent = state.save.path;
  els.dirtyBadge.textContent = "已读取";
  els.metrics.innerHTML = [
    metric("当前回合", summary.round ?? "—"),
    metric("卡牌实例", summary.card_instances ?? 0),
    metric("在袋卡牌", summary.hand_instances ?? 0),
    metric("仪式实例", summary.rite_instances ?? 0),
    metric("谗言", summary.slander ?? 0, summary.slander > 0),
    metric("待处理戏弄", summary.teasing ?? 0, summary.teasing > 0),
  ].join("");
  renderParameters();
  renderHandPreview();
  renderInventory();
}

function metric(label, value, alert = false) {
  return `<div class="metric ${alert ? "alert" : ""}"><small>${label}</small><strong>${escapeHtml(value)}</strong></div>`;
}

function renderParameters() {
  if (!state.save) return;
  const query = els.parameterSearch.value.trim().toLowerCase();
  const parameters = state.save.parameters || { values: [], collections: [] };
  const collections = parameters.collections.filter((item) => {
    const haystack = `${item.label} ${item.key} ${item.kind} ${item.count}`.toLowerCase();
    return !query || haystack.includes(query);
  });
  els.collectionGrid.innerHTML = collections.length ? collections.map((item) => `
    <article class="collection-stat">
      <span>${escapeHtml(item.label)}</span>
      <strong>${escapeHtml(item.count)}</strong>
      <small>${escapeHtml(item.key)} · ${escapeHtml(item.kind)}</small>
    </article>`).join("") : "";

  const values = parameters.values.filter((item) => {
    const haystack = `${item.group} ${item.label} ${item.key} ${formatParameterValue(item)}`.toLowerCase();
    return !query || haystack.includes(query);
  });
  els.parameterBody.innerHTML = values.length ? values.map((item) => `
    <tr>
      <td><span class="parameter-group">${escapeHtml(item.group)}</span></td>
      <td><strong class="parameter-label">${escapeHtml(item.label)}</strong></td>
      <td><code class="parameter-key">${escapeHtml(item.key)}</code></td>
      <td><span class="type-pill">${escapeHtml(parameterTypeName(item.type))}</span></td>
      <td><code class="parameter-value">${escapeHtml(formatParameterValue(item))}</code></td>
    </tr>`).join("") : '<tr><td colspan="5">没有匹配的参数。</td></tr>';
  els.parameterFoot.textContent = `匹配 ${values.length} 个参数与 ${collections.length} 个集合；全部内容均为只读。`;
}

function formatParameterValue(item) {
  if (item.type === "boolean") return item.value ? "是 / true" : "否 / false";
  if (item.type === "null") return "空 / null";
  if (item.value === "") return "（空字符串）";
  return String(item.value);
}

function parameterTypeName(type) {
  return ({ number: "数值", boolean: "布尔", string: "文本", null: "空值" })[type] || type;
}

function renderHandPreview() {
  if (!state.save) return;
  const query = els.handSearch.value.trim().toLowerCase();
  const bagFilter = els.handBag.value;
  const cards = state.save.cards
    .filter((card) => card.in_inventory)
    .filter((card) => bagFilter === "" || String(card.bag) === bagFilter)
    .filter((card) => {
      const haystack = `${card.name} ${card.id} ${card.title} ${(card.tags || []).join(" ")}`.toLowerCase();
      return !query || haystack.includes(query);
    })
    .sort((a, b) => Number(a.bag) - Number(b.bag)
      || Number(a.bagpos) - Number(b.bagpos)
      || Number(a.uid) - Number(b.uid));

  const groups = new Map();
  cards.forEach((card) => {
    const key = String(card.bag);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(card);
  });
  els.handGroups.innerHTML = cards.length ? [...groups.entries()].map(([bag, bagCards]) => `
    <section class="hand-bag-section">
      <div class="hand-bag-heading">
        <div><span class="eyebrow">BAG ${escapeHtml(bag)}</span><h3>${bagName(bag)}</h3></div>
        <span class="count-pill">${bagCards.length}</span>
      </div>
      <div class="hand-card-grid">
        ${bagCards.map(renderPreviewCard).join("")}
      </div>
    </section>`).join("") : '<div class="empty-preview">没有匹配的手牌或背包卡牌。</div>';
  els.handFoot.textContent = `当前显示 ${cards.length} 个已分配袋位的卡牌实例。`;
}

function renderPreviewCard(card) {
  return `
    <article class="preview-card rarity-${card.rare}">
      <div class="preview-card-top">
        <span class="preview-glyph">${escapeHtml(card.name.slice(0, 1) || "?")}</span>
        <span class="rare-pill rare-${card.rare}">${rareName(card.rare)}</span>
      </div>
      <div class="preview-card-copy">
        <small>${escapeHtml(card.title || typeName(card.type))}</small>
        <h4>${escapeHtml(card.name)}</h4>
        <p>${escapeHtml(card.text || "暂无卡牌描述")}</p>
      </div>
      <div class="tag-row">
        ${(card.tags || []).slice(0, 3).map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("")}
      </div>
      <footer>
        <span>UID ${escapeHtml(card.uid)}</span>
        <strong>位 ${escapeHtml(card.bagpos)}${Number(card.count) > 1 ? ` · ×${escapeHtml(card.count)}` : ""}</strong>
      </footer>
    </article>`;
}

function bagName(bag) {
  return String(bag) === "0" ? "当前手牌 · 袋 0" : `背包分组 · 袋 ${escapeHtml(bag)}`;
}

function renderInventory() {
  if (!state.save) return;
  const query = els.inventorySearch.value.trim().toLowerCase();
  const rare = els.inventoryRare.value;
  const matches = state.save.cards.filter((card) => {
    const haystack = `${card.name} ${card.id} ${card.title} ${(card.tags || []).join(" ")}`.toLowerCase();
    return (!query || haystack.includes(query)) && (!rare || String(card.rare) === rare);
  });
  const visible = matches.slice(0, 250);
  els.inventoryBody.innerHTML = visible.length ? visible.map((card) => `
    <tr>
      <td>
        <div class="card-name">
          <span class="card-glyph">${escapeHtml(card.name.slice(0, 1) || "?")}</span>
          <span><strong>${escapeHtml(card.name)}</strong><small>ID ${card.id} · UID ${card.uid}</small></span>
        </div>
      </td>
      <td><span class="rare-pill rare-${card.rare}">${rareName(card.rare)}</span></td>
      <td>${card.in_inventory
        ? `<span class="position-pill">袋 ${card.bag} · 位 ${card.bagpos}</span>`
        : '<span class="position-pill inactive">未进入手牌</span>'}</td>
      <td>
        <div class="count-editor">
          <input class="count-input" type="number" min="1" max="999" value="${card.count}"
            data-count-uid="${card.uid}" aria-label="${escapeAttr(card.name)}的数量" />
          <button class="icon-button" type="button" data-save-count="${card.uid}">保存</button>
        </div>
      </td>
      <td class="align-right">
        ${card.in_inventory ? "" : `<button class="icon-button" type="button" data-place-uid="${card.uid}"
          data-place-name="${escapeAttr(card.name)}">放入手牌</button>`}
        <button class="icon-button danger" type="button" data-remove-uid="${card.uid}"
          data-remove-name="${escapeAttr(card.name)}">移除</button>
      </td>
    </tr>`).join("") : '<tr><td colspan="5">没有匹配的卡牌。</td></tr>';
  els.inventoryFoot.textContent = `匹配 ${matches.length} 个实例${matches.length > 250 ? "，当前显示前 250 个" : ""}`;
  els.inventoryBody.querySelectorAll("[data-save-count]").forEach((button) => {
    button.addEventListener("click", () => {
      const input = els.inventoryBody.querySelector(`[data-count-uid="${button.dataset.saveCount}"]`);
      setCardCount(Number(button.dataset.saveCount), Number(input.value));
    });
  });
  els.inventoryBody.querySelectorAll("[data-remove-uid]").forEach((button) => {
    button.addEventListener("click", () => removeCard(Number(button.dataset.removeUid), button.dataset.removeName));
  });
  els.inventoryBody.querySelectorAll("[data-place-uid]").forEach((button) => {
    button.addEventListener("click", () => placeCard(Number(button.dataset.placeUid), button.dataset.placeName));
  });
}

async function loadCatalog() {
  if (!state.selectedId) return;
  const params = new URLSearchParams({
    q: els.catalogSearch.value.trim(),
    rare: els.catalogRare.value,
    type: els.catalogType.value,
    limit: "300",
  });
  try {
    const payload = await api(`/api/catalog?${params}`);
    state.catalog = payload.cards;
    renderCatalog();
  } catch (error) {
    toast(error.message, true);
  }
}

function renderCatalog() {
  els.catalogGrid.innerHTML = state.catalog.length ? state.catalog.map((card) => `
    <article class="catalog-card">
      <div class="catalog-card-head">
        <div>
          <h3>${escapeHtml(card.name)}</h3>
          <span class="catalog-id">ID ${card.id}</span>
        </div>
        <span class="rare-pill rare-${card.rare}">${rareName(card.rare)}</span>
      </div>
      <div class="tag-row">
        <span class="type-pill">${typeName(card.type)}</span>
        ${(card.tags || []).slice(0, 4).map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("")}
      </div>
      <p class="catalog-text">${escapeHtml(card.text || "暂无描述")}</p>
      <div class="catalog-footer">
        <input class="catalog-count" type="number" min="1" max="999" value="1" data-catalog-count="${card.id}" />
        <button class="button button-gold button-small" type="button" data-add-card="${card.id}">加入存档</button>
      </div>
    </article>`).join("") : '<p class="table-foot">没有匹配的卡牌。</p>';
  els.catalogFoot.textContent = `显示 ${state.catalog.length} 张卡牌，最多显示 300 张。`;
  els.catalogGrid.querySelectorAll("[data-add-card]").forEach((button) => {
    button.addEventListener("click", () => {
      const input = els.catalogGrid.querySelector(`[data-catalog-count="${button.dataset.addCard}"]`);
      addCard(Number(button.dataset.addCard), Number(input.value));
    });
  });
}

async function cleanupSlander() {
  const summary = state.save?.summary;
  if (!summary) return;
  const message = `将从所选范围中移除谗言卡，并删除待处理的“苏丹的戏弄”仪式。当前存档检测到谗言 ${summary.slander}、戏弄 ${summary.teasing}。修改前会自动备份。`;
  const accepted = await confirmChange("清除谗言与戏弄", message, "确认清理");
  if (!accepted) return;
  await performAction({ action: "cleanup_slander" }, "清理完成");
}

async function addCard(cardId, count) {
  if (!Number.isInteger(count) || count < 1 || count > 999) {
    toast("数量必须是 1 到 999 之间的整数。", true);
    return;
  }
  await performAction({ action: "add_card", card_id: cardId, count }, `已添加卡牌 ${cardId} ×${count}`);
}

async function setCardCount(uid, count) {
  if (!Number.isInteger(count) || count < 1 || count > 999) {
    toast("数量必须是 1 到 999 之间的整数。", true);
    return;
  }
  await performAction({ action: "set_card_count", uid, count }, "数量已更新", false);
}

async function removeCard(uid, name) {
  const accepted = await confirmChange("移除卡牌", `确认移除“${name}”（UID ${uid}）？修改前会自动备份。`, "确认移除");
  if (!accepted) return;
  await performAction({ action: "remove_card", uid }, `已移除 ${name}`, false);
}

async function placeCard(uid, name) {
  await performAction({ action: "place_card", uid }, `已将 ${name} 放入手牌`);
}

async function performAction(action, successMessage, syncOverride = null) {
  if (!state.selectedId || state.busy) return;
  setBusy(true);
  try {
    const payload = await api("/api/action", {
      method: "POST",
      body: JSON.stringify({
        ...action,
        save_id: state.selectedId,
        sync_linked: syncOverride === null ? els.syncLinked.checked : syncOverride,
      }),
    });
    state.save = payload.save;
    renderSave();
    await refreshIndexQuietly();
    toast(`${successMessage}；备份 ${payload.backup_id}`);
    if (document.querySelector(".tab.active")?.dataset.tab === "backups") loadBackups();
  } catch (error) {
    toast(error.message, true);
  } finally {
    setBusy(false);
  }
}

async function refreshIndexQuietly() {
  const payload = await api("/api/state");
  state.index = payload;
  renderSaveList();
}

async function loadBackups() {
  if (!state.selectedId) return;
  els.backupList.innerHTML = '<div class="skeleton skeleton-card"></div>';
  try {
    const payload = await api(`/api/backups?id=${encodeURIComponent(state.selectedId)}`);
    state.backups = payload.backups;
    renderBackups();
  } catch (error) {
    els.backupList.innerHTML = "";
    toast(error.message, true);
  }
}

function renderBackups() {
  if (!state.backups.length) {
    els.backupList.innerHTML = '<p class="table-foot">这份存档还没有由修改器生成的备份。</p>';
    return;
  }
  els.backupList.innerHTML = state.backups.map((backup) => `
    <article class="backup-item">
      <div>
        <strong>${escapeHtml(backup.id)}</strong>
        <small>回合 ${backup.summary.round ?? "—"} · 谗言 ${backup.summary.slander ?? "—"} · ${formatDate(backup.summary.save_time)}</small>
      </div>
      <button class="button button-ghost button-small" type="button" data-restore="${backup.id}">恢复</button>
    </article>`).join("");
  els.backupList.querySelectorAll("[data-restore]").forEach((button) => {
    button.addEventListener("click", () => restoreBackup(button.dataset.restore));
  });
}

async function restoreBackup(backupId) {
  const accepted = await confirmChange("恢复备份", `将用备份 ${backupId} 覆盖当前选中的存档。覆盖前仍会保存当前版本。`, "确认恢复");
  if (!accepted) return;
  setBusy(true);
  try {
    const payload = await api("/api/action", {
      method: "POST",
      body: JSON.stringify({ action: "restore_backup", save_id: state.selectedId, backup_id: backupId }),
    });
    state.save = payload.save;
    renderSave();
    await refreshIndexQuietly();
    await loadBackups();
    toast(`已恢复备份；恢复前版本保存在 ${payload.backup_id}`);
  } catch (error) {
    toast(error.message, true);
  } finally {
    setBusy(false);
  }
}

function selectTab(name) {
  document.querySelectorAll(".tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.tab === name));
  ["overview", "hand", "inventory", "catalog", "backups"].forEach((tabName) => {
    els[`${tabName}Tab`].classList.toggle("hidden", tabName !== name);
  });
  if (name === "catalog" && !state.catalog.length) loadCatalog();
  if (name === "backups") loadBackups();
}

function setBusy(busy) {
  state.busy = busy;
  document.querySelectorAll("button").forEach((button) => { button.disabled = busy; });
  els.dirtyBadge.textContent = busy ? "正在写入…" : "已读取";
}

function setConnection(mode, message) {
  els.connectionBadge.classList.remove("connected", "error");
  if (mode === "connected") els.connectionBadge.classList.add("connected");
  if (mode === "error") els.connectionBadge.classList.add("error");
  els.connectionBadge.querySelector("span").textContent = message;
}

function toast(message, isError = false) {
  const item = document.createElement("div");
  item.className = `toast ${isError ? "error" : ""}`;
  item.textContent = message;
  els.toastRegion.appendChild(item);
  setTimeout(() => item.remove(), 6000);
}

function confirmChange(title, message, acceptLabel = "确认") {
  return new Promise((resolve) => {
    els.confirmTitle.textContent = title;
    els.confirmMessage.textContent = message;
    els.confirmAccept.textContent = acceptLabel;
    els.confirmDialog.classList.remove("hidden");
    const finish = (value) => {
      els.confirmDialog.classList.add("hidden");
      els.confirmAccept.onclick = null;
      els.confirmCancel.onclick = null;
      resolve(value);
    };
    els.confirmAccept.onclick = () => finish(true);
    els.confirmCancel.onclick = () => finish(false);
  });
}

function rareName(rare) {
  return ({ 4: "黄金", 3: "白银", 2: "青铜", 1: "普通", 0: "无品级" })[rare] || `品级 ${rare}`;
}

function typeName(type) {
  return ({ item: "物品", char: "角色", sudan: "苏丹卡" })[type] || type || "其他";
}

function formatDate(value) {
  if (!value) return "时间未知";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(date);
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[char]);
}

function escapeAttr(value) {
  return escapeHtml(value);
}

function debounce(fn, delay) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), delay);
  };
}
