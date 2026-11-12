import { Fragment } from "react";

/** Text for LCD readouts. The dot-matrix face draws ":" and "." ambiguously at small sizes, so punctuation
    between digits is set in the body face to keep times and decimals unmistakable. */
export function LcdText({ children }: { children: string }) {
  const parts = children.split(/([:.])/);
  return (
    <>
      {parts.map((p, i) => (p === ":" || p === "." ? <span key={i} className="lcd-punct">{p}</span> : <Fragment key={i}>{p}</Fragment>))}
    </>
  );
}

export const clock = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
