/** Instagram 9-grid preview — fetch assets, interactive cell swap */

let assets = [];
let selectedSlot = null;
let activeCategory = "";

const grid = document.getElementById("instagram-grid");
const caption = document.getElementById("grid-caption");

document.querySelectorAll(".grid-filter").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".grid-filter").forEach((b) => {
      b.classList.remove("active-filter", "text-sullivan-green", "border-sullivan-green/40");
      b.classList.add("text-muted", "border-sullivan-taupe");
    });
    btn.classList.add("active-filter", "text-sullivan-green", "border-sullivan-green/40");
    btn.classList.remove("text-muted", "border-sullivan-taupe");
    activeCategory = btn.dataset.category || "";
    loadAssets();
  });
});

async function loadAssets() {
  const qs = activeCategory ? `?category=${encodeURIComponent(activeCategory)}` : "";
  const res = await fetch(`/api/studio/assets${qs}`);
  if (!res.ok) {
    caption.textContent = "Unable to load assets";
    return;
  }
  const data = await res.json();
  assets = data.assets.slice(0, 9);
  renderGrid();
  caption.textContent = assets.length
    ? `${assets.length} photo${assets.length === 1 ? "" : "s"} in your 9-grid preview`
    : "Upload photos on the dashboard to populate your grid";
}

function renderGrid() {
  grid.querySelectorAll(".grid-cell").forEach((cell, idx) => {
    cell.innerHTML = `<div class="empty-slot absolute inset-0 flex items-center justify-center text-muted/40 text-xs">+</div>`;
    const asset = assets[idx];
    if (asset) {
      cell.innerHTML = `
        <img src="${asset.url}" alt="" class="w-full h-full object-cover" />
        <span class="grid-category-badge">${asset.category || ""}</span>
      `;
    }
    cell.onclick = () => handleCellClick(idx);
  });
  selectedSlot = null;
}

function handleCellClick(idx) {
  if (selectedSlot === null) {
    if (!assets[idx]) return;
    selectedSlot = idx;
    grid.querySelectorAll(".grid-cell")[idx].classList.add("ring-2", "ring-sullivan-green");
    return;
  }
  if (selectedSlot === idx) {
    grid.querySelectorAll(".grid-cell")[idx].classList.remove("ring-2", "ring-sullivan-green");
    selectedSlot = null;
    return;
  }
  const tmp = assets[selectedSlot];
  assets[selectedSlot] = assets[idx];
  assets[idx] = tmp;
  renderGrid();
}

loadAssets();
