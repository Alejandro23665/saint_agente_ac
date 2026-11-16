/** El logo de Saint tal cual, con "artificial intelligence" debajo como en la landing. */
export function Logo({ width = 140 }: { width?: number }) {
  return (
    <div role="img" aria-label="Saint artificial intelligence" className="logo" style={{ width }}>
      <img src="/logo-saint.png" alt="" />
      <svg aria-hidden="true" viewBox="0 0 640 64">
        <text x="5" y="44" textLength="629" lengthAdjust="spacingAndGlyphs">
          artificial intelligence
        </text>
      </svg>
    </div>
  );
}
