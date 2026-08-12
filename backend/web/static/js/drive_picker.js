/**
 * Google Drive cloud picker — shared by New Post & Vincent's Studio
 */
(function () {
  "use strict";

  const state = {
    connected: false,
    configured: false,
    mode: "multi",
    maxFiles: null,
    category: "",
    eventName: "",
    onImport: null,
    stack: [{ id: "root", name: "Drive" }],
    folders: [],
    files: [],
    selectedFileIds: new Set(),
    currentFolderId: null,
    importing: false,
  };

  const els = {};

  function $(id) {
    return document.getElementById(id);
  }

  async function fetchJson(url, options) {
    const res = await fetch(url, options);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || res.statusText);
    return data;
  }

  async function refreshStatus() {
    try {
      const data = await fetchJson("/api/studio/drive/status");
      state.connected = data.connected;
      state.configured = data.configured;
      updateStatusPills();
      return data;
    } catch {
      state.connected = false;
      updateStatusPills();
      return null;
    }
  }

  function updateStatusPills() {
    document.querySelectorAll("[data-drive-status]").forEach((el) => {
      el.textContent = state.connected
        ? "Drive Connected"
        : state.configured
          ? "Drive Offline"
          : "Drive Not Configured";
      el.classList.toggle("drive-status-connected", state.connected);
      el.classList.toggle("drive-status-offline", !state.connected);
    });
    document.querySelectorAll("[data-drive-connect]").forEach((btn) => {
      btn.classList.toggle("hidden", state.connected);
    });
    document.querySelectorAll("[data-drive-browse]").forEach((btn) => {
      btn.disabled = !state.connected;
    });
  }

  function connectDrive(returnTo) {
    const path = returnTo || window.location.pathname;
    window.location.href = `/api/studio/drive/oauth/start?return_to=${encodeURIComponent(path)}`;
  }

  function renderCategoryChips(categories) {
    const wrap = els.categoryChips;
    if (!wrap) return;
    wrap.innerHTML = categories
      .map(
        (cat) =>
          `<button type="button" class="drive-chip ${cat === state.category ? "drive-chip-active" : ""}" data-cat="${cat}">${cat}</button>`
      )
      .join("");
    wrap.querySelectorAll(".drive-chip").forEach((chip) => {
      chip.addEventListener("click", async () => {
        state.category = chip.dataset.cat;
        renderCategoryChips(categories);
        await searchCategory(chip.dataset.cat);
      });
    });
  }

  function renderBreadcrumb() {
    const bc = els.breadcrumb;
    if (!bc) return;
    bc.innerHTML = state.stack
      .map((crumb, i) => {
        const isLast = i === state.stack.length - 1;
        const cls = isLast ? "drive-crumb-muted" : "drive-crumb";
        return `<span class="${cls}" data-idx="${i}">${crumb.name}</span>${isLast ? "" : " / "}`;
      })
      .join("");
    bc.querySelectorAll(".drive-crumb").forEach((el) => {
      el.addEventListener("click", () => {
        const idx = parseInt(el.dataset.idx, 10);
        state.stack = state.stack.slice(0, idx + 1);
        const crumb = state.stack[idx];
        if (crumb.id === "search") return;
        loadFolder(crumb.id === "root" ? null : crumb.id);
      });
    });
  }

  function renderList() {
    const list = els.list;
    if (!list) return;

    if (!state.folders.length && !state.files.length) {
      list.innerHTML = '<p class="drive-empty">No folders or images here.</p>';
      return;
    }

    const folderRows = state.folders
      .map(
        (f) => `
      <div class="drive-row drive-folder-row" data-folder-id="${f.id}" data-folder-name="${f.name}">
        <span class="drive-row-icon">📁</span>
        <div class="drive-row-meta">
          <p class="drive-row-title">${f.name}</p>
          <p class="drive-row-sub">Folder</p>
        </div>
      </div>`
      )
      .join("");

    const fileRows = state.files
      .map((f) => {
        const selected = state.selectedFileIds.has(f.id);
        return `
      <div class="drive-row drive-file-row ${selected ? "drive-row-selected" : ""}" data-file-id="${f.id}">
        <input type="checkbox" class="drive-file-check accent-[#2F5233]" ${selected ? "checked" : ""} />
        <span class="drive-row-icon">🖼</span>
        <div class="drive-row-meta">
          <p class="drive-row-title">${f.name}</p>
          <p class="drive-row-sub">${formatSize(f.size)}</p>
        </div>
      </div>`;
      })
      .join("");

    list.innerHTML = folderRows + fileRows;

    list.querySelectorAll(".drive-folder-row").forEach((row) => {
      row.addEventListener("click", () => {
        const id = row.dataset.folderId;
        const name = row.dataset.folderName;
        state.stack.push({ id, name });
        loadFolder(id);
      });
    });

    list.querySelectorAll(".drive-file-row").forEach((row) => {
      const toggle = () => {
        const id = row.dataset.fileId;
        if (state.mode === "single") {
          state.selectedFileIds.clear();
          state.selectedFileIds.add(id);
        } else if (state.selectedFileIds.has(id)) {
          state.selectedFileIds.delete(id);
        } else {
          if (state.maxFiles && state.selectedFileIds.size >= state.maxFiles) {
            alert(`Select at most ${state.maxFiles} images.`);
            return;
          }
          state.selectedFileIds.add(id);
        }
        renderList();
        updateSelectionLabel();
      };
      row.addEventListener("click", (e) => {
        if (e.target.type !== "checkbox") toggle();
      });
      row.querySelector(".drive-file-check")?.addEventListener("change", toggle);
    });

    updateSelectionLabel();
  }

  function formatSize(bytes) {
    if (!bytes) return "";
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  function updateSelectionLabel() {
    const n = state.selectedFileIds.size;
    if (els.selectionLabel) {
      if (state.mode === "single") {
        els.selectionLabel.textContent = n ? "1 image selected" : "Select one cover image";
      } else if (state.maxFiles) {
        els.selectionLabel.textContent = `${n} / ${state.maxFiles} selected`;
      } else {
        els.selectionLabel.textContent = n ? `${n} image(s) selected` : "Select images or use entire folder";
      }
    }
    if (els.importBtn) els.importBtn.disabled = n === 0 || state.importing;
    if (els.selectFolderBtn) {
      els.selectFolderBtn.classList.toggle("hidden", state.mode === "single");
      els.selectFolderBtn.disabled = !state.currentFolderId || state.importing;
    }
  }

  async function loadFolder(parentId) {
    state.currentFolderId = parentId;
    state.selectedFileIds.clear();
    els.list.innerHTML = '<p class="drive-empty">Loading…</p>';
    renderBreadcrumb();

    const parentParam = parentId ? `parent_id=${encodeURIComponent(parentId)}` : "";
    const folderData = await fetchJson(`/api/studio/drive/folders?${parentParam}`);
    state.folders = folderData.folders || [];

    if (parentId) {
      const fileData = await fetchJson(`/api/studio/drive/files?folder_id=${encodeURIComponent(parentId)}`);
      state.files = fileData.files || [];
    } else {
      state.files = [];
    }
    renderList();
  }

  async function searchCategory(category) {
    els.list.innerHTML = '<p class="drive-empty">Searching category folders…</p>';
    const data = await fetchJson(`/api/studio/drive/search?category=${encodeURIComponent(category)}`);
    state.folders = data.folders || [];
    state.files = [];
    state.stack = [
      { id: "root", name: "Drive" },
      { id: "search", name: category },
    ];
    state.currentFolderId = null;
    renderBreadcrumb();
    renderList();
  }

  async function importSelection(useFolder = false) {
    if (!state.eventName.trim()) {
      alert("Enter an event name first.");
      return;
    }
    if (!state.category) {
      alert("Select a category first.");
      return;
    }

    state.importing = true;
    updateSelectionLabel();
    els.importBtn.textContent = "Importing…";

    try {
      const body = {
        category: state.category,
        event_name: state.eventName.trim(),
      };
      if (useFolder && state.currentFolderId) {
        body.folder_id = state.currentFolderId;
        if (state.maxFiles) body.limit = state.maxFiles;
      } else {
        body.file_ids = Array.from(state.selectedFileIds);
      }

      const data = await fetchJson("/api/studio/drive/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });

      const files = await Promise.all(
        (data.files || []).map(async (f) => {
          const res = await fetch(f.url);
          const blob = await res.blob();
          const type = blob.type || f.mime_type || "image/jpeg";
          return new File([blob], f.filename || "photo.jpg", { type });
        })
      );

      if (state.onImport) await state.onImport(files, data);
      closeModal();
    } catch (err) {
      alert(err.message || "Drive import failed");
    } finally {
      state.importing = false;
      els.importBtn.textContent = "Import Selected";
      updateSelectionLabel();
    }
  }

  function openModal(options) {
    state.mode = options.mode || "multi";
    state.maxFiles =
      options.maxFiles ?? (state.mode === "single" ? 1 : state.mode === "multi-8" ? 8 : null);
    state.category = options.category || "";
    state.eventName = options.eventName || "";
    state.onImport = options.onImport || null;
    state.stack = [{ id: "root", name: "Drive" }];
    state.selectedFileIds.clear();

    els.backdrop.classList.remove("hidden");
    els.backdrop.setAttribute("aria-hidden", "false");

    refreshStatus().then((status) => {
      if (status?.categories) renderCategoryChips(status.categories);
      if (state.connected) loadFolder(null);
      else {
        els.list.innerHTML = '<p class="drive-empty">Connect Google Drive to browse cloud folders.</p>';
      }
    });
  }

  function closeModal() {
    els.backdrop.classList.add("hidden");
    els.backdrop.setAttribute("aria-hidden", "true");
  }

  function init() {
    els.backdrop = $("drive-modal-backdrop");
    els.list = $("drive-list");
    els.breadcrumb = $("drive-breadcrumb");
    els.categoryChips = $("drive-category-chips");
    els.importBtn = $("drive-import-btn");
    els.selectFolderBtn = $("drive-select-folder-btn");
    els.selectionLabel = $("drive-selection-label");

    $("drive-modal-close")?.addEventListener("click", closeModal);
    els.backdrop?.addEventListener("click", (e) => {
      if (e.target === els.backdrop) closeModal();
    });
    els.importBtn?.addEventListener("click", () => importSelection(false));
    els.selectFolderBtn?.addEventListener("click", () => importSelection(true));

    document.querySelectorAll("[data-drive-connect]").forEach((btn) => {
      btn.addEventListener("click", () => connectDrive(btn.dataset.returnTo));
    });

    document.querySelectorAll("[data-drive-browse]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const callbackName = btn.dataset.driveCallback;
        openModal({
          mode: btn.dataset.driveMode || "multi",
          maxFiles: btn.dataset.driveMax ? parseInt(btn.dataset.driveMax, 10) : undefined,
          category: btn.dataset.driveCategory || document.getElementById("selected-category")?.value,
          eventName: btn.dataset.driveEvent || document.getElementById("event-name")?.value,
          onImport: callbackName && window[callbackName] ? window[callbackName] : null,
        });
      });
    });

    if (new URLSearchParams(window.location.search).get("drive") === "connected") {
      refreshStatus();
    } else {
      refreshStatus();
    }
  }

  window.DrivePicker = { init, open: openModal, refreshStatus, connectDrive };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
