/** Watermark Studio — live preview + FFmpeg settings sync */

(function () {
  "use strict";

  const FRAME_W = 1080;
  const FRAME_H = 1920;
  const DEFAULT_MARGIN = 56;

  const previewFrame = document.getElementById("wm-preview-frame");
  const previewLogo = document.getElementById("wm-preview-logo");
  const logoPicker = document.getElementById("wm-logo-picker");
  const opacityInput = document.getElementById("wm-opacity");
  const scaleInput = document.getElementById("wm-scale");
  const opacityVal = document.getElementById("wm-opacity-val");
  const scaleVal = document.getElementById("wm-scale-val");
  const anchorGrid = document.getElementById("wm-anchor-grid");
  const saveBtn = document.getElementById("wm-save-btn");
  const saveStatus = document.getElementById("wm-save-status");

  const state = {
    logo_filename: "",
    logo_url: "",
    opacity: 0.88,
    scale: 0.26,
    anchor: "top-center",
    margin: DEFAULT_MARGIN,
  };

  function frameRect() {
    return previewFrame.getBoundingClientRect();
  }

  function positionLogo() {
    if (!previewLogo.src) return;

    const rect = frameRect();
    const scaleX = rect.width / FRAME_W;
    const scaleY = rect.height / FRAME_H;
    const margin = state.margin * scaleX;
    const logoW = FRAME_W * state.scale * scaleX;

    previewLogo.style.width = `${logoW}px`;
    previewLogo.style.opacity = String(state.opacity);

    const logoRect = { w: logoW, h: previewLogo.offsetHeight || logoW * 0.32 };

    let left = margin;
    let top = margin;

    switch (state.anchor) {
      case "top-center":
        left = (rect.width - logoRect.w) / 2;
        top = margin;
        break;
      case "top-right":
        left = rect.width - logoRect.w - margin;
        top = margin;
        break;
      case "bottom-left":
        left = margin;
        top = rect.height - logoRect.h - margin * scaleY / scaleX;
        break;
      case "bottom-right":
        left = rect.width - logoRect.w - margin;
        top = rect.height - logoRect.h - margin * scaleY / scaleX;
        break;
      case "center":
        left = (rect.width - logoRect.w) / 2;
        top = (rect.height - logoRect.h) / 2;
        break;
      default:
        break;
    }

    previewLogo.style.left = `${left}px`;
    previewLogo.style.top = `${top}px`;
  }

  function renderLogoPicker(logos) {
    logoPicker.innerHTML = logos
      .map(
        (logo) => `
      <div class="wm-logo-option ${logo.filename === state.logo_filename ? "wm-logo-option-active" : ""}" data-filename="${logo.filename}" data-url="${logo.url}">
        <img src="${logo.url}" alt="${logo.filename}" />
        <span class="text-xs truncate">${logo.filename}</span>
      </div>`
      )
      .join("");

    logoPicker.querySelectorAll(".wm-logo-option").forEach((el) => {
      el.addEventListener("click", () => {
        state.logo_filename = el.dataset.filename;
        state.logo_url = el.dataset.url;
        previewLogo.src = state.logo_url;
        previewLogo.onload = positionLogo;
        renderLogoPicker(logos);
      });
    });
  }

  function setAnchor(anchor) {
    state.anchor = anchor;
    anchorGrid.querySelectorAll(".wm-anchor-btn").forEach((btn) => {
      btn.classList.toggle("wm-anchor-btn-active", btn.dataset.anchor === anchor);
    });
    positionLogo();
  }

  opacityInput.addEventListener("input", () => {
    state.opacity = parseInt(opacityInput.value, 10) / 100;
    opacityVal.textContent = `${opacityInput.value}%`;
    positionLogo();
  });

  scaleInput.addEventListener("input", () => {
    state.scale = parseInt(scaleInput.value, 10) / 100;
    scaleVal.textContent = `${scaleInput.value}%`;
    positionLogo();
  });

  anchorGrid.querySelectorAll(".wm-anchor-btn").forEach((btn) => {
    btn.addEventListener("click", () => setAnchor(btn.dataset.anchor));
  });

  saveBtn.addEventListener("click", async () => {
    saveStatus.textContent = "Saving…";
    const res = await fetch("/api/studio/watermark/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        logo_filename: state.logo_filename,
        opacity: state.opacity,
        scale: state.scale,
        anchor: state.anchor,
        margin: state.margin,
      }),
    });
    if (res.ok) {
      saveStatus.textContent = "Saved — applied to all reel renders.";
    } else {
      saveStatus.textContent = "Save failed. Try again.";
    }
  });

  window.addEventListener("resize", positionLogo);

  async function init() {
    const res = await fetch("/api/studio/watermark/settings");
    const data = await res.json();
    state.logo_filename = data.logo_filename;
    state.logo_url = data.logo_url;
    state.opacity = data.opacity;
    state.scale = data.scale;
    state.anchor = data.anchor;
    state.margin = data.margin || DEFAULT_MARGIN;

    opacityInput.value = Math.round(state.opacity * 100);
    scaleInput.value = Math.round(state.scale * 100);
    opacityVal.textContent = `${opacityInput.value}%`;
    scaleVal.textContent = `${scaleInput.value}%`;

    if (data.logo_url) {
      previewLogo.src = data.logo_url;
      previewLogo.onload = positionLogo;
    }

    renderLogoPicker(data.logos || []);
    setAnchor(state.anchor);
  }

  init();
})();
