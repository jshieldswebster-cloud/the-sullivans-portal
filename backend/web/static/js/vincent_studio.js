/** Vincent's Studio — batch upload, shot checklist, Ideal Row push */

const state = {
  checklist: null,
  recentUploads: [],
};

const eventName = document.getElementById("event-name");
const selectedCategory = document.getElementById("selected-category");
const uploadShotId = document.getElementById("upload-shot-id");

function eventReady() {
  return eventName.value.trim().length > 0 && selectedCategory.value;
}

function setActiveCategory(cat) {
  selectedCategory.value = cat;
  document.getElementById("upload-target-label").textContent = cat;
  document.querySelectorAll(".category-tab").forEach((tab) => {
    const active = tab.dataset.category === cat;
    tab.classList.toggle("category-tab-active", active);
    tab.classList.toggle("text-muted", !active);
  });
  loadChecklist();
}

document.querySelectorAll(".category-tab").forEach((tab, idx) => {
  if (idx === 0) setActiveCategory(tab.dataset.category);
  tab.addEventListener("click", () => setActiveCategory(tab.dataset.category));
});

eventName.addEventListener("change", loadChecklist);
eventName.addEventListener("blur", loadChecklist);

async function loadChecklist() {
  if (!eventReady()) return;
  const qs = new URLSearchParams({
    category: selectedCategory.value,
    event_name: eventName.value.trim(),
  });
  const res = await fetch(`/api/studio/vincent/checklist?${qs}`);
  if (!res.ok) return;
  state.checklist = await res.json();
  renderChecklist();
  populateShotSelect();
}

function populateShotSelect() {
  if (!state.checklist) return;
  uploadShotId.innerHTML = '<option value="">General batch ingest</option>';
  state.checklist.shots.forEach((shot) => {
    const opt = document.createElement("option");
    opt.value = shot.id;
    opt.textContent = `[Post ${shot.post}] ${shot.label}`;
    uploadShotId.appendChild(opt);
  });
}

function renderChecklist() {
  const { shots, state: shotState, progress } = state.checklist;
  const byPost = { 1: [], 2: [], 3: [] };
  shots.forEach((s) => byPost[s.post].push(s));

  [1, 2, 3].forEach((post) => {
    const container = document.getElementById(`checklist-post-${post}`);
    container.innerHTML = "";
    byPost[post].forEach((shot) => {
      const st = shotState[shot.id] || {};
      const item = document.createElement("div");
      item.className = `shot-checklist-item ${st.checked ? "checked" : ""}`;
      item.innerHTML = `
        <input type="checkbox" data-shot-id="${shot.id}" ${st.checked ? "checked" : ""} />
        <div class="flex-1 min-w-0">
          <div class="flex items-center gap-2 flex-wrap">
            <span class="shot-post-badge">Post ${shot.post}</span>
            <span class="font-medium text-sm">${shot.label}</span>
          </div>
          <p class="text-xs text-muted mt-0.5">${shot.hint}</p>
          ${st.file_url ? `<img src="${st.file_url}" class="mt-2 h-16 w-16 object-cover rounded-lg border border-sullivan-taupe" alt="" />` : ""}
        </div>
        <label class="text-xs text-sullivan-green cursor-pointer shrink-0">
          <input type="file" class="hidden shot-upload-input" data-shot-id="${shot.id}" accept="image/*,video/*" />
          Upload
        </label>
      `;
      item.querySelector('input[type="checkbox"]').addEventListener("change", (e) => {
        toggleShot(shot.id, e.target.checked);
      });
      item.querySelector(".shot-upload-input").addEventListener("change", (e) => {
        if (e.target.files.length) uploadFiles(e.target.files, shot.id);
      });
      container.appendChild(item);
    });

    const prog = progress[`post_${post}`];
    const el = document.getElementById(`prog-post-${post}`);
    if (el && prog) el.textContent = `${prog.done} / ${prog.total} complete`;
    if (post === 2) {
      const pct = prog ? (prog.done / prog.total) * 100 : 0;
      document.getElementById("bar-post-2").style.width = `${pct}%`;
    }
  });

  const total = progress.post_1.total + progress.post_2.total + progress.post_3.total;
  const done = progress.post_1.done + progress.post_2.done + progress.post_3.done;
  document.getElementById("overall-progress").textContent = `${done} / ${total} shots tracked`;
}

async function toggleShot(shotId, checked) {
  if (!state.checklist) return;
  state.checklist.state[shotId] = { ...state.checklist.state[shotId], checked };
  await saveChecklist(false);
}

async function saveChecklist(showMsg = true) {
  if (!eventReady() || !state.checklist) return;
  const form = new FormData();
  form.append("category", selectedCategory.value);
  form.append("event_name", eventName.value.trim());
  form.append("state_json", JSON.stringify(state.checklist.state));
  const res = await fetch("/api/studio/vincent/checklist", { method: "POST", body: form });
  if (res.ok) {
    state.checklist = await res.json();
    renderChecklist();
    if (showMsg) {
      const st = document.getElementById("push-status");
      st.textContent = "Checklist saved";
      st.classList.remove("hidden");
      setTimeout(() => st.classList.add("hidden"), 2000);
    }
  }
}

document.getElementById("save-checklist-btn").addEventListener("click", () => saveChecklist(true));

const dropZone = document.getElementById("batch-drop-zone");
const fileInput = document.getElementById("batch-file-input");

dropZone.addEventListener("click", () => fileInput.click());
dropZone.addEventListener("dragover", (e) => {
  e.preventDefault();
  dropZone.classList.add("dragover");
});
dropZone.addEventListener("dragleave", () => dropZone.classList.remove("dragover"));
dropZone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropZone.classList.remove("dragover");
  if (e.dataTransfer.files.length) uploadFiles(e.dataTransfer.files);
});
fileInput.addEventListener("change", () => {
  if (fileInput.files.length) uploadFiles(fileInput.files);
});

window.onDriveImportVincent = async (files) => {
  if (!files.length) return;
  await uploadFiles(files);
};

async function uploadFiles(fileList, shotId = null) {
  if (!eventReady()) {
    alert("Enter an event name first.");
    return;
  }

  const form = new FormData();
  form.append("category", selectedCategory.value);
  form.append("event_name", eventName.value.trim());
  const sid = shotId || uploadShotId.value;
  if (sid) form.append("shot_id", sid);
  for (const f of fileList) form.append("files", f);

  const status = document.getElementById("upload-status");
  status.classList.remove("hidden");
  status.textContent = "Uploading…";

  const res = await fetch("/api/studio/vincent/batch-upload", { method: "POST", body: form });
  const data = await res.json();
  if (!res.ok) {
    status.textContent = data.error || "Upload failed";
    return;
  }

  status.textContent = `Uploaded ${data.uploaded} file(s)`;
  state.recentUploads = [...data.files, ...state.recentUploads].slice(0, 12);
  renderRecentUploads();
  await loadChecklist();
}

function renderRecentUploads() {
  const el = document.getElementById("recent-uploads");
  el.innerHTML = state.recentUploads
    .map(
      (f) =>
        `<div class="aspect-square rounded-lg overflow-hidden border border-sullivan-taupe shadow-luxe"><img src="${f.url}" class="w-full h-full object-cover" alt="" /></div>`
    )
    .join("");
}

document.getElementById("push-ideal-row-btn").addEventListener("click", async () => {
  if (!eventReady()) {
    alert("Enter an event name.");
    return;
  }
  const st = document.getElementById("push-status");
  st.classList.remove("hidden");
  st.textContent = "Pushing assets to Ideal Row folders…";

  const form = new FormData();
  form.append("category", selectedCategory.value);
  form.append("event_name", eventName.value.trim());

  const res = await fetch("/api/studio/vincent/push-ideal-row", { method: "POST", body: form });
  const data = await res.json();
  if (!res.ok) {
    st.textContent = data.error || "Push failed";
    st.className = "text-center text-sm text-red-400";
    return;
  }

  st.textContent = "Pushed to Ideal Row — opening packaging dashboard…";
  st.className = "text-center text-sm text-sullivan-green";
  setTimeout(() => {
    window.location.href = `/dashboard?category=${encodeURIComponent(data.category)}&event=${encodeURIComponent(data.event_name)}`;
  }, 1200);
});
