export default function ImageGrid({ images }) {
  if (!images.length) {
    return (
      <div className="rounded-xl border border-white/5 bg-luxe-surface/50 p-12 text-center text-luxe-muted text-sm">
        No images yet. Upload venue photography to begin.
      </div>
    );
  }

  return (
    <div className="grid grid-cols-2 gap-3 max-h-[70vh] overflow-y-auto pr-2">
      {images.map((img) => (
        <div
          key={img.id || img.filepath}
          className="rounded-lg overflow-hidden bg-luxe-surface border border-white/5"
        >
          <img
            src={`http://127.0.0.1:8765/api/upload/file/${encodeURIComponent(img.filename)}`}
            alt={img.filename}
            className="w-full aspect-[4/5] object-cover"
            onError={(e) => {
              e.target.style.display = "none";
            }}
          />
          <div className="p-2 text-xs">
            <p className="truncate text-luxe-text">{img.filename}</p>
            <p className={`mt-0.5 ${img.is_uncategorized ? "text-amber-400" : "text-luxe-accent"}`}>
              {img.primary_category}
              {img.is_uncategorized ? " (low confidence)" : ""}
            </p>
            {img.confidence != null && (
              <p className="text-luxe-muted">
                {(img.confidence * 100).toFixed(0)}% confidence
              </p>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
