/** VV LUXE Studio — Ideal Row wizard + Instagram Export */

const POST_2_REQUIRED = 8;

const state = {
  step: 1,
  post1: null,
  post1Url: null,
  post2: [],
  post2Urls: [],
  post3: [],
  post3Urls: [],
  post2PreviewIdx: 0,
  savedData: null,
  reelJobId: null,
  reelReady: false,
  exportReady: false,
  savedPost2Slides: [],
  savedPost2Idx: 0,
  audioPath: null,
  pollTimer: null,
};

const eventName = document.getElementById("event-name");
const selectedCategory = document.getElementById("selected-category");

// ── Wizard navigation ────────────────────────────────────────────────────────

function goToStep(n) {
  state.step = n;
  document.querySelectorAll(".wizard-panel").forEach((el) => el.classList.add("hidden"));
  document.getElementById(`step-${n}`).classList.remove("hidden");
  document.querySelectorAll(".wizard-step").forEach((btn) => {
    const s = parseInt(btn.dataset.step, 10);
    btn.classList.toggle("active", s === n);
    btn.classList.toggle("completed", s < n);
  });
  if (n === 4) updateTemplatePreview();
  if (n === 5 && state.savedData) updateSavedPreview();
}

document.querySelectorAll(".wizard-step").forEach((btn) => {
  btn.addEventListener("click", () => {
    const target = parseInt(btn.dataset.step, 10);
    if (target <= maxReachableStep()) goToStep(target);
  });
});

document.querySelectorAll(".wizard-back-btn").forEach((btn) => {
  btn.addEventListener("click", () => goToStep(parseInt(btn.dataset.goto, 10)));
});

function maxReachableStep() {
  if (!state.savedData) {
    if (state.post3.length) return 4;
    if (state.post2.length === POST_2_REQUIRED) return 3;
    if (state.post1) return 2;
    return 1;
  }
  return 5;
}

// ── Category ─────────────────────────────────────────────────────────────────

function setActiveCategory(cat) {
  selectedCategory.value = cat;
  document.querySelectorAll(".category-tab").forEach((tab) => {
    const active = tab.dataset.category === cat;
    tab.classList.toggle("category-tab-active", active);
    tab.classList.toggle("border-sullivan-taupe", !active);
    tab.classList.toggle("text-muted", !active);
  });
  validate();
}

document.querySelectorAll(".category-tab").forEach((tab, idx) => {
  if (idx === 0) setActiveCategory(tab.dataset.category);
  tab.addEventListener("click", () => setActiveCategory(tab.dataset.category));
});

// ── Validation ─────────────────────────────────────────────────────────────

function validate() {
  const nameOk = eventName.value.trim().length > 0;
  const p1Ok = state.post1 !== null;
  const p2Ok = state.post2.length === POST_2_REQUIRED;
  const p3Ok = state.post3.length > 0;
  const ready = nameOk && p1Ok && p2Ok && p3Ok;

  document.getElementById("step-1-next").disabled = !p1Ok;
  document.getElementById("step-2-next").disabled = !p2Ok;
  document.getElementById("step-3-next").disabled = !p3Ok;
  document.getElementById("save-row-btn").disabled = !ready;

  const hint = document.getElementById("validation-hint");
  if (!nameOk) hint.textContent = "Enter an event name";
  else if (!p1Ok) hint.textContent = "Step 1: upload cover photo";
  else if (!p2Ok) hint.textContent = `Step 2: upload ${POST_2_REQUIRED} carousel photos (${state.post2.length}/${POST_2_REQUIRED})`;
  else if (!p3Ok) hint.textContent = "Step 3: upload reel photos";
  else hint.textContent = "Ready to save your Ideal Row";

  document.getElementById("prepare-instagram-btn").disabled = !state.reelReady;

  return ready;
}

eventName.addEventListener("input", validate);

// ── Google Drive import callbacks ───────────────────────────────────────────

window.onDriveImportPost1 = async (files) => {
  const file = files[0];
  if (!file) return;
  if (state.post1Url) URL.revokeObjectURL(state.post1Url);
  state.post1 = file;
  state.post1Url = URL.createObjectURL(file);
  document.getElementById("post-1-preview").classList.remove("hidden");
  document.getElementById("post-1-img").src = state.post1Url;
  document.getElementById("post-1-status").textContent = `${file.name} (Drive)`;
  validate();
};

window.onDriveImportPost2 = async (files) => {
  if (files.length !== POST_2_REQUIRED) {
    alert(`Post 2 requires exactly ${POST_2_REQUIRED} photos. Drive returned ${files.length}.`);
    return;
  }
  state.post2Urls.forEach((u) => URL.revokeObjectURL(u));
  state.post2 = files;
  state.post2Urls = files.map((f) => URL.createObjectURL(f));
  state.post2PreviewIdx = 0;
  document.getElementById("post-2-count").textContent = String(files.length);
  const grid = document.getElementById("post-2-grid");
  grid.classList.remove("hidden");
  grid.innerHTML = state.post2Urls
    .map((url, i) => `<img src="${url}" alt="${i + 1}" class="rounded-lg aspect-square object-cover" />`)
    .join("");
  validate();
};

window.onDriveImportPost3 = async (files) => {
  if (!files.length) return;
  state.post3Urls.forEach((u) => URL.revokeObjectURL(u));
  state.post3 = files;
  state.post3Urls = files.map((f) => URL.createObjectURL(f));
  document.getElementById("post-3-count").textContent = String(files.length);
  validate();
};

document.getElementById("step-1-next").addEventListener("click", () => goToStep(2));
document.getElementById("step-2-next").addEventListener("click", () => goToStep(3));
document.getElementById("step-3-next").addEventListener("click", () => goToStep(4));

// ── Post 1 ───────────────────────────────────────────────────────────────────

document.getElementById("post-1-input").addEventListener("change", (e) => {
  const file = e.target.files[0];
  if (!file) return;
  if (e.target.files.length > 1) {
    alert("Post 1 accepts exactly one cover image.");
    e.target.value = "";
    return;
  }
  if (state.post1Url) URL.revokeObjectURL(state.post1Url);
  state.post1 = file;
  state.post1Url = URL.createObjectURL(file);
  document.getElementById("post-1-preview").classList.remove("hidden");
  document.getElementById("post-1-img").src = state.post1Url;
  document.getElementById("post-1-status").textContent = file.name;
  validate();
});

// ── Post 2 ───────────────────────────────────────────────────────────────────

document.getElementById("post-2-input").addEventListener("change", (e) => {
  const files = Array.from(e.target.files);
  if (files.length !== POST_2_REQUIRED) {
    alert(`Post 2 requires exactly ${POST_2_REQUIRED} photos. You selected ${files.length}.`);
    e.target.value = "";
    return;
  }
  state.post2Urls.forEach((u) => URL.revokeObjectURL(u));
  state.post2 = files;
  state.post2Urls = files.map((f) => URL.createObjectURL(f));
  state.post2PreviewIdx = 0;

  document.getElementById("post-2-count").textContent = String(files.length);
  const grid = document.getElementById("post-2-grid");
  grid.classList.remove("hidden");
  grid.innerHTML = state.post2Urls
    .map((url, i) => `<img src="${url}" alt="${i + 1}" class="rounded-lg aspect-square object-cover" />`)
    .join("");
  validate();
});

// ── Post 3 ───────────────────────────────────────────────────────────────────

document.getElementById("post-3-input").addEventListener("change", (e) => {
  const files = Array.from(e.target.files);
  if (!files.length) return;
  state.post3Urls.forEach((u) => URL.revokeObjectURL(u));
  state.post3 = files;
  state.post3Urls = files.map((f) => URL.createObjectURL(f));
  document.getElementById("post-3-count").textContent = String(files.length);
  const grid = document.getElementById("post-3-grid");
  grid.classList.remove("hidden");
  grid.innerHTML = state.post3Urls
    .map((url) => `<img src="${url}" alt="" class="rounded-lg aspect-[4/5] object-cover" />`)
    .join("");
  validate();
});

// ── Reel settings ────────────────────────────────────────────────────────────

document.getElementById("clip-duration").addEventListener("input", (e) => {
  document.getElementById("duration-label").textContent = `${e.target.value}s`;
});
document.getElementById("transition-sec").addEventListener("input", (e) => {
  document.getElementById("transition-label").textContent = `${e.target.value}s`;
});
document.getElementById("audio-input").addEventListener("change", async (e) => {
  if (!e.target.files.length) return;
  const form = new FormData();
  form.append("file", e.target.files[0]);
  const res = await fetch("/api/studio/upload-audio", { method: "POST", body: form });
  if (res.ok) state.audioPath = (await res.json()).path;
});

// ── Template preview (Step 4) ────────────────────────────────────────────────

function updateTemplatePreview() {
  const p1img = document.getElementById("preview-post-1");
  const p1empty = document.getElementById("preview-post-1-empty");
  if (state.post1Url) {
    p1img.src = state.post1Url;
    p1img.classList.remove("hidden");
    p1empty.classList.add("hidden");
  } else {
    p1img.classList.add("hidden");
    p1empty.classList.remove("hidden");
  }

  const p2grid = document.getElementById("preview-post-2-grid");
  if (state.post2Urls.length) {
    p2grid.innerHTML = state.post2Urls
      .map((url, i) => `<img src="${url}" alt="${i + 1}" class="rounded object-cover w-full h-full" />`)
      .join("");
    document.getElementById("preview-p2-prev").disabled = false;
    document.getElementById("preview-p2-next").disabled = false;
    document.getElementById("preview-p2-counter").textContent = `${state.post2PreviewIdx + 1} / ${state.post2Urls.length}`;
  }

  const p3grid = document.getElementById("preview-post-3-grid");
  if (state.post3Urls.length) {
    p3grid.innerHTML = state.post3Urls
      .map((url) => `<img src="${url}" alt="" class="rounded object-cover aspect-[4/5]" />`)
      .join("");
  }
}

document.getElementById("preview-p2-prev").addEventListener("click", () => {
  if (!state.post2Urls.length) return;
  state.post2PreviewIdx = (state.post2PreviewIdx - 1 + state.post2Urls.length) % state.post2Urls.length;
  highlightPreviewP2();
});
document.getElementById("preview-p2-next").addEventListener("click", () => {
  if (!state.post2Urls.length) return;
  state.post2PreviewIdx = (state.post2PreviewIdx + 1) % state.post2Urls.length;
  highlightPreviewP2();
});

function highlightPreviewP2() {
  const imgs = document.querySelectorAll("#preview-post-2-grid img");
  imgs.forEach((img, i) => img.classList.toggle("ring-2", i === state.post2PreviewIdx));
  document.getElementById("preview-p2-counter").textContent =
    `${state.post2PreviewIdx + 1} / ${state.post2Urls.length}`;
}

// ── Save Event Row ───────────────────────────────────────────────────────────

document.getElementById("save-row-btn").addEventListener("click", async () => {
  if (!validate()) return;

  const btn = document.getElementById("save-row-btn");
  const status = document.getElementById("save-status");
  btn.disabled = true;
  btn.textContent = "Saving…";
  status.classList.remove("hidden");
  status.textContent = "Saving Ideal Row and starting reel render…";
  status.className = "text-sm text-muted text-center";

  const form = new FormData();
  form.append("category", selectedCategory.value);
  form.append("event_name", eventName.value.trim());
  form.append("post_1", state.post1);
  state.post2.forEach((f) => form.append("post_2", f));
  state.post3.forEach((f) => form.append("post_3", f));
  form.append("motion_style", document.getElementById("motion-style").value);
  form.append("clip_duration_sec", document.getElementById("clip-duration").value);
  form.append("transition_sec", document.getElementById("transition-sec").value);
  if (state.audioPath) form.append("audio_path", state.audioPath);

  try {
    const res = await fetch("/api/studio/ideal-row/save", { method: "POST", body: form });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Save failed");

    state.savedData = data;
    state.reelJobId = data.reel_job_id;
    state.savedPost2Slides = data.post_2.carousel_slides || [];
    state.savedPost2Idx = 0;

    status.textContent = "Event row saved — reel encoding in progress";
    status.className = "text-sm text-sullivan-green text-center";

    document.getElementById("saved-preview").classList.remove("hidden");
    goToStep(5);
    pollReelJob(data.reel_job_id);
  } catch (err) {
    status.textContent = err.message || "Save failed";
    status.className = "text-sm text-red-300 text-center";
    btn.disabled = false;
  } finally {
    btn.textContent = "Save Event Row";
  }
});

function updateSavedPreview() {
  if (!state.savedData) return;
  document.getElementById("saved-post-1").src = state.savedData.post_1.url;
  if (state.savedPost2Slides.length) {
    document.getElementById("saved-post-2").src = state.savedPost2Slides[0];
  }
}

document.getElementById("saved-p2-prev").addEventListener("click", () => {
  if (!state.savedPost2Slides.length) return;
  state.savedPost2Idx = (state.savedPost2Idx - 1 + state.savedPost2Slides.length) % state.savedPost2Slides.length;
  document.getElementById("saved-post-2").src = state.savedPost2Slides[state.savedPost2Idx];
});
document.getElementById("saved-p2-next").addEventListener("click", () => {
  if (!state.savedPost2Slides.length) return;
  state.savedPost2Idx = (state.savedPost2Idx + 1) % state.savedPost2Slides.length;
  document.getElementById("saved-post-2").src = state.savedPost2Slides[state.savedPost2Idx];
});

function pollReelJob(jobId) {
  if (state.pollTimer) clearInterval(state.pollTimer);
  const bar = document.getElementById("reel-progress-bar");
  const msg = document.getElementById("reel-progress-msg");

  state.pollTimer = setInterval(async () => {
    const res = await fetch(`/api/studio/montage/jobs/${jobId}`);
    if (!res.ok) return;
    const job = await res.json();

    bar.style.width = `${job.progress}%`;
    msg.textContent = job.message;

    if (job.status === "completed") {
      clearInterval(state.pollTimer);
      state.reelReady = true;
      validate();
      document.getElementById("reel-progress").classList.add("hidden");
      const video = document.getElementById("saved-reel");
      video.src = job.output_url;
      video.classList.remove("hidden");
      document.getElementById("prepare-status").textContent = "Reel ready — click Prepare for Instagram";
      document.getElementById("prepare-status").classList.remove("hidden");
    } else if (job.status === "failed") {
      clearInterval(state.pollTimer);
      msg.textContent = job.error || "Reel render failed";
      msg.className = "text-xs text-red-300 text-center";
    }
  }, 1500);
}

// ── Prepare for Instagram ────────────────────────────────────────────────────

document.getElementById("prepare-instagram-btn").addEventListener("click", async () => {
  const btn = document.getElementById("prepare-instagram-btn");
  const status = document.getElementById("prepare-status");
  btn.disabled = true;
  btn.textContent = "Packaging…";
  status.classList.remove("hidden");
  status.textContent = "Optimizing cover, building carousel ZIP, finalizing reel…";
  status.className = "text-sm text-muted text-center";

  const form = new FormData();
  form.append("category", selectedCategory.value);
  form.append("event_name", eventName.value.trim());
  if (state.reelJobId) form.append("reel_job_id", state.reelJobId);

  try {
    const res = await fetch("/api/studio/ideal-row/prepare-instagram", { method: "POST", body: form });
    const data = await res.json();

    if (res.status === 409) {
      status.textContent = `Reel still rendering (${data.progress}%) — please wait…`;
      btn.disabled = false;
      btn.textContent = "Prepare for Instagram";
      setTimeout(() => document.getElementById("prepare-instagram-btn").click(), 3000);
      return;
    }

    if (!res.ok) throw new Error(data.error || "Export failed");

    if (data.zip_job_id) {
      status.textContent = "Packaging in background…";
      await pollZipJob(data.zip_job_id, status, btn);
      return;
    }

    state.exportReady = true;
    showExportDownloads(data);
    status.textContent = "Instagram export package ready";
    status.className = "text-sm text-sullivan-green text-center";
  } catch (err) {
    status.textContent = err.message || "Export failed";
    status.className = "text-sm text-red-300 text-center";
    btn.disabled = false;
  } finally {
    btn.textContent = "Prepare for Instagram";
  }
});

async function pollZipJob(jobId, statusEl, btn) {
  for (let i = 0; i < 120; i++) {
    await new Promise((r) => setTimeout(r, 2000));
    const res = await fetch(`/api/studio/jobs/${jobId}`);
    if (!res.ok) continue;
    const job = await res.json();
    statusEl.textContent = job.message || "Packaging…";
    if (job.status === "completed" && job.meta?.export) {
      state.exportReady = true;
      showExportDownloads(job.meta.export);
      statusEl.textContent = "Instagram export package ready";
      statusEl.className = "text-sm text-sullivan-green text-center";
      btn.disabled = false;
      btn.textContent = "Prepare for Instagram";
      return;
    }
    if (job.status === "failed") {
      throw new Error(job.error || "Export failed");
    }
  }
  throw new Error("Export timed out");
}

function showExportDownloads(data) {
  const panel = document.getElementById("export-downloads");
  panel.classList.remove("hidden");

  document.getElementById("dl-post-1").href = data.post_1.bundle_url || data.post_1.url;
  document.getElementById("dl-post-1").download = data.post_1.bundle_filename || data.post_1.filename;
  document.getElementById("dl-post-2").href = data.post_2.bundle_url || data.post_2.url;
  document.getElementById("dl-post-2").download = data.post_2.bundle_filename || data.post_2.filename;
  document.getElementById("dl-post-3").href = data.post_3.bundle_url || data.post_3.url;
  document.getElementById("dl-post-3").download = data.post_3.bundle_filename || data.post_3.filename;

  ["post_1", "post_2", "post_3"].forEach((key, i) => {
    const post = data[key];
    const idx = i + 1;
    const preview = document.getElementById(`caption-preview-${idx}`);
    if (post.caption) {
      preview.innerHTML = post.caption.formatted_html;
      const sub = document.createElement("p");
      sub.className = "text-muted text-xs mt-2";
      sub.textContent = post.caption.body;
      preview.appendChild(sub);
    }
    const capDl = document.getElementById(`dl-caption-${idx}`);
    if (post.caption_download_url) {
      capDl.href = post.caption_download_url;
      capDl.download = "caption_instagram.txt";
    }
  });

  document.getElementById("export-path").textContent = data.export_base;
  panel.scrollIntoView({ behavior: "smooth" });
}

validate();
goToStep(1);

// Pre-fill from Vincent Studio redirect
const params = new URLSearchParams(window.location.search);
if (params.get("category")) {
  setActiveCategory(decodeURIComponent(params.get("category")));
}
if (params.get("event")) {
  eventName.value = decodeURIComponent(params.get("event"));
  validate();
}
