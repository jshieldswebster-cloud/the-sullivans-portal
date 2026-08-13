/** Review for Posting + Drive backlog queues */

const driveReview = {
  postingProjects: [],
  backlogProjects: [],
  selectedId: null,
  selectedQueue: null,
  syncJobId: null,
  backlogJobId: null,
  syncPoll: null,
  backlogPoll: null,
  audioTracks: [],
  defaultTrackId: null,
  backlogStatus: null,
};

async function driveReviewFetch(path, options = {}) {
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

function driveReviewEl(id) {
  return document.getElementById(id);
}

function escapeHtml(text) {
  const d = document.createElement("div");
  d.textContent = text || "";
  return d.innerHTML;
}

function driveReviewSetStatus(msg, target = "posting") {
  const id = target === "backlog" ? "drive-sync-status" : "posting-queue-status";
  const el = driveReviewEl(id);
  if (el) el.textContent = msg || "";
}

function postingThumb(project) {
  const local = project.local_previews || {};
  if (local.cover_url) return local.cover_url;
  if (local.carousel && local.carousel[0]) return local.carousel[0];
  return (
    project.previews?.cover?.thumbnail_link ||
    project.previews?.carousel?.[0]?.thumbnail_link ||
    ""
  );
}

function renderProjectCards(projects, gridId, emptyId, queue) {
  const grid = driveReviewEl(gridId);
  const empty = driveReviewEl(emptyId);
  if (!grid) return;

  if (!projects.length) {
    grid.innerHTML = "";
    empty?.classList.remove("hidden");
    return;
  }
  empty?.classList.add("hidden");

  grid.innerHTML = projects
    .map((p) => {
      const thumb = postingThumb(p);
      const selected = p.id === driveReview.selectedId && driveReview.selectedQueue === queue;
      const badge = queue === "posting"
        ? `<span class="drive-review-badge ${p.reel_job_id ? "rendering" : ""}">${p.reel_job_id ? "Reel rendering" : "Ready to review"}</span>`
        : `<span class="drive-review-badge">${p.package_complete ? "Complete" : "Incomplete"}</span>`;
      return `
        <article class="drive-review-card ${selected ? "selected" : ""}" data-project-id="${p.id}" data-queue="${queue}">
          <div class="drive-review-thumb">
            ${
              thumb
                ? `<img src="${thumb}" alt="" />`
                : `<span class="drive-review-thumb-placeholder">${queue === "posting" ? "Staged" : "Pending"}</span>`
            }
          </div>
          <div class="drive-review-body">
            <h3>${escapeHtml(p.event_name)}</h3>
            <p class="drive-review-meta">${escapeHtml(p.category)}${p.batch_date ? ` · ${p.batch_date}` : ""}</p>
            ${badge}
          </div>
        </article>`;
    })
    .join("");

  grid.querySelectorAll(".drive-review-card").forEach((card) => {
    card.addEventListener("click", () =>
      driveReviewSelect(card.dataset.projectId, card.dataset.queue)
    );
  });
}

function driveReviewUrlProjectId() {
  return (new URLSearchParams(window.location.search).get("project") || "").trim();
}

function driveReviewFindQueued(projectId) {
  if (!projectId) return null;
  const posting = driveReview.postingProjects.find((p) => p.id === projectId);
  if (posting) return { project: posting, queue: "posting" };
  const backlog = driveReview.backlogProjects.find((p) => p.id === projectId);
  if (backlog) return { project: backlog, queue: "backlog" };
  return null;
}

function driveReviewFirstAvailable() {
  if (driveReview.postingProjects[0]) {
    return { project: driveReview.postingProjects[0], queue: "posting" };
  }
  if (driveReview.backlogProjects[0]) {
    return { project: driveReview.backlogProjects[0], queue: "backlog" };
  }
  return null;
}

async function driveReviewEnsureSelection(preferredId = null) {
  const wanted =
    preferredId ||
    driveReview.selectedId ||
    driveReviewUrlProjectId() ||
    driveReview.backlogStatus?.active_project_id ||
    "";
  const match = driveReviewFindQueued(wanted) || driveReviewFirstAvailable();
  if (!match) {
    driveReviewHideDetail();
    return;
  }
  if (driveReview.selectedId === match.project.id && driveReview.selectedQueue === match.queue) {
    return;
  }
  await driveReviewSelect(match.project.id, match.queue, true);
}

async function driveReviewRefreshQueues() {
  await Promise.all([driveReviewLoadBacklogStatus(), driveReviewLoadBacklogQueue()]);
  await driveReviewEnsureSelection();
}

async function driveReviewLoadBacklogStatus() {
  try {
    const data = await driveReviewFetch("/backlog/status");
    driveReview.backlogStatus = data;
    const hint = driveReviewEl("backlog-quota-hint");
    if (hint) {
      const rem = data.remaining_today ?? data.posts_per_day ?? 3;
      const total = data.posts_per_day ?? 3;
      hint.textContent = `${data.review_queue_count || 0} in queue · ${rem} of ${total} daily batch slots remaining today`;
    }
    driveReview.postingProjects = data.review_queue || [];
    renderProjectCards(
      driveReview.postingProjects,
      "posting-queue-grid",
      "posting-queue-empty",
      "posting"
    );
  } catch (err) {
    driveReviewSetStatus(err.message);
  }
}

async function driveReviewLoadBacklogQueue() {
  try {
    const data = await driveReviewFetch("/drive/projects?status=pending_review");
    driveReview.backlogProjects = data.projects || [];
    renderProjectCards(
      driveReview.backlogProjects,
      "drive-review-grid",
      "drive-review-empty",
      "backlog"
    );
  } catch (err) {
    driveReviewSetStatus(err.message, "backlog");
  }
}

async function driveReviewLoadAudio() {
  try {
    const res = await fetch("/api/studio/audio/library", { credentials: "same-origin" });
    const data = await res.json();
    driveReview.audioTracks = data.tracks || [];
    driveReview.defaultTrackId = data.default_track_id;
    ["posting-audio-select", "drive-review-audio-select"].forEach((id) => {
      const select = driveReviewEl(id);
      if (!select) return;
      select.innerHTML = driveReview.audioTracks
        .map(
          (t) =>
            `<option value="${t.id}" ${t.id === data.default_track_id ? "selected" : ""}>${escapeHtml(t.label || t.name || t.id)}</option>`
        )
        .join("");
    });
  } catch {
    /* optional */
  }
}

function driveReviewHideDetail() {
  driveReview.selectedId = null;
  driveReview.selectedQueue = null;
  driveReviewEl("posting-queue-detail")?.classList.add("hidden");
  driveReviewEl("drive-review-detail")?.classList.add("hidden");
  document.querySelectorAll(".drive-review-card").forEach((c) => c.classList.remove("selected"));
}

async function driveReviewSelect(projectId, queue, silent = false) {
  if (!projectId || projectId === "undefined" || projectId === "null") {
    await driveReviewEnsureSelection();
    return;
  }
  driveReview.selectedId = projectId;
  driveReview.selectedQueue = queue;
  document.querySelectorAll(".drive-review-card").forEach((c) => {
    c.classList.toggle("selected", c.dataset.projectId === projectId && c.dataset.queue === queue);
  });

  try {
    const project = await driveReviewFetch(`/drive/projects/${projectId}`);
    if (queue === "posting") {
      driveReviewShowPostingDetail(project);
    } else {
      driveReviewShowBacklogDetail(project);
    }
    if (typeof applyDriveProjectToEditor === "function") {
      await applyDriveProjectToEditor(project);
    } else {
      if (typeof setActiveCategory === "function" && project.category) {
        setActiveCategory(project.category);
      }
      const eventInput = driveReviewEl("event-name");
      if (eventInput && project.event_name) {
        eventInput.value = project.event_name;
        if (typeof validate === "function") validate();
      }
    }
  } catch (err) {
    const missing = /not found/i.test(err.message || "");
    if (missing) {
      driveReview.selectedId = null;
      const fallback = driveReviewFirstAvailable();
      if (fallback && fallback.project.id !== projectId) {
        await driveReviewSelect(fallback.project.id, fallback.queue, true);
        return;
      }
      driveReviewHideDetail();
      return;
    }
    if (!silent) driveReviewSetStatus(err.message, queue === "backlog" ? "backlog" : "posting");
  }
}

function driveReviewShowPostingDetail(project) {
  const detail = driveReviewEl("posting-queue-detail");
  detail?.classList.remove("hidden");
  driveReviewEl("drive-review-detail")?.classList.add("hidden");

  driveReviewEl("posting-detail-title").textContent = project.event_name;
  driveReviewEl("posting-detail-category").textContent = project.category;
  driveReviewEl("posting-detail-batch").textContent = project.batch_date
    ? `Batch ${project.batch_date} · Staged at ${project.staging_path || "Review for Posting"}`
    : "";

  const local = project.local_previews || {};
  const urls = [
    local.cover_url,
    ...(local.carousel || []).slice(0, 6),
    project.local_paths?.reel_output_url,
  ].filter(Boolean);
  const row = driveReviewEl("posting-preview-row");
  if (row) {
    row.innerHTML = urls.map((u) => `<img src="${u}" alt="" />`).join("");
  }

  const reelStatus = driveReviewEl("posting-reel-status");
  if (reelStatus) {
    if (project.reel_job_id) {
      reelStatus.textContent = "Branded reel is rendering — approve once preview looks good, or re-render after changing audio/watermark.";
      pollPostingReelJob(project.reel_job_id);
    } else if (project.local_paths?.reel_output_url) {
      reelStatus.innerHTML = `Reel ready · <a href="${project.local_paths.reel_output_url}" class="text-sullivan-green hover:underline" target="_blank" rel="noopener">Preview MP4</a>`;
    } else {
      reelStatus.textContent = "";
    }
  }

  const approveBtn = driveReviewEl("posting-approve-btn");
  const rerenderBtn = driveReviewEl("posting-rerender-btn");
  if (approveBtn) approveBtn.dataset.projectId = project.id;
  if (rerenderBtn) rerenderBtn.dataset.projectId = project.id;

  const select = driveReviewEl("posting-audio-select");
  if (select) select.value = project.audio_track_id || driveReview.defaultTrackId || "";
}

function driveReviewShowBacklogDetail(project) {
  const detail = driveReviewEl("drive-review-detail");
  detail?.classList.remove("hidden");
  driveReviewEl("posting-queue-detail")?.classList.add("hidden");

  driveReviewEl("drive-review-detail-title").textContent = project.event_name;
  driveReviewEl("drive-review-detail-category").textContent = project.category;
  driveReviewEl("drive-review-slot-cover").textContent = project.cover_drive_id ? "1" : "0";
  driveReviewEl("drive-review-slot-carousel").textContent = String(
    (project.carousel_drive_ids || []).length
  );
  driveReviewEl("drive-review-slot-reel").textContent = String(
    (project.reel_drive_ids || []).length
  );

  const previews = [
    project.previews?.cover,
    ...(project.previews?.carousel || []),
    project.previews?.reel_sample,
  ].filter(Boolean);
  const row = driveReviewEl("drive-review-preview-row");
  if (row) {
    row.innerHTML = previews
      .map(
        (p) =>
          `<img src="${p.thumbnail_link || ""}" alt="${escapeHtml(p.name)}" referrerpolicy="no-referrer" />`
      )
      .join("");
  }

  const approveBtn = driveReviewEl("drive-approve-btn");
  if (approveBtn) {
    approveBtn.disabled = false;
    approveBtn.dataset.projectId = project.id;
    approveBtn.dataset.complete = project.package_complete ? "1" : "0";
    approveBtn.title = project.package_complete
      ? "Build the Ideal Row and publish"
      : "Folder is missing cover, 8 carousel photos, or reel media";
  }
}

let postingReelPoll = null;
function pollPostingReelJob(jobId) {
  if (postingReelPoll) clearInterval(postingReelPoll);
  postingReelPoll = setInterval(async () => {
    try {
      const job = await driveReviewFetch(`/jobs/${jobId}`);
      const el = driveReviewEl("posting-reel-status");
      if (!el) return;
      if (job.status === "completed" && job.output_url) {
        clearInterval(postingReelPoll);
        el.innerHTML = `Reel ready · <a href="${job.output_url}" class="text-sullivan-green hover:underline" target="_blank" rel="noopener">Preview MP4</a>`;
      } else if (job.status === "failed") {
        clearInterval(postingReelPoll);
        el.textContent = job.error || "Reel render failed";
      } else {
        el.textContent = `${job.message || "Rendering"} (${job.progress || 0}%)`;
      }
    } catch {
      /* ignore poll errors */
    }
  }, 2000);
}

async function driveReviewStartSync() {
  const btn = driveReviewEl("drive-sync-btn");
  if (btn) btn.disabled = true;
  driveReviewSetStatus("Queuing Drive sync…", "backlog");
  try {
    const data = await driveReviewFetch("/drive/sync", { method: "POST", body: "{}" });
    if (!data.job_id) throw new Error(data.error || "Drive sync did not start");
    driveReview.syncJobId = data.job_id;
    driveReviewPollJob("sync");
  } catch (err) {
    driveReviewSetStatus(err.message, "backlog");
    if (btn) btn.disabled = false;
  }
}

async function driveReviewStartBacklogBatch() {
  const btn = driveReviewEl("backlog-run-btn");
  if (btn) btn.disabled = true;
  driveReviewSetStatus("Running daily batch…");
  try {
    const data = await driveReviewFetch("/backlog/run-daily", {
      method: "POST",
      body: JSON.stringify({ force: true }),
    });
    if (!data.job_id) {
      throw new Error(data.error || "Daily batch did not start");
    }
    driveReview.backlogJobId = data.job_id;
    driveReviewPollJob("backlog");
  } catch (err) {
    driveReviewSetStatus(err.message);
    if (btn) btn.disabled = false;
  }
}

function driveReviewPollJob(kind) {
  const pollKey = kind === "sync" ? "syncPoll" : "backlogPoll";
  const jobIdKey = kind === "sync" ? "syncJobId" : "backlogJobId";
  const btnId = kind === "sync" ? "drive-sync-btn" : "backlog-run-btn";
  const statusTarget = kind === "sync" ? "backlog" : "posting";

  if (driveReview[pollKey]) clearInterval(driveReview[pollKey]);
  driveReview[pollKey] = setInterval(async () => {
    const jobId = driveReview[jobIdKey];
    if (!jobId) return;
    try {
      const job = await driveReviewFetch(`/jobs/${jobId}`);
      const summary = job.meta && job.meta.summary;
      const summaryMsg =
        (summary && (summary.message || summary.reason)) || job.message || job.status;
      driveReviewSetStatus(summaryMsg, statusTarget);
      if (job.status === "completed") {
        clearInterval(driveReview[pollKey]);
        driveReview[pollKey] = null;
        driveReview[jobIdKey] = null;
        const doneBtn = driveReviewEl(btnId);
        if (doneBtn) doneBtn.disabled = false;
        await driveReviewRefreshQueues();
        driveReviewSetStatus(
          summaryMsg || (kind === "backlog" ? "Daily batch complete" : "Drive sync complete"),
          statusTarget
        );
      } else if (job.status === "failed") {
        clearInterval(driveReview[pollKey]);
        driveReview[pollKey] = null;
        driveReview[jobIdKey] = null;
        const failBtn = driveReviewEl(btnId);
        if (failBtn) failBtn.disabled = false;
        driveReviewSetStatus(job.error || "Job failed", statusTarget);
      }
    } catch (err) {
      driveReviewSetStatus(err.message, statusTarget);
    }
  }, 1500);
}

async function driveReviewApprovePosting(rerender = false) {
  const btn = rerender ? driveReviewEl("posting-rerender-btn") : driveReviewEl("posting-approve-btn");
  const projectId = btn?.dataset.projectId || driveReview.selectedId;
  if (!projectId) {
    driveReviewSetStatus("Select a Review for Posting package first");
    return;
  }

  const trackId = driveReviewEl("posting-audio-select")?.value || null;
  if (btn) btn.disabled = true;
  driveReviewSetStatus(rerender ? "Re-rendering reel…" : "Approving and promoting to production…");

  try {
    if (trackId) {
      await driveReviewFetch(`/drive/projects/${projectId}`, {
        method: "PATCH",
        body: JSON.stringify({ audio_track_id: trackId }),
      });
    }
    const result = await driveReviewFetch(`/drive/projects/${projectId}/approve`, {
      method: "POST",
      body: JSON.stringify({ audio_track_id: trackId, rerender_reel: rerender }),
    });

    if (rerender) {
      driveReviewSetStatus("Re-render queued");
      if (result.reel_job_id) pollPostingReelJob(result.reel_job_id);
      if (btn) btn.disabled = false;
      return;
    }

    driveReviewSetStatus("Approved — cleared from backlog");
    driveReviewHideDetail();
    await driveReviewRefreshQueues();

    if (typeof goToStep === "function" && result.ideal_row) {
      state.savedData = result.ideal_row;
      state.reelJobId = result.reel_job_id || null;
      state.reelReady = !state.reelJobId;
      if (typeof setActiveCategory === "function") {
        setActiveCategory(result.ideal_row.category || result.category);
      }
      const eventInput = driveReviewEl("event-name");
      if (eventInput) eventInput.value = result.ideal_row.event_name || result.event_name;
      goToStep(4);
      if (state.reelJobId && typeof pollReelJob === "function") pollReelJob(state.reelJobId);
    }
  } catch (err) {
    driveReviewSetStatus(err.message);
    if (btn) btn.disabled = false;
  }
}

async function driveReviewApproveBacklog() {
  const btn = driveReviewEl("drive-approve-btn");
  const projectId = btn?.dataset.projectId || driveReview.selectedId;
  if (!projectId) {
    driveReviewSetStatus("Select a Drive backlog project first", "backlog");
    return;
  }
  if (btn?.dataset.complete === "0") {
    driveReviewSetStatus(
      "This folder is missing cover, 8 carousel photos, or reel media — cannot publish yet",
      "backlog"
    );
    return;
  }

  const trackId = driveReviewEl("drive-review-audio-select")?.value || null;
  if (btn) btn.disabled = true;
  driveReviewSetStatus("Building Ideal Row…", "backlog");

  try {
    if (trackId) {
      await driveReviewFetch(`/drive/projects/${projectId}`, {
        method: "PATCH",
        body: JSON.stringify({ audio_track_id: trackId }),
      });
    }
    const result = await driveReviewFetch(`/drive/projects/${projectId}/approve`, {
      method: "POST",
      body: JSON.stringify({ audio_track_id: trackId }),
    });

    driveReviewSetStatus("Package approved", "backlog");
    driveReviewHideDetail();
    await driveReviewRefreshQueues();

    if (typeof goToStep === "function" && result.ideal_row) {
      state.savedData = result.ideal_row;
      state.reelJobId = result.reel_job_id || null;
      goToStep(4);
      if (state.reelJobId && typeof pollReelJob === "function") pollReelJob(state.reelJobId);
    }
  } catch (err) {
    driveReviewSetStatus(err.message, "backlog");
    if (btn) btn.disabled = false;
  }
}

let driveReviewBound = false;

function driveReviewInit() {
  if (driveReviewBound) return;
  driveReviewBound = true;
  driveReviewEl("drive-sync-btn")?.addEventListener("click", driveReviewStartSync);
  driveReviewEl("backlog-run-btn")?.addEventListener("click", (event) => {
    event.preventDefault();
    driveReviewStartBacklogBatch();
  });
  driveReviewEl("posting-approve-btn")?.addEventListener("click", (event) => {
    event.preventDefault();
    driveReviewApprovePosting(false);
  });
  driveReviewEl("posting-rerender-btn")?.addEventListener("click", (event) => {
    event.preventDefault();
    driveReviewApprovePosting(true);
  });
  driveReviewEl("drive-approve-btn")?.addEventListener("click", (event) => {
    event.preventDefault();
    driveReviewApproveBacklog();
  });
  driveReviewLoadAudio();
  driveReviewRefreshQueues().then(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get("drive") === "connected") {
      driveReviewStartSync();
      driveReviewStartBacklogBatch();
    }
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", driveReviewInit);
} else {
  driveReviewInit();
}
