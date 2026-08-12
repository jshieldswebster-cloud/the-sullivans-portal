/** Secure Client Gallery — view-only protections + 3-post presentation */

(function () {
  "use strict";

  // ── View-only security layers (best-effort deterrent) ───────────────────────
  function initProtections() {
    if (!window.__CLIENT_GALLERY_ACTIVE__) return;

    const block = (e) => {
      const t = e.target;
      if (t.closest(".client-protected") || t.closest(".media-shield")) {
        e.preventDefault();
        return false;
      }
    };

    document.addEventListener("contextmenu", block);
    document.addEventListener("dragstart", block);

    document.addEventListener(
      "copy",
      (e) => {
        if (document.getElementById("client-view")) e.preventDefault();
      },
      true
    );

    document.addEventListener(
      "cut",
      (e) => {
        if (document.getElementById("client-view")) e.preventDefault();
      },
      true
    );

    // Deter common save shortcuts while gallery is open
    document.addEventListener("keydown", (e) => {
      if (!document.getElementById("client-view")) return;
      const mod = e.metaKey || e.ctrlKey;
      if (mod && ["s", "p", "c", "x"].includes(e.key.toLowerCase())) {
        e.preventDefault();
      }
    });

    // Disable long-press callout on iOS for media shields
    document.querySelectorAll(".media-shield").forEach((el) => {
      el.style.webkitTouchCallout = "none";
    });
  }

  initProtections();

  if (!window.__CLIENT_GALLERY_ACTIVE__) return;

  const state = { portfolio: null, detailIdx: 0, panel: "cover", touchX: 0, touchY: 0 };
  const PANELS = ["cover", "details", "reel"];

  const imgCover = document.getElementById("img-cover");
  const imgDetail = document.getElementById("img-detail");
  const detailLabel = document.getElementById("detail-label");
  const videoReel = document.getElementById("video-reel");
  const reelFallback = document.getElementById("reel-fallback");
  const btnPlay = document.getElementById("btn-play");
  const reelSpeed = document.getElementById("reel-speed");
  const speedReadout = document.getElementById("speed-readout");

  function showPanel(name) {
    state.panel = name;
    PANELS.forEach((p) => {
      document.getElementById(`panel-${p}`).classList.toggle("client-panel-active", p === name);
      document.querySelector(`.client-tab[data-panel="${p}"]`)?.classList.toggle("client-tab-active", p === name);
    });
    if (name !== "reel" && videoReel) videoReel.pause();
  }

  document.querySelectorAll(".client-tab").forEach((tab) => {
    tab.addEventListener("click", () => showPanel(tab.dataset.panel));
  });

  function renderDetail() {
    const details = state.portfolio?.details || [];
    if (!details.length) {
      detailLabel.textContent = "0 / 0";
      return;
    }
    const idx = Math.min(state.detailIdx, details.length - 1);
    state.detailIdx = idx;
    imgDetail.src = details[idx].url;
    detailLabel.textContent = `${idx + 1} / ${details.length}`;
  }

  function setupReel() {
    reelFallback.classList.add("hidden");
    reelFallback.innerHTML = "";
    videoReel.classList.add("hidden");
    btnPlay.textContent = "▶";

    const reel = state.portfolio?.reel;
    if (reel?.url) {
      videoReel.src = reel.url;
      videoReel.load();
      videoReel.playbackRate = parseFloat(reelSpeed.value);
      videoReel.classList.remove("hidden");
    } else if (state.portfolio?.reel_stills?.length) {
      reelFallback.classList.remove("hidden");
      reelFallback.innerHTML = state.portfolio.reel_stills
        .map((s) => `<img src="${s.url}" alt="" draggable="false" />`)
        .join("");
    }
  }

  async function loadGallery() {
    const res = await fetch("/api/studio/client-gallery/event");
    if (!res.ok) {
      window.location.href = "/client-gallery";
      return;
    }
    state.portfolio = await res.json();
    document.getElementById("view-category").textContent = state.portfolio.category;
    document.getElementById("view-event-name").textContent = state.portfolio.event_name;
    imgCover.src = state.portfolio.cover.url;
    state.detailIdx = 0;
    renderDetail();
    setupReel();
    showPanel("cover");
  }

  document.getElementById("btn-prev").addEventListener("click", () => {
    const n = state.portfolio?.details?.length || 0;
    if (!n) return;
    state.detailIdx = (state.detailIdx - 1 + n) % n;
    renderDetail();
  });

  document.getElementById("btn-next").addEventListener("click", () => {
    const n = state.portfolio?.details?.length || 0;
    if (!n) return;
    state.detailIdx = (state.detailIdx + 1) % n;
    renderDetail();
  });

  btnPlay.addEventListener("click", () => {
    if (!videoReel.src) return;
    if (videoReel.paused) {
      videoReel.play();
      btnPlay.textContent = "❚❚";
    } else {
      videoReel.pause();
      btnPlay.textContent = "▶";
    }
  });

  videoReel.addEventListener("ended", () => {
    btnPlay.textContent = "▶";
  });

  reelSpeed.addEventListener("input", () => {
    const r = parseFloat(reelSpeed.value);
    videoReel.playbackRate = r;
    speedReadout.textContent = `${r}×`;
  });

  // Touch swipe between photos / panels
  document.querySelectorAll(".client-swipe").forEach((zone) => {
    zone.addEventListener(
      "touchstart",
      (e) => {
        state.touchX = e.changedTouches[0].screenX;
        state.touchY = e.changedTouches[0].screenY;
      },
      { passive: true }
    );
    zone.addEventListener(
      "touchend",
      (e) => {
        const dx = e.changedTouches[0].screenX - state.touchX;
        const dy = e.changedTouches[0].screenY - state.touchY;
        if (Math.abs(dx) < 48 || Math.abs(dx) < Math.abs(dy)) return;

        const swipe = zone.dataset.swipe;
        if (swipe === "cover") {
          if (dx < 0) showPanel("details");
        } else if (swipe === "details") {
          if (dx < 0) {
            if (state.detailIdx < (state.portfolio?.details?.length || 1) - 1) {
              state.detailIdx += 1;
              renderDetail();
            } else {
              showPanel("reel");
            }
          } else {
            if (state.detailIdx > 0) {
              state.detailIdx -= 1;
              renderDetail();
            } else {
              showPanel("cover");
            }
          }
        } else if (swipe === "reel" && dx > 0) {
          showPanel("details");
        }
      },
      { passive: true }
    );
  });

  document.addEventListener("keydown", (e) => {
    if (!state.portfolio) return;
    if (e.key === "ArrowRight") {
      if (state.panel === "cover") showPanel("details");
      else if (state.panel === "details") document.getElementById("btn-next").click();
    } else if (e.key === "ArrowLeft") {
      if (state.panel === "reel") showPanel("details");
      else if (state.panel === "details") document.getElementById("btn-prev").click();
    } else if (e.key === " " && state.panel === "reel") {
      e.preventDefault();
      btnPlay.click();
    }
  });

  loadGallery();
})();
