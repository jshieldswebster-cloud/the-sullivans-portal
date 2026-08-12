export default function UploadZone({ onUpload, loading }) {
  const handleChange = (e) => {
    const files = Array.from(e.target.files || []);
    if (files.length) onUpload(files);
  };

  const handleDrop = (e) => {
    e.preventDefault();
    const files = Array.from(e.dataTransfer.files || []);
    if (files.length) onUpload(files);
  };

  return (
    <div
      onDragOver={(e) => e.preventDefault()}
      onDrop={handleDrop}
      className="border-2 border-dashed border-luxe-accent/30 rounded-xl p-8 text-center hover:border-luxe-accent/60 transition cursor-pointer"
    >
      <input
        type="file"
        multiple
        accept="image/*"
        onChange={handleChange}
        className="hidden"
        id="file-upload"
        disabled={loading}
      />
      <label htmlFor="file-upload" className="cursor-pointer block">
        <p className="text-luxe-accent font-medium">
          {loading ? "Processing…" : "Drop photos here"}
        </p>
        <p className="text-xs text-luxe-muted mt-2">
          Batch upload · auto-classified locally
        </p>
      </label>
    </div>
  );
}
