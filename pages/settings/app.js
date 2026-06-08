import { createApi } from "./api.js";

const bridge = window.AstrBotPluginPage;
const root = document.documentElement;
const themeMediaQuery =
  typeof window.matchMedia === "function"
    ? window.matchMedia("(prefers-color-scheme: dark)")
    : null;
const THEME_STORAGE_KEY = "audit-page-theme-mode";
const DEFAULT_GROUP_ID = "__default__";
const DEFAULT_GROUP_ICON = "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='96' height='96' viewBox='0 0 96 96'><rect width='96' height='96' rx='24' fill='%236EB1D8'/><svg x='24' y='24' width='48' height='48' viewBox='0 -960 960 960' fill='%231f1f1f'><path d='M440-120v-240h80v80h320v80H520v80h-80Zm-320-80v-80h240v80H120Zm160-160v-80H120v-80h160v-80h80v240h-80Zm160-80v-80h400v80H440Zm160-160v-240h80v80h160v80H680v80h-80Zm-480-80v-80h400v80H120Z'/></svg></svg>";

let api = null;
let bootstrapData = null;
let currentGroup = null;
let allGroups = [];
let detachContextHandler = null;
let detachSystemThemeHandler = null;
let themePreference = loadThemePreference();

const els = {
  groupForm: document.getElementById("groupForm"),
  groupList: document.getElementById("groupList"),
  groupSearchInput: document.getElementById("groupSearchInput"),
  groupDetailHeader: document.getElementById("groupDetailHeader"),
  groupInfoAvatar: document.getElementById("groupInfoAvatar"),
  groupInfoName: document.getElementById("groupInfoName"),
  groupInfoSub: document.getElementById("groupInfoSub"),
  groupListCount: document.getElementById("groupListCount"),
  toastLayer: document.getElementById("toastLayer"),
  toggleThemeBtn: document.getElementById("toggleThemeBtn"),
  addGroupBtn: document.getElementById("addGroupBtn"),
  saveGroupBtn: document.getElementById("saveGroupBtn"),
  deleteGroupBtn: document.getElementById("deleteGroupBtn"),
  enabledGroupsInfo: document.getElementById("enabledGroupsInfo"),
  addGroupModal: document.getElementById("addGroupModal"),
  modalCloseBtn: document.getElementById("modalCloseBtn"),
  modalSearchInput: document.getElementById("modalSearchInput"),
  modalGroupList: document.getElementById("modalGroupList"),
  modalConfirmBtn: document.getElementById("modalConfirmBtn"),
  deleteConfirmModal: document.getElementById("deleteConfirmModal"),
  deleteConfirmCloseBtn: document.getElementById("deleteConfirmCloseBtn"),
  deleteConfirmBody: document.getElementById("deleteConfirmBody"),
  deleteConfirmCancelBtn: document.getElementById("deleteConfirmCancelBtn"),
  deleteConfirmOkBtn: document.getElementById("deleteConfirmOkBtn"),
};

/* ── theme ── */
function loadThemePreference() {
  try {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
    if (stored === "light" || stored === "dark" || stored === "auto") {
      return stored;
    }
  } catch {}
  return "auto";
}

function saveThemePreference() {
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, themePreference);
  } catch {}
}

function getThemeButtonLabel() {
  if (themePreference === "dark") return "主题: 深色";
  if (themePreference === "light") return "主题: 浅色";
  return "主题: 自动";
}

function updateThemeButton() {
  if (els.toggleThemeBtn) {
    els.toggleThemeBtn.textContent = getThemeButtonLabel();
  }
}

function getBridgeThemeMode(context) {
  if (context?.theme === "dark" || context?.theme === "light") {
    return context.theme;
  }
  return null;
}

function getSystemThemeMode() {
  return themeMediaQuery?.matches ? "dark" : "light";
}

function resolveThemeMode(context) {
  if (themePreference === "dark" || themePreference === "light") {
    return themePreference;
  }
  const bridgeThemeMode = getBridgeThemeMode(context);
  if (bridgeThemeMode) {
    return bridgeThemeMode;
  }
  return getSystemThemeMode();
}

function applyThemeMode(themeMode) {
  root.dataset.theme = themeMode;
  root.style.colorScheme = themeMode;
}

function syncThemeFromContext(context) {
  applyThemeMode(resolveThemeMode(context));
  updateThemeButton();
}

function cycleThemePreference() {
  if (themePreference === "auto") {
    themePreference = "dark";
  } else if (themePreference === "dark") {
    themePreference = "light";
  } else {
    themePreference = "auto";
  }
  saveThemePreference();
  syncThemeFromContext(bridge?.getContext?.());
}

function bindSystemTheme() {
  if (!themeMediaQuery) return;
  const handleThemeChange = () => {
    if (themePreference === "auto") {
      applyThemeMode(resolveThemeMode(bridge?.getContext?.()));
    }
  };
  if (typeof themeMediaQuery.addEventListener === "function") {
    themeMediaQuery.addEventListener("change", handleThemeChange);
    detachSystemThemeHandler = () => {
      themeMediaQuery.removeEventListener("change", handleThemeChange);
    };
    return;
  }
  if (typeof themeMediaQuery.addListener === "function") {
    themeMediaQuery.addListener(handleThemeChange);
    detachSystemThemeHandler = () => {
      themeMediaQuery.removeListener(handleThemeChange);
    };
  }
}

/* ── toast ── */
function showToast(message, type = "success") {
  const node = document.createElement("div");
  node.className = `toast ${type}`;
  node.textContent = message;
  els.toastLayer.appendChild(node);
  setTimeout(() => {
    node.classList.add("fade-out");
    node.addEventListener("animationend", () => node.remove());
  }, 2600);
}

/* ── form renderer ── */
function setByPath(target, path, value) {
  const parts = path.split(".");
  let cursor = target;
  parts.forEach((part, index) => {
    if (index === parts.length - 1) {
      cursor[part] = value;
      return;
    }
    if (!cursor[part] || typeof cursor[part] !== "object") {
      cursor[part] = {};
    }
    cursor = cursor[part];
  });
}

function buildField(key, schema, value, prefix) {
  const path = prefix ? `${prefix}.${key}` : key;
  const type = schema.type || "string";

  if (type === "object") {
    return buildObjectField(path, key, schema, value || {});
  }

  const field = document.createElement("label");
  field.className = "field";

  const copy = document.createElement("div");
  copy.className = "field-copy";

  const label = document.createElement("div");
  label.className = "field-label";
  label.textContent = schema.description || key;
  copy.appendChild(label);

  if (schema.hint) {
    const hint = document.createElement("div");
    hint.className = "field-hint";
    hint.textContent = schema.hint;
    copy.appendChild(hint);
  }

  field.appendChild(copy);

  const control = document.createElement("div");
  control.className = "field-control";

  let input;
  if (type === "bool") {
    field.classList.add("checkbox-field");
    const shell = document.createElement("span");
    shell.className = "switch";
    input = document.createElement("input");
    input.type = "checkbox";
    input.checked = Boolean(value);
    const slider = document.createElement("span");
    slider.className = "slider";
    shell.appendChild(input);
    shell.appendChild(slider);
    control.appendChild(shell);
  } else if (type === "int") {
    input = document.createElement("input");
    input.type = "number";
    input.value = String(value ?? schema.default ?? 0);
    if (schema.slider) {
      if (schema.slider.min !== undefined) input.min = schema.slider.min;
      if (schema.slider.max !== undefined) input.max = schema.slider.max;
    }
    control.appendChild(input);
  } else if (type === "list") {
    input = document.createElement("textarea");
    input.value = Array.isArray(value) ? value.join("\n") : "";
    input.placeholder = "每行一个条目";
    control.appendChild(input);
  } else {
    input = document.createElement("input");
    input.type = "text";
    input.value = String(value ?? schema.default ?? "");
    control.appendChild(input);
  }

  input.dataset.path = path;
  input.dataset.type = type;
  field.appendChild(control);
  return field;
}

function buildObjectField(path, key, schema, values) {
  const wrapper = document.createElement("section");
  wrapper.className = "form-object";

  const header = document.createElement("div");
  header.className = "section-head";

  const title = document.createElement("div");
  title.className = "section-title";
  title.textContent = schema.description || key;
  header.appendChild(title);

  if (schema.hint) {
    const hint = document.createElement("div");
    hint.className = "section-hint";
    hint.textContent = schema.hint;
    header.appendChild(hint);
  }

  wrapper.appendChild(header);

  const grid = document.createElement("div");
  grid.className = "field-grid";
  Object.entries(schema.items || {}).forEach(([childKey, childSchema]) => {
    const val = values?.[childKey] ?? childSchema.default;
    grid.appendChild(buildField(childKey, childSchema, val, path));
  });
  wrapper.appendChild(grid);
  return wrapper;
}

function renderSchemaFields(rootEl, schema, values) {
  rootEl.innerHTML = "";
  const grid = document.createElement("div");
  grid.className = "field-grid";
  Object.entries(schema).forEach(([key, fieldSchema]) => {
    const val = values?.[key] ?? fieldSchema.default;
    grid.appendChild(buildField(key, fieldSchema, val, ""));
  });
  rootEl.appendChild(grid);
}

function collectFormData(rootEl) {
  const payload = {};
  rootEl.querySelectorAll("[data-path]").forEach((node) => {
    const { path, type } = node.dataset;
    let value;
    if (type === "bool") {
      value = node.checked;
    } else if (type === "int") {
      value = Number(node.value || 0);
    } else if (type === "list") {
      value = node.value
        .split(/\n+/)
        .map((item) => item.trim())
        .filter(Boolean);
    } else {
      value = node.value;
    }
    setByPath(payload, path, value);
  });
  return payload;
}

function splitGlobalAndDisposal(payload) {
  const globalConfig = {};
  const disposalConfig = {};
  Object.entries(payload).forEach(([key, value]) => {
    if (key === "__global__" && typeof value === "object") {
      // Flatten __global__.xxx into globalConfig
      Object.assign(globalConfig, value);
    } else if (key.startsWith("__global__.")) {
      // Should not happen with setByPath, but handle just in case
      globalConfig[key.slice("__global__.".length)] = value;
    } else {
      disposalConfig[key] = value;
    }
  });
  return { globalConfig, disposalConfig };
}

/* ── group list ── */
function normalizeGroups(groups) {
  return Array.isArray(groups) ? groups : [];
}

function filterGroups() {
  const keyword = String(els.groupSearchInput.value || "")
    .trim()
    .toLowerCase();
  if (!keyword) return allGroups;
  return allGroups.filter((group) => {
    const groupId = String(group.group_id || "").toLowerCase();
    const groupName = String(group.group_name || "").toLowerCase();
    return groupId.includes(keyword) || groupName.includes(keyword);
  });
}

function renderGroupCards(forceRebuild = false) {
  const groups = filterGroups();
  els.groupListCount.textContent = `${groups.length} 个群配置`;

  if (!groups.length) {
    els.groupList.classList.add("empty-state");
    els.groupList.textContent = "没有匹配的群配置。";
    return;
  }

  els.groupList.classList.remove("empty-state");

  // Build a map of existing cards by group_id for reuse
  const existingCards = new Map();
  if (!forceRebuild) {
    els.groupList.querySelectorAll(".group-card").forEach((card) => {
      existingCards.set(card.dataset.groupId, card);
    });
  }

  const enabledGroups = Array.isArray(bootstrapData?.enabled_groups)
    ? bootstrapData.enabled_groups
    : [];

  const fragment = document.createDocumentFragment();

  groups.forEach((group) => {
    const existing = existingCards.get(group.group_id);
    if (existing && !forceRebuild) {
      // Reuse existing card — only update dynamic parts
      existing.classList.toggle("is-active", group.group_id === currentGroup?.group_id);
      updateCardBadges(existing, group, enabledGroups);
      existingCards.delete(group.group_id);
      fragment.appendChild(existing);
      return;
    }

    // Build new card
    const card = document.createElement("article");
    card.className = "group-card";
    card.dataset.groupId = group.group_id;
    if (group.group_id === currentGroup?.group_id) {
      card.classList.add("is-active");
    }

    const avatar = document.createElement("img");
    avatar.className = "group-card-avatar";
    avatar.src =
      group.avatar ||
      (group.is_default_group
        ? DEFAULT_GROUP_ICON
        : "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='96' height='96' viewBox='0 0 96 96'><rect width='96' height='96' rx='24' fill='%23e8c49a'/><text x='48' y='56' text-anchor='middle' font-size='34' fill='%23824f1f' font-family='Arial'>G</text></svg>");
    avatar.alt = group.group_name + " 头像";
    avatar.loading = "lazy";
    card.appendChild(avatar);

    const main = document.createElement("div");
    main.className = "group-card-main";

    const title = document.createElement("div");
    title.className = "group-card-title";

    const name = document.createElement("div");
    name.className = "group-card-name";
    name.textContent = group.group_name || "群 " + group.group_id;
    title.appendChild(name);

    const badgesEl = document.createElement("span");
    badgesEl.className = "group-card-badges";
    title.appendChild(badgesEl);

    main.appendChild(title);

    const subline = document.createElement("div");
    subline.className = "group-card-subline";
    if (group.is_default_group) {
      subline.innerHTML =
        '<span class="group-card-id">默认模板</span><span>所有群共用的默认审核配置</span>';
    } else {
      subline.innerHTML =
        '<span class="group-card-id">' + group.group_id + "</span>";
    }
    main.appendChild(subline);

    card.appendChild(main);

    card.addEventListener("click", () => {
      switchGroup(group);
    });

    updateCardBadges(card, group, enabledGroups);
    fragment.appendChild(card);
  });

  // Remove stale cards that no longer exist in groups
  existingCards.forEach((card) => card.remove());

  els.groupList.innerHTML = "";
  els.groupList.appendChild(fragment);
}

function updateCardBadges(card, group, enabledGroups) {
  const badgesEl = card.querySelector(".group-card-badges");
  if (!badgesEl) return;
  badgesEl.innerHTML = "";

  const badges = [];
  if (group.is_default_group) {
    badges.push({ cls: "group-card-badge", text: "全局" });
  }
  if (group.template_name) {
    badges.push({ cls: "group-card-badge", text: group.template_name });
  }
  if (!group.is_default_group && enabledGroups.includes(String(group.group_id))) {
    const cfg = group.config || {};
    const textOn = cfg.enable_text_censor !== false;
    const imageOn = cfg.enable_image_censor !== false;
    if (!textOn && !imageOn) {
      badges.push({ cls: "group-card-badge disabled", text: "已停用" });
    } else {
      badges.push({ cls: "group-card-badge enabled", text: "已启用" });
    }
  }

  badges.forEach((badgeInfo) => {
    const badge = document.createElement("span");
    badge.className = badgeInfo.cls;
    badge.textContent = badgeInfo.text;
    badgesEl.appendChild(badge);
  });
}

/* ── group detail ── */
function renderGroupDetailHeader(groupPayload) {
  const group = groupPayload;
  const info = group.group_info || {};
  const groupName = info.group_name || group.group_name || "群 " + group.group_id;
  const groupId = group.group_id || "";

  els.groupDetailHeader.style.display = "";
  els.groupInfoName.textContent = groupName;

  if (groupId === DEFAULT_GROUP_ID) {
    els.groupInfoSub.textContent = "全局默认配置";
  } else {
    els.groupInfoSub.textContent = "群号: " + groupId;
  }

  if (els.groupInfoAvatar) {
    els.groupInfoAvatar.src =
      (info.avatar || group.avatar) ||
      (group.is_default_group
        ? DEFAULT_GROUP_ICON
        : "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='96' height='96' viewBox='0 0 96 96'><rect width='96' height='96' rx='24' fill='%23e8c49a'/><text x='48' y='56' text-anchor='middle' font-size='34' fill='%23824f1f' font-family='Arial'>G</text></svg>");
    els.groupInfoAvatar.alt = groupName + " 头像";
  }
}

function renderEnabledGroupsInfo() {
  const enabledGroups = Array.isArray(bootstrapData?.enabled_groups)
    ? bootstrapData.enabled_groups
    : [];
  if (!enabledGroups.length) {
    els.enabledGroupsInfo.style.display = "none";
    return;
  }
  els.enabledGroupsInfo.style.display = "";
  els.enabledGroupsInfo.innerHTML = `
    <div class="section-head">
      <div class="section-title">已启用审核的群</div>
      <div class="section-hint">以下群号已开启内容审核（在 AstrBot WebUI 配置中管理）</div>
    </div>
    <div class="enabled-groups-chips">
      ${enabledGroups.map((gid) => `<span class="group-card-badge enabled">${gid}</span>`).join(" ")}
    </div>
  `;
}

function renderGroupForm(groupPayload) {
  currentGroup = groupPayload;

  // Render detail header with avatar + name (same pattern as qqadmin)
  renderGroupDetailHeader(groupPayload);

  const isDefault = Boolean(groupPayload.is_default_group);

  if (isDefault) {
    // Default group: show global config (baidu_audit) + disposal.default
    const globalSchema = bootstrapData?.schema?.global || {};
    const globalConfig = bootstrapData?.global_config || {};
    const defaultSchema = bootstrapData?.schema?.default || {};
    const defaultConfig = groupPayload.config || {};

    els.groupForm.innerHTML = "";

    // Render global config section (baidu_audit etc.)
    if (Object.keys(globalSchema).length > 0) {
      Object.entries(globalSchema).forEach(([key, fieldSchema]) => {
        const val = globalConfig[key] ?? fieldSchema.default;
        els.groupForm.appendChild(buildField(key, fieldSchema, val, "__global__." + key));
      });
    }

    // Render disposal.default section
    renderSchemaFields(els.groupForm, defaultSchema, defaultConfig);

    renderEnabledGroupsInfo();
    els.deleteGroupBtn.style.display = "none";
    els.saveGroupBtn.textContent = "保存默认全局配置";
  } else {
    // Per-group: show template schema
    const schema = bootstrapData?.schema?.default || {};
    const config = groupPayload.config || {};
    renderSchemaFields(els.groupForm, schema, config);

    els.enabledGroupsInfo.style.display = "none";
    els.deleteGroupBtn.style.display = "";
    els.saveGroupBtn.textContent = "保存此群配置";
  }

  renderGroupCards();
}

function switchGroup(group) {
  if (currentGroup?.group_id !== group.group_id) {
    saveCurrentGroupSilent(currentGroup);
  }
  renderGroupForm(group);
}

/* ── API helpers ── */
async function loadBootstrapData() {
  const data = await api.safeGet("settings/bootstrap");
  bootstrapData = data;
  allGroups = normalizeGroups(data.groups || []);
}

async function loadGroupConfig(groupId) {
  const target = String(groupId || "").trim();
  if (!target) return;

  const data = await api.safeGet("settings/group", { group_id: target });
  renderGroupForm(data);
}

async function saveGroupConfig() {
  if (!currentGroup) {
    showToast("请先选择一个群配置", "error");
    return;
  }

  const payload = collectFormData(els.groupForm);
  const { globalConfig, disposalConfig } = splitGlobalAndDisposal(payload);

  const requestBody = {
    group_id: currentGroup.group_id,
    config: disposalConfig,
  };
  if (Object.keys(globalConfig).length > 0) {
    requestBody.global_config = globalConfig;
  }

  const data = await api.safePost("settings/group", requestBody);

  // Update local data without re-fetching
  Object.assign(currentGroup, data);
  const idx = allGroups.findIndex((g) => g.group_id === currentGroup.group_id);
  if (idx !== -1) {
    allGroups[idx] = { ...allGroups[idx], ...data };
  }
  if (Object.keys(globalConfig).length > 0) {
    Object.assign(bootstrapData.global_config, globalConfig);
  }

  renderGroupCards();
  showToast("配置已保存");
}

async function saveCurrentGroupSilent(group) {
  if (!group || !els.groupForm.querySelector("[data-path]")) return;
  try {
    const payload = collectFormData(els.groupForm);
    const { globalConfig, disposalConfig } = splitGlobalAndDisposal(payload);

    const requestBody = {
      group_id: group.group_id,
      config: disposalConfig,
    };
    if (Object.keys(globalConfig).length > 0) {
      requestBody.global_config = globalConfig;
    }

    await api.safePost("settings/group", requestBody);
  } catch {}
}

async function deleteGroupConfig() {
  if (!currentGroup || currentGroup.is_default_group) {
    showToast("不能删除默认全局配置", "error");
    return;
  }

  await api.safePost("settings/group/delete", {
    group_id: currentGroup.group_id,
  });

  // Switch to default group
  const defaultGroup = allGroups.find((g) => g.is_default_group);
  if (defaultGroup) {
    await loadGroupConfig(defaultGroup.group_id);
  }

  await loadBootstrapData();
  renderGroupCards(true);
  showToast("群配置已删除");
}

/* ── add-group modal ── */
let modalAvailableGroups = [];
let modalSelectedGroup = null;

const CHECK_SVG = '<svg xmlns="http://www.w3.org/2000/svg" height="24px" viewBox="0 -960 960 960" width="24px" fill="currentColor"><path d="m424-296 282-282-56-56-226 226-114-114-56 56 170 170Zm56 216q-83 0-156-31.5T197-197q-54-54-85.5-127T80-480q0-83 31.5-156T197-763q54-54 127-85.5T480-880q83 0 156 31.5T763-763q54 54 85.5 127T880-480q0 83-31.5 156T763-197q-54 54-127 85.5T480-80Zm0-80q134 0 227-93t93-227q0-134-93-227t-227-93q-134 0-227 93t-93 227q0 134 93 227t227 93Zm0-320Z"/></svg>';

function openAddGroupModal() {
  modalSelectedGroup = null;
  els.addGroupModal.style.display = "";
  els.modalSearchInput.value = "";
  els.modalConfirmBtn.disabled = true;
  loadAvailableGroups();
  els.modalSearchInput.focus();
}

function closeAddGroupModal() {
  els.addGroupModal.style.display = "none";
  modalSelectedGroup = null;
}

async function loadAvailableGroups() {
  els.modalGroupList.innerHTML = '<div class="modal-loading">加载中...</div>';
  try {
    const data = await api.safeGet("settings/available-groups");
    modalAvailableGroups = Array.isArray(data) ? data : [];
    renderModalGroups();
  } catch (error) {
    els.modalGroupList.innerHTML = '<div class="modal-empty">加载失败: ' + (error.message || "未知错误") + '</div>';
  }
}

function renderModalGroups() {
  const keyword = String(els.modalSearchInput.value || "")
    .trim()
    .toLowerCase();

  let filtered = modalAvailableGroups;
  if (keyword) {
    filtered = modalAvailableGroups.filter((g) => {
      const gid = String(g.group_id || "").toLowerCase();
      const gname = String(g.group_name || "").toLowerCase();
      return gid.includes(keyword) || gname.includes(keyword);
    });
  }

  if (!filtered.length) {
    els.modalGroupList.innerHTML = '<div class="modal-empty">' + (keyword ? "没有匹配的群" : "所有群都已有配置") + "</div>";
    return;
  }

  els.modalGroupList.innerHTML = "";

  filtered.forEach((group) => {
    const item = document.createElement("div");
    item.className = "modal-group-item";
    if (modalSelectedGroup && modalSelectedGroup.group_id === group.group_id) {
      item.classList.add("is-selected");
    }

    const avatar = document.createElement("img");
    avatar.className = "modal-group-item-avatar";
    avatar.src = group.avatar || "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='96' height='96' viewBox='0 0 96 96'><rect width='96' height='96' rx='24' fill='%23e8c49a'/><text x='48' y='56' text-anchor='middle' font-size='34' fill='%23824f1f' font-family='Arial'>G</text></svg>";
    avatar.alt = group.group_name;
    avatar.loading = "lazy";
    item.appendChild(avatar);

    const info = document.createElement("div");
    info.className = "modal-group-item-info";

    const name = document.createElement("div");
    name.className = "modal-group-item-name";
    name.textContent = group.group_name || "群 " + group.group_id;
    info.appendChild(name);

    const idLine = document.createElement("div");
    idLine.className = "modal-group-item-id";
    idLine.textContent = group.group_id + (group.member_count ? " | " + group.member_count + " 人" : "");
    info.appendChild(idLine);

    item.appendChild(info);

    const check = document.createElement("span");
    check.className = "modal-group-check";
    check.innerHTML = CHECK_SVG;
    item.appendChild(check);

    item.addEventListener("click", () => {
      modalSelectedGroup = group;
      els.modalConfirmBtn.disabled = false;
      renderModalGroups();
    });

    els.modalGroupList.appendChild(item);
  });
}

async function confirmAddGroup() {
  if (!modalSelectedGroup) return;
  const group = modalSelectedGroup;
  const groupId = group.group_id;
  closeAddGroupModal();

  try {
    const defaultGroup = allGroups.find((g) => g.is_default_group);
    const newConfig = defaultGroup?.config ? { ...defaultGroup.config } : {};

    await api.safePost("settings/group", {
      group_id: groupId,
      config: newConfig,
    });

    await loadBootstrapData();
    renderGroupCards(true);

    const newGroup = allGroups.find((g) => g.group_id === groupId);
    if (newGroup) {
      renderGroupForm(newGroup);
    }

    showToast("群 " + group.group_name + " 配置已添加");
  } catch (error) {
    showToast(error.message || "添加群配置失败", "error");
  }
}

/* ── delete confirm modal ── */
function openDeleteConfirmModal() {
  if (!currentGroup || currentGroup.is_default_group) {
    showToast("不能删除默认全局配置", "error");
    return;
  }
  const info = currentGroup.group_info || {};
  const displayName = info.group_name || currentGroup.group_name || currentGroup.group_id;
  els.deleteConfirmBody.textContent = '确定要删除 "' + displayName + '"（' + currentGroup.group_id + '）的配置吗？此操作不可撤销。';
  els.deleteConfirmModal.style.display = "";
}

function closeDeleteConfirmModal() {
  els.deleteConfirmModal.style.display = "none";
}

/* ── events ── */
function bindEvents() {
  els.toggleThemeBtn.addEventListener("click", () => cycleThemePreference());

  els.addGroupBtn.addEventListener("click", () => openAddGroupModal());

  els.modalCloseBtn.addEventListener("click", () => closeAddGroupModal());

  els.addGroupModal.addEventListener("click", (e) => {
    if (e.target === els.addGroupModal) closeAddGroupModal();
  });

  els.modalSearchInput.addEventListener("input", () => renderModalGroups());

  els.modalConfirmBtn.addEventListener("click", () => confirmAddGroup());

  els.saveGroupBtn.addEventListener("click", async () => {
    try {
      await saveGroupConfig();
    } catch (error) {
      showToast(error.message, "error");
    }
  });

  els.deleteGroupBtn.addEventListener("click", () => openDeleteConfirmModal());

  els.deleteConfirmCloseBtn.addEventListener("click", () => closeDeleteConfirmModal());

  els.deleteConfirmCancelBtn.addEventListener("click", () => closeDeleteConfirmModal());

  els.deleteConfirmModal.addEventListener("click", (e) => {
    if (e.target === els.deleteConfirmModal) closeDeleteConfirmModal();
  });

  els.deleteConfirmOkBtn.addEventListener("click", async () => {
    closeDeleteConfirmModal();
    try {
      await deleteGroupConfig();
    } catch (error) {
      showToast(error.message, "error");
    }
  });

  els.groupSearchInput.addEventListener("input", () => renderGroupCards());
}

/* ── init ── */
async function init() {
  bindSystemTheme();
  updateThemeButton();
  applyThemeMode(resolveThemeMode(null));

  if (!bridge) return;

  try {
    api = createApi(bridge);
  } catch {
    return;
  }

  try {
    if (typeof bridge.ready === "function") {
      const context = await Promise.race([
        bridge.ready(),
        new Promise((_, reject) =>
          setTimeout(() => reject(new Error("Bridge ready timeout")), 5000)
        ),
      ]);
      syncThemeFromContext(context);
    }

    if (typeof bridge.onContext === "function") {
      detachContextHandler = bridge.onContext((context) => {
        syncThemeFromContext(context);
      });
    } else {
      syncThemeFromContext(bridge.getContext?.());
    }

    bindEvents();
    await loadBootstrapData();
    renderGroupCards();

    // Auto-select default group
    const defaultGroup = allGroups.find((g) => g.is_default_group);
    if (defaultGroup) {
      await loadGroupConfig(defaultGroup.group_id);
    }
  } catch (error) {
    const message = error?.message || "页面初始化失败";
    showToast(message, "error");
  }
}

window.addEventListener("beforeunload", () => {
  detachContextHandler?.();
  detachSystemThemeHandler?.();
});

init();
