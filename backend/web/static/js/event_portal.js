/** Mobile Event Portal — backlog status, daily batch, drive sync */

async function portalFetch(path, options = {}) {
  const { headers: extraHeaders, ...rest } = options;
  const res = await fetch(`/api/studio${path}`, {
    credentials: "same-origin",
    ...rest,
    headers: { "Content-Type": "application/json", ...(extraHeaders || {}) },
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText || `Request failed (${res.status})`);
  return data;
}

function setPortalStatus(msg) {
  const el = document.getElementById("portal-status");
  if (el) el.textContent = msg || "";
}

function renderPostingQueue(projects) {
  const list = document.getElementById("posting-queue-list");
  const empty = document.getElementById("posting-queue-empty");
  if (!list) return;

  if (!projects || !projects.length) {
    list.innerHTML = "";
    empty?.classList.remove("hidden");
    return;
  }
  empty?.classList.add("hidden");
  list.innerHTML = projects
    .map(
      (p) => `
      <li>
        <a class="queue-item" href="/new-post?project=${encodeURIComponent(p.id)}">
          <strong>${escapeHtml(p.event_name)}</strong>
          <span>${escapeHtml(p.category)}${p.batch_date ? ` · ${p.batch_date}` : ""}</span>
        </a>
      </li>`
    )
    .join("");
}

function escapeHtml(text) {
  const d = document.createElement("div");
  d.textContent = text || "";
  return d.innerHTML;
}

async function loadPortalState() {
  try {
    const data = await portalFetch("/backlog/status");
    const rem = data.remaining_today ?? 0;
    const total = data.posts_per_day ?? 3;
    const qCount = data.review_queue_count ?? 0;

    const postingMeta = document.getElementById("posting-meta");
    if (postingMeta) {
      postingMeta.textContent = `${qCount} in queue · ${rem} of ${total} daily batch slots remaining today`;
    }

    renderPostingQueue(data.review_queue || []);

    const backlogData = await portalFetch("/drive/projects?status=pending_review");
    const backlogCount = backlogData.count ?? 0;
    const backlogMeta = document.getElementById("backlog-meta");
    if (backlogMeta) {
      backlogMeta.textContent =
        backlogCount > 0
          ? `${backlogCount} unprocessed folder${backlogCount === 1 ? "" : "s"} in Drive backlog`
          : "No unprocessed folders in backlog";
    }

    const backlogEmpty = document.getElementById("backlog-empty");
    if (backlogEmpty) {
      backlogEmpty.classList.toggle("hidden", backlogCount > 0);
    }
  } catch (err) {
    setPortalStatus(err.message);
  }
}

let jobPoll = null;

function pollJob(jobId, label) {
  if (jobPoll) clearInterval(jobPoll);
  const btnBatch = document.getElementById("btn-daily-batch");
  const btnSync = document.getElementById("btn-drive-sync");
  jobPoll = setInterval(async () => {
    try {
      const job = await portalFetch(`/jobs/${jobId}`);
      setPortalStatus(`${label}: ${job.message || job.status}`);
      if (job.status === "completed") {
        clearInterval(jobPoll);
        jobPoll = null;
        if (btnBatch) btnBatch.disabled = false;
        if (btnSync) btnSync.disabled = false;
        await loadPortalState();
        setPortalStatus(label + " complete");
      } else if (job.status === "failed") {
        clearInterval(jobPoll);
        jobPoll = null;
        if (btnBatch) btnBatch.disabled = false;
        if (btnSync) btnSync.disabled = false;
        setPortalStatus(job.error || label + " failed");
      }
    } catch (err) {
      setPortalStatus(err.message);
    }
  }, 1500);
}

async function runDailyBatch() {
  const btn = document.getElementById("btn-daily-batch");
  if (btn) btn.disabled = true;
  setPortalStatus("Starting daily batch…");
  try {
    const data = await portalFetch("/backlog/run-daily", {
      method: "POST",
      body: JSON.stringify({ force: true }),
    });
    if (!data.job_id) throw new Error(data.error || "Daily batch did not start");
    pollJob(data.job_id, "Daily batch");
  } catch (err) {
    setPortalStatus(err.message);
    if (btn) btn.disabled = false;
  }
}

async function syncFromDrive() {
  const btn = document.getElementById("btn-drive-sync");
  if (btn) btn.disabled = true;
  setPortalStatus("Syncing from Drive…");
  try {
    const data = await portalFetch("/drive/sync", { method: "POST", body: "{}" });
    pollJob(data.job_id, "Drive sync");
  } catch (err) {
    setPortalStatus(err.message);
    if (btn) btn.disabled = false;
  }
}

document.getElementById("btn-daily-batch")?.addEventListener("click", (event) => {
  event.preventDefault();
  runDailyBatch();
});
document.getElementById("btn-drive-sync")?.addEventListener("click", (event) => {
  event.preventDefault();
  syncFromDrive();
});

function toggleMenu() {
  const menu = document.getElementById("dropdownMenu");
  const toggle = document.getElementById("menu-toggle");
  if (!menu) return;
  const open = menu.classList.toggle("active");
  if (toggle) toggle.setAttribute("aria-expanded", open ? "true" : "false");
}

document.getElementById("menu-toggle")?.addEventListener("click", (e) => {
  e.stopPropagation();
  toggleMenu();
});

window.addEventListener("click", (event) => {
  const menu = document.getElementById("dropdownMenu");
  const toggle = document.getElementById("menu-toggle");
  if (!menu || !menu.classList.contains("active")) return;
  if (toggle && (event.target === toggle || toggle.contains(event.target))) return;
  menu.classList.remove("active");
  toggle?.setAttribute("aria-expanded", "false");
});

loadPortalState();
