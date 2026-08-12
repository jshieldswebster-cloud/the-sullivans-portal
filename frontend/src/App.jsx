import { useCallback, useEffect, useState } from "react";
import {
  fetchHealth,
  fetchImages,
  generateBundle,
  generateCaption,
  loadModels,
  uploadBatch,
} from "./api/client";
import CategoryFilter from "./components/CategoryFilter";
import ImageGrid from "./components/ImageGrid";
import StatusBar from "./components/StatusBar";
import UploadZone from "./components/UploadZone";

const CATEGORIES = [
  "Baby Shower",
  "Birthday",
  "Corporate",
  "Weddings",
  "Legacy Receptions",
];

export default function App() {
  const [health, setHealth] = useState(null);
  const [selectedCategory, setSelectedCategory] = useState(null);
  const [images, setImages] = useState([]);
  const [loading, setLoading] = useState(false);
  const [caption, setCaption] = useState("");
  const [bundleResult, setBundleResult] = useState(null);
  const [error, setError] = useState(null);

  const refresh = useCallback(async () => {
    try {
      const [h, imgData] = await Promise.all([
        fetchHealth(),
        fetchImages(selectedCategory),
      ]);
      setHealth(h);
      setImages(imgData.images || []);
    } catch (e) {
      setError(e.message);
    }
  }, [selectedCategory]);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 15000);
    return () => clearInterval(id);
  }, [refresh]);

  const handleUpload = async (files) => {
    setLoading(true);
    setError(null);
    try {
      await loadModels();
      await uploadBatch(files);
      await refresh();
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const handleGenerateBundle = async () => {
    if (!selectedCategory) return;
    setLoading(true);
    setError(null);
    try {
      await loadModels();
      const result = await generateBundle(selectedCategory);
      setBundleResult(result);
      setCaption(result.caption);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const handleGenerateCaption = async () => {
    if (!selectedCategory) return;
    setLoading(true);
    setError(null);
    try {
      const result = await generateCaption(selectedCategory);
      setCaption(result.caption);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex flex-col">
      <header className="border-b border-white/5 px-8 py-6 flex items-center justify-between">
        <div>
          <h1 className="font-display text-2xl tracking-wide text-luxe-accent">
            VV LUXE Studio
          </h1>
          <p className="text-sm text-luxe-muted mt-1">
            Local AI content production · Richmond, CA
          </p>
        </div>
        <StatusBar health={health} />
      </header>

      <main className="flex-1 grid grid-cols-12 gap-6 p-8">
        <aside className="col-span-3 space-y-6">
          <UploadZone onUpload={handleUpload} loading={loading} />
          <CategoryFilter
            categories={CATEGORIES}
            selected={selectedCategory}
            onSelect={setSelectedCategory}
            counts={images.reduce((acc, img) => {
              const cat = img.primary_category;
              if (cat) acc[cat] = (acc[cat] || 0) + 1;
              return acc;
            }, {})}
          />
          {selectedCategory && (
            <div className="space-y-3">
              <button
                onClick={handleGenerateBundle}
                disabled={loading}
                className="w-full py-3 px-4 rounded-lg bg-luxe-accent text-luxe-bg font-medium text-sm hover:opacity-90 disabled:opacity-40 transition"
              >
                Generate Full Bundle
              </button>
              <button
                onClick={handleGenerateCaption}
                disabled={loading}
                className="w-full py-3 px-4 rounded-lg border border-luxe-accent/40 text-luxe-accent text-sm hover:bg-luxe-accent/10 disabled:opacity-40 transition"
              >
                Caption Only
              </button>
            </div>
          )}
        </aside>

        <section className="col-span-5">
          <h2 className="text-sm uppercase tracking-widest text-luxe-muted mb-4">
            Library {selectedCategory ? `· ${selectedCategory}` : ""}
          </h2>
          <ImageGrid images={images} />
        </section>

        <section className="col-span-4 space-y-4">
          <h2 className="text-sm uppercase tracking-widest text-luxe-muted">
            Output
          </h2>
          {error && (
            <div className="p-4 rounded-lg bg-red-950/40 border border-red-800/50 text-red-200 text-sm">
              {error}
            </div>
          )}
          {caption && (
            <div className="p-5 rounded-xl bg-luxe-surface border border-white/5">
              <h3 className="text-xs uppercase tracking-wider text-luxe-accent mb-3">
                Caption
              </h3>
              <p className="text-sm leading-relaxed whitespace-pre-wrap">{caption}</p>
            </div>
          )}
          {bundleResult && (
            <div className="p-5 rounded-xl bg-luxe-surface border border-white/5 text-sm space-y-2">
              <p>
                <span className="text-luxe-muted">Reel:</span>{" "}
                {bundleResult.reel_path}
              </p>
              <p>
                <span className="text-luxe-muted">Carousel slides:</span>{" "}
                {bundleResult.carousel_slides?.length}
              </p>
            </div>
          )}
        </section>
      </main>
    </div>
  );
}
