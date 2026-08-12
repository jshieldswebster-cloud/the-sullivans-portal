/** Tour Portfolio Presentation Mode — filters, grid, swipe slideshow, reel controls */

const state = {
  category: "",
  palette: "",
  portfolios: [],
  paletteLabels: {},
  active: null,
  detailIdx: 0,
  panel: "cover",
  touchStartX: 0,
  touchStartY: 0,
};

const lobby = document.getElementById("tour-lobby");
const stage = document.getElementById("tour-stage");
const grid = document.getElementById("tour-grid");
const gridCaption = document.getElementById("tour-grid-caption");

const stageCategory = document.getElementById("stage-category");
const stageEventName = document.getElementById("stage-event-name");
const coverImg = document.getElementById("stage-cover-img");
const detailImg = document.getElementById("stage-detail-img");
const detailCounter = document.getElementById("detail-counter");
const detailDots = document.getElementById("detail-dots");
const detailCountBadge = document.getElementById("detail-count-badge");
const reelVideo = document.getElementById("stage-reel-video");
const reelPlaceholder = document.getElementById("reel-placeholder");
const reelStills = document.getElementById("reel-stills");
const reelPlayBtn = document.getElementById("reel-play-btn");
const reelSpeed = document.getElementById("reel-speed");
const reelSpeedLabel = document.getElementById("reel-speed-label");

const PANELS = ["cover", "details", "reel"];

// ── Filters ───────────────────────────────────────────────────────────────────

function bindFilterGroup(containerId, dataKey, activeClass = "tour-chip-active") {
  document.querySelectorAll(`#${containerId} .tour-chip`).forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(`#${containerId} .tour-chip`).forEach((b) => b.classList.remove(activeClass));
      btn.classList.add(activeClass);
      state[dataKey] = btn.dataset[dataKey] || "";
      loadPortfolios();
    });
  });
}

bindFilterGroup("filter-category", "category");
bindFilterGroup("filter-palette", "palette");

// ── Load portfolios ─────────────────────────────────────────────────────────

async function loadPortfolios() {
  gridCaption.textContent = "Loading portfolios…";
  grid.innerHTML = "";

  const qs = new URLSearchParams();
  if (state.category) qs.set("category", state.category);
  if (state.palette) qs.set("palette", state.palette);

  const res = await fetch(`/api/studio/tour/portfolios?${qs}`);
  if (!res.ok) {
    gridCaption.textContent = "Could not load portfolios.";
    return;
  }

  const data = await res.json();
  state.portfolios = data.portfolios || [];
  (data.palettes || []).forEach((p) => {
    state.paletteLabels[p.id] = p.label;
  });

  gridCaption.textContent =
    state.portfolios.length === 0
      ? "No saved Ideal Row events match these filters yet."
      : `${state.portfolios.length} event${state.portfolios.length === 1 ? "" : "s"} ready for tour presentation`;

  grid.innerHTML = state.portfolios
    .map((p) => {
      const tags = (p.palettes || [])
        .map((id) => `<span class="tour-palette-tag">${state.paletteLabels[id] || id}</span>`)
        .join("");
      return `
        <button type="button" class="tour-card" data-id="${p.id}">
          <img class="tour-card-thumb" src="${p.cover.url}" alt="" loading="lazy" />
          <div class="tour-card-body">
            <p class="tour-card-title">${escapeHtml(p.event_name)}</p>
            <p class="tour-card-meta">${escapeHtml(p.category)} · ${p.detail_count} details</p>
            <div class="tour-palette-tags">${tags}</div>
          </div>
        </button>`;
    })
    .join("");

  grid.querySelectorAll(".tour-card").forEach((card) => {
    card.addEventListener("click", () => openPresentation(card.dataset.id));
  });
}

function escapeHtml(text) {
  const d = document.createElement("div");
  d.textContent = text;
  return d.innerHTML;
}

// ── Presentation ──────────────────────────────────────────────────────────────

function openPresentation(id) {
  const portfolio = state.portfolios.find((p) => p.id === id);
  if (!portfolio) return;

  state.active = portfolio;
  state.detailIdx = 0;
  state.panel = "cover";

  stageCategory.textContent = portfolio.category;
  stageEventName.textContent = portfolio.event_name;
  coverImg.src = portfolio.cover.url;
  detailCountBadge.textContent = `(${portfolio.detail_count})`;

  renderDetailSlide();
  setupReel(portfolio);

  lobby.classList.add("hidden");
  stage.classList.remove("hidden");
  stage.setAttribute("aria-hidden", "false");
  showPanel("cover");
}

function closePresentation() {
  if (reelVideo) {
    reelVideo.pause();
    reelVideo.removeAttribute("src");
  }
  stage.classList.add("hidden");
  stage.setAttribute("aria-hidden", "true");
  lobby.classList.remove("hidden");
  state.active = null;
}

document.getElementById("tour-back-btn").addEventListener("click", closePresentation);

document.getElementById("tour-fullscreen-btn").addEventListener("click", () => {
  const el = stage;
  if (!document.fullscreenElement) {
    el.requestFullscreen?.();
  } else {
    document.exitFullscreen?.();
  }
});

// ── Panels / tabs ───────────────────────────────────────────────────────────

function showPanel(name) {
  state.panel = name;
  PANELS.forEach((p) => {
    document.getElementById(`panel-${p}`).classList.toggle("tour-panel-active", p === name);
    document.querySelector(`.tour-tab[data-panel="${p}"]`)?.classList.toggle("tour-tab-active", p === name);
  });
}

document.querySelectorAll(".tour-tab").forEach((tab) => {
  tab.addEventListener("click", () => showPanel(tab.dataset.panel));
});

// ── Detail carousel ───────────────────────────────────────────────────────────

function renderDetailSlide() {
  const p = state.active;
  if (!p || !p.details.length) {
    detailImg.src = "";
    detailCounter.textContent = "0 / 0";
    detailDots.innerHTML = "";
    return;
  }

  const idx = Math.min(state.detailIdx, p.details.length - 1);
  state.detailIdx = idx;
  const slide = p.details[idx];
  detailImg.src = slide.url;
  detailCounter.textContent = `${idx + 1} / ${p.details.length}`;

  detailDots.innerHTML = p.details
    .map(
      (_, i) =>
        `<button type="button" class="tour-dot ${i === idx ? "tour-dot-active" : ""}" data-idx="${i}" aria-label="Photo ${i + 1}"></button>`
    )
    .join("");

  detailDots.querySelectorAll(".tour-dot").forEach((dot) => {
    dot.addEventListener("click", () => {
      state.detailIdx = parseInt(dot.dataset.idx, 10);
      renderDetailSlide();
    });
  });
}

document.getElementById("detail-prev").addEventListener("click", () => {
  if (!state.active?.details.length) return;
  state.detailIdx = (state.detailIdx - 1 + state.active.details.length) % state.active.details.length;
  renderDetailSlide();
});

document.getElementById("detail-next").addEventListener("click", () => {
  if (!state.active?.details.length) return;
  state.detailIdx = (state.detailIdx + 1) % state.active.details.length;
  renderDetailSlide();
});

// ── Reel playback ───────────────────────────────────────────────────────────

function setupReel(portfolio) {
  reelVideo.classList.add("hidden");
  reelPlaceholder.classList.add("hidden");
  reelStills.innerHTML = "";
  reelPlayBtn.textContent = "▶";

  if (portfolio.reel?.url) {
    reelVideo.src = portfolio.reel.url;
    reelVideo.load();
    reelVideo.classList.remove("hidden");
    reelVideo.playbackRate = parseFloat(reelSpeed.value);
  } else if (portfolio.reel_stills?.length) {
    reelPlaceholder.classList.remove("hidden");
    reelStills.innerHTML = portfolio.reel_stills
      .map((s) => `<img src="${s.url}" alt="" />`)
      .join("");
  } else {
    reelPlaceholder.classList.remove("hidden");
    const msg = reelPlaceholder.querySelector(".tour-reel-msg");
    if (msg) msg.textContent = "No reel assets yet for this event";
  }
}

reelPlayBtn.addEventListener("click", () => {
  if (!reelVideo.src) return;
  if (reelVideo.paused) {
    reelVideo.play();
    reelPlayBtn.textContent = "❚❚";
  } else {
    reelVideo.pause();
    reelPlayBtn.textContent = "▶";
  }
});

reelVideo.addEventListener("ended", () => {
  reelPlayBtn.textContent = "▶";
});

reelSpeed.addEventListener("input", () => {
  const rate = parseFloat(reelSpeed.value);
  reelVideo.playbackRate = rate;
  reelSpeedLabel.textContent = `${rate.toFixed(2).replace(/\.00$/, "")}×`;
});

// ── Touch swipe ─────────────────────────────────────────────────────────────

function bindSwipe(zoneEl, onSwipeLeft, onSwipeRight) {
  zoneEl.addEventListener(
    "touchstart",
    (e) => {
      state.touchStartX = e.changedTouches[0].screenX;
      state.touchStartY = e.changedTouches[0].screenY;
    },
    { passive: true }
  );

  zoneEl.addEventListener(
    "touchend",
    (e) => {
      const dx = e.changedTouches[0].screenX - state.touchStartX;
      const dy = e.changedTouches[0].screenY - state.touchStartY;
      if (Math.abs(dx) < 50 || Math.abs(dx) < Math.abs(dy)) return;
      if (dx < 0) onSwipeLeft();
      else onSwipeRight();
    },
    { passive: true }
  );
}

bindSwipe(document.getElementById("cover-swipe"), () => showPanel("details"), () => {});

bindSwipe(
  document.getElementById("details-swipe"),
  () => {
    if (state.detailIdx < (state.active?.details.length || 1) - 1) {
      state.detailIdx += 1;
      renderDetailSlide();
    } else {
      showPanel("reel");
    }
  },
  () => {
    if (state.detailIdx > 0) {
      state.detailIdx -= 1;
      renderDetailSlide();
    } else {
      showPanel("cover");
    }
  }
);

bindSwipe(
  document.getElementById("reel-swipe"),
  () => {},
  () => showPanel("details")
);

// Keyboard for smart screens
document.addEventListener("keydown", (e) => {
  if (!state.active || stage.classList.contains("hidden")) return;
  if (e.key === "ArrowRight") {
    if (state.panel === "cover") showPanel("details");
    else if (state.panel === "details") document.getElementById("detail-next").click();
  } else if (e.key === "ArrowLeft") {
    if (state.panel === "reel") showPanel("details");
    else if (state.panel === "details") document.getElementById("detail-prev").click();
    else if (state.panel === "cover") closePresentation();
  } else if (e.key === " ") {
    e.preventDefault();
    if (state.panel === "reel") reelPlayBtn.click();
  } else if (e.key === "Escape") {
    closePresentation();
  }
});

loadPortfolios();
