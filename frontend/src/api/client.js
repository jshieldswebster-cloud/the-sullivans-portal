const DEFAULT_BASE = "http://127.0.0.1:8765";

async function getBaseUrl() {
  if (window.vvLuxe?.getApiBase) {
    return window.vvLuxe.getApiBase();
  }
  return DEFAULT_BASE;
}

export async function apiFetch(path, options = {}) {
  const base = await getBaseUrl();
  const res = await fetch(`${base}${path}`, options);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Request failed");
  }
  return res.json();
}

export async function uploadBatch(files) {
  const base = await getBaseUrl();
  const form = new FormData();
  for (const file of files) form.append("files", file);
  const res = await fetch(`${base}/api/upload/batch`, { method: "POST", body: form });
  if (!res.ok) throw new Error("Upload failed");
  return res.json();
}

export async function fetchHealth() {
  return apiFetch("/api/health");
}

export async function fetchImages(category) {
  const q = category ? `?category=${encodeURIComponent(category)}` : "";
  return apiFetch(`/api/upload/images${q}`);
}

export async function loadModels() {
  return apiFetch("/api/models/load", { method: "POST" });
}

export async function generateBundle(category) {
  return apiFetch("/api/render/bundle", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ category }),
  });
}

export async function generateCaption(category) {
  return apiFetch("/api/captions/generate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ category }),
  });
}
