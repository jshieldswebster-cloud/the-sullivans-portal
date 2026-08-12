export default function CategoryFilter({ categories, selected, onSelect, counts }) {
  return (
    <div className="rounded-xl bg-luxe-surface border border-white/5 p-4">
      <h3 className="text-xs uppercase tracking-wider text-luxe-muted mb-3">
        Event Categories
      </h3>
      <ul className="space-y-1">
        <li>
          <button
            onClick={() => onSelect(null)}
            className={`w-full text-left px-3 py-2 rounded-lg text-sm transition ${
              !selected
                ? "bg-luxe-accent/20 text-luxe-accent"
                : "hover:bg-white/5 text-luxe-text"
            }`}
          >
            All
          </button>
        </li>
        {categories.map((cat) => (
          <li key={cat}>
            <button
              onClick={() => onSelect(cat)}
              className={`w-full text-left px-3 py-2 rounded-lg text-sm transition flex justify-between ${
                selected === cat
                  ? "bg-luxe-accent/20 text-luxe-accent"
                  : "hover:bg-white/5 text-luxe-text"
              }`}
            >
              <span>{cat}</span>
              {counts[cat] ? (
                <span className="text-luxe-muted text-xs">{counts[cat]}</span>
              ) : null}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
