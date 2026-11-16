import { Link } from "react-router-dom";

/** Las secciones que todavía no están: cada una llega en su bloque de la Fase 6. */
export function ComingSoon({ title, block, text }: { title: string; block: string; text: string }) {
  return (
    <main className="page">
      <div className="page-head">
        <h1>{title}</h1>
        <p>{text}</p>
      </div>
      <section className="card placeholder">
        <span className="pill pill-ice">Llega en el bloque {block}</span>
        <p className="muted">Esta pantalla ya tiene su lugar en el menú. La programamos en su bloque, después de que apruebes su diseño.</p>
        <Link to="/">Volver al inicio</Link>
      </section>
    </main>
  );
}
