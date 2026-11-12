import { Link } from "react-router-dom";

export function NotFound() {
  return (
    <section className="section narrow center">
      <h1>That page isn't here</h1>
      <p className="muted">The link may be old, or the beat may have been deleted.</p>
      <Link className="btn btn-primary" to="/">Back to ChopLab</Link>
    </section>
  );
}
