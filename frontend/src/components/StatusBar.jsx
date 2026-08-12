export default function StatusBar({ health }) {
  if (!health) {
    return <span className="text-xs text-luxe-muted">Connecting…</span>;
  }

  return (
    <div className="flex items-center gap-4 text-xs">
      <span className="flex items-center gap-1.5">
        <span
          className={`w-2 h-2 rounded-full ${
            health.status === "ok" ? "bg-emerald-500" : "bg-red-500"
          }`}
        />
        Backend
      </span>
      <span className="text-luxe-muted">Device: {health.device}</span>
      <span className="text-luxe-muted">
        Ollama: {health.ollama_available ? "ready" : "offline"}
      </span>
      {health.mps && (
        <span className="text-luxe-accent/80">MPS enabled</span>
      )}
    </div>
  );
}
