import { useRef, useState } from "react";
import { Icon } from "./Icon";

const ACCEPT = "audio/*,.wav,.mp3,.flac,.aif,.aiff,.ogg,.m4a";
const mb = new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 });

export function Dropzone({ onFile, onDemo, file }: { onFile: (f: File) => void; onDemo?: () => void; file?: File | null }) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const take = (f?: File | null) => f && onFile(f);
  return (
    <div
      className={`dropzone${over ? " over" : ""}${file ? " has-file" : ""}`}
      onDragOver={(e) => { e.preventDefault(); setOver(true); }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => { e.preventDefault(); setOver(false); take(e.dataTransfer.files[0]); }}
    >
      <input ref={input} type="file" accept={ACCEPT} hidden aria-label="Sample file" onChange={(e) => { take(e.target.files?.[0]); e.target.value = ""; }} />
      <div className="dropzone-icon"><Icon name={file ? "music" : "upload"} size={24} /></div>
      {file ? (
        <>
          <div className="dropzone-title">{file.name}</div>
          <div className="dropzone-sub">{mb.format(file.size / 1e6)}&nbsp;MB, ready to chop. <button type="button" className="link" onClick={() => input.current?.click()}>Choose another</button></div>
        </>
      ) : (
        <>
          <div className="dropzone-title">Drop your sample here</div>
          <div className="dropzone-sub">A loop, a one-shot or a piece of a song. WAV, MP3, FLAC, AIFF or M4A.</div>
          <div className="dropzone-actions">
            <button type="button" className="btn btn-primary" onClick={() => input.current?.click()}>Choose a file</button>
            {onDemo && <button type="button" className="btn btn-ghost" onClick={onDemo}>Try the demo loop</button>}
          </div>
        </>
      )}
    </div>
  );
}
