/** AI Audio & Vibe Matcher dashboard */

(function () {
  "use strict";

  const grid = document.getElementById("vibe-grid");
  const selectedLabel = document.getElementById("selected-track-label");
  const statsEl = document.getElementById("library-stats");
  const player = document.getElementById("preview-player");

  let library = null;
  let selectedId = null;
  let playingId = null;

  function formatDuration(sec) {
    if (!sec) return "—";
    const m = Math.floor(sec / 60);
    const s = Math.round(sec % 60);
    return `${m}:${String(s).padStart(2, "0")}`;
  }

  function findTrack(id) {
    if (!library) return null;
    for (const vibe of library.vibes) {
      for (const t of vibe.tracks) {
        if (t.id === id) return { ...t, vibe_label: vibe.label };
      }
    }
    return null;
  }

  function updateHero() {
    const track = findTrack(selectedId);
    if (track) {
      selectedLabel.textContent = `${track.title} · ${track.vibe_label}`;
    } else {
      selectedLabel.textContent = "No track selected";
    }
    if (library) {
      statsEl.textContent = `${library.available_count} of ${library.track_count} tracks ready · auto trim/loop on reel export`;
    }
  }

  async function selectTrack(trackId) {
    const res = await fetch("/api/studio/audio/select", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ track_id: trackId }),
    });
    if (!res.ok) return;
    selectedId = trackId;
    document.querySelectorAll(".audio-track-row").forEach((row) => {
      row.classList.toggle("audio-track-row-selected", row.dataset.trackId === trackId);
    });
    document.querySelectorAll(".audio-select-btn").forEach((btn) => {
      const active = btn.dataset.trackId === trackId;
      btn.classList.toggle("audio-select-btn-active", active);
      btn.textContent = active ? "Active" : "Use";
    });
    updateHero();
  }

  function togglePreview(track) {
    if (!track.url) return;
    if (playingId === track.id && !player.paused) {
      player.pause();
      playingId = null;
      renderPlayIcons();
      return;
    }
    player.src = track.url;
    player.play();
    playingId = track.id;
    renderPlayIcons();
  }

  function renderPlayIcons() {
    document.querySelectorAll(".audio-play-btn").forEach((btn) => {
      const active = btn.dataset.trackId === playingId && !player.paused;
      btn.textContent = active ? "❚❚" : "▶";
    });
  }

  player.addEventListener("ended", () => {
    playingId = null;
    renderPlayIcons();
  });

  function renderVibes() {
    if (!library?.vibes?.length) {
      grid.innerHTML = '<p class="text-muted text-sm">No vibes configured.</p>';
      return;
    }

    grid.innerHTML = library.vibes
      .map(
        (vibe) => `
      <article class="audio-vibe-card card-surface shadow-luxe">
        <div class="audio-vibe-header">
          <h2 class="font-display text-xl text-sullivan-ink">${vibe.label}</h2>
          <p class="text-xs text-muted mt-1">${vibe.description || ""}</p>
        </div>
        <div class="audio-track-list">
          ${vibe.tracks
            .map(
              (track) => `
            <div class="audio-track-row ${track.id === selectedId ? "audio-track-row-selected" : ""}" data-track-id="${track.id}">
              <button type="button" class="audio-icon-btn audio-play-btn" data-track-id="${track.id}" title="Preview">▶</button>
              <div class="audio-track-meta">
                <p class="audio-track-title">${track.title}</p>
                <p class="audio-track-duration">${formatDuration(track.duration_sec)} · ${track.available ? "Ready" : "Pending"}</p>
              </div>
              <button type="button" class="audio-select-btn ${track.id === selectedId ? "audio-select-btn-active" : ""}" data-track-id="${track.id}">
                ${track.id === selectedId ? "Active" : "Use"}
              </button>
            </div>`
            )
            .join("")}
        </div>
      </article>`
      )
      .join("");

    grid.querySelectorAll(".audio-play-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const track = findTrack(btn.dataset.trackId);
        if (track) togglePreview(track);
      });
    });

    grid.querySelectorAll(".audio-select-btn").forEach((btn) => {
      btn.addEventListener("click", () => selectTrack(btn.dataset.trackId));
    });
  }

  async function init() {
    const [libRes, selRes] = await Promise.all([
      fetch("/api/studio/audio/library"),
      fetch("/api/studio/audio/selected"),
    ]);
    library = await libRes.json();
    const sel = await selRes.json();
    selectedId = sel.selected_track_id || library.default_track_id;
    updateHero();
    renderVibes();
  }

  init();
})();
