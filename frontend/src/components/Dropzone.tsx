import { useRef, useState } from "react";
import { Icon } from "./Icon";

const ACCEPT = "audio/*,.wav,.mp3,.flac,.aif,.aiff,.ogg,.m4a";

export function Dropzone({ onFile, onDemo, file, compact }: { onFile: (f: File) => void; onDemo?: () => void; file?: File | null; compact?: boolean }) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const take = (f?: File | null) => f && onFile(f);
  return (
    <div
      className={`dropzone${over ? " over" : ""}${compact ? " compact" : ""}${file ? " has-file" : ""}`}
      onDragOver={(e) => { e.preventDefault(); setOver(true); }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => { e.preventDefault(); setOver(false); take(e.dataTransfer.files[0]); }}
    >
      <input ref={input} type="file" accept={ACCEPT} hidden onChange={(e) => { take(e.target.files?.[0]); e.target.value = ""; }} />
      <div className="dropzone-icon"><Icon name={file ? "music" : "upload"} size={26} /></div>
      {file ? (
        <>
          <div className="dropzone-title">{file.name}</div>
          <div className="dropzone-sub">{(file.size / 1e6).toFixed(1)} MB · <button type="button" className="link" onClick={() => input.current?.click()}>choose another</button></div>
        </>
      ) : (
        <>
          <div className="dropzone-title">Drop your sample here</div>
          <div className="dropzone-sub">A loop, a one-shot or a snippet of a song — WAV, MP3, FLAC, AIFF, M4A</div>
          <div className="dropzone-actions">
            <button type="button" className="btn btn-primary" onClick={() => input.current?.click()}>Choose a file</button>
            {onDemo && <button type="button" className="btn btn-ghost" onClick={onDemo}>No sample handy? Try the demo loop</button>}
          </div>
        </>
      )}
    </div>
  );
}
