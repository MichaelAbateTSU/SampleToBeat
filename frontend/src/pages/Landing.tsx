import { useNavigate } from "react-router-dom";
import { Dropzone } from "../components/Dropzone";
import { Icon } from "../components/Icon";
import { clock, LcdText } from "../components/Lcd";
import { Pricing } from "../components/Pricing";
import { Waveform } from "../components/Waveform";
import { DEMOS } from "../demos";
import { setPendingSample } from "../handoff";
import { usePlayer } from "../player";
import { useSession } from "../session";

const STEPS = [
  { title: "Drop in a sample", text: "A two-bar loop, a single piano stab, thirty seconds of an old record. If it's audio, it works." },
  { title: "ChopLab reads it", text: "It finds the tempo, key and downbeat, picks the strongest phrases and slices them on the transients." },
  { title: "Pick from four flips", text: "Each one is a full arrangement with an intro, hooks, verses and an outro, and drums and an 808 that follow your sample's chords." },
];

const FEATURES = [
  { icon: "music", title: "Any sample, not only loops", text: "It reads the tempo from how the music repeats, so drumless keys, strings and guitar work too. Tight loops are used whole, longer recordings are searched for the best bars, and one-shots are played as a melody in key." },
  { icon: "layers", title: "Four flips, four ideas", text: "A straight loop flip, a chopped re-sequence, a slowed-down take and a sped-up one, the way producers have always worked records. Reroll any of them." },
  { icon: "sliders", title: "A studio, not a slot machine", text: "Change the tempo, swing, drum kit and mix. Mute parts. Rebuild from a different chop. What you hear is what you export." },
  { icon: "download", title: "Files a producer can use", text: "A mastered WAV, separate track stems, and MIDI for the drums, 808 and chop triggers, ready for your DAW." },
  { icon: "shield", title: "Your sample stays yours", text: "Nothing is trained on your uploads. The engine is signal processing and music theory, so every sound traces back to your sample or our drum kits." },
  { icon: "file", title: "A license with each export", text: "Paid plans include a commercial license for everything ChopLab generates, in plain words, saved next to your files." },
];

const FAQ = [
  ["What kind of audio can I upload?", "WAV, MP3, FLAC, AIFF, OGG or M4A. Anything from a tenth of a second to several minutes. Short clips are treated as the sample itself; longer recordings are searched for the best loops and chops."],
  ["Is this an AI song generator?", "No. ChopLab doesn't invent audio from a text prompt. It analyses the sample you give it and builds a beat around it: chopping, time-stretching, drum programming, a key-aware 808 line, arrangement and mastering. That is why results stay faithful to your sample and why you can edit every part."],
  ["Can I release what I make?", "The Pro and Studio plans include a commercial license for everything ChopLab adds. The sample you upload is a separate matter: if it comes from someone else's record, you need their permission to release it. ChopLab can't clear samples for you."],
  ["Which styles does it make?", "Boom bap, trap, drill and lo-fi today, each with its own drum kit, swing, tempo range and 808 behaviour."],
  ["Do I need an account to try it?", "No. Make a few beats and listen first. You're asked to sign up when you want to download or keep going."],
  ["How do I cancel?", "From your account page, in two clicks. You keep your plan until the end of the period you paid for, and your projects stay in your library on the free plan."],
];

/** The hero: a sampler you can play. Each pad is a real flip of the same loop, rendered by the engine. */
function Machine({ onFile, onDemo }: { onFile: (f: File) => void; onDemo: () => void }) {
  const player = usePlayer();
  const current = DEMOS.find((d) => d.id === player.track?.id);
  const playing = current && player.playing;
  const source = DEMOS.find((d) => d.source);
  const flips = DEMOS.filter((d) => !d.source);
  const press = (d: (typeof DEMOS)[number]) =>
    player.toggle({ id: d.id, url: d.url, title: d.source ? "The sample" : `${d.title} flip`, subtitle: `${d.bpm} BPM, ${d.key}`, peaks: d.peaks, duration: d.duration });
  return (
    <div className="machine">
      <div className="lcd machine-lcd" aria-live="polite">
        <span className="line-1">{current ? (current.source ? "The sample: a soul loop" : `${current.title} flip`) : "Press a pad"}</span>
        <span className="line-2 lcd-dim">{current ? `${current.bpm} BPM  ${current.key}${current.style ? `  ${current.style}` : ""}` : "One loop in, four beats out"}</span>
        <span className="time"><LcdText>{clock(current ? player.time : 0)}</LcdText></span>
      </div>
      <div className="machine-wave">
        <Waveform peaks={(current ?? source)?.peaks} height={40} progress={current && player.duration ? player.time / player.duration : 0} onSeek={current ? (f) => player.seek(f * player.duration) : undefined} />
      </div>
      <div className="pads" role="group" aria-label="Hear the demo">
        {flips.map((d, i) => {
          const on = playing && current?.id === d.id;
          return (
            <button key={d.id} type="button" className={`pad-btn${on ? " on" : ""}`} aria-pressed={!!on} onClick={() => press(d)}>
              <span className="led" aria-hidden="true" />
              <span>{d.title}</span>
              <small>Pad {i + 1}, {d.bpm} BPM</small>
            </button>
          );
        })}
        {source && (
          <button type="button" className={`pad-btn src${playing && current?.id === source.id ? " on" : ""}`} aria-pressed={!!(playing && current?.id === source.id)} onClick={() => press(source)}>
            <span className="led" aria-hidden="true" />
            <span>Hear the sample they were made from</span>
          </button>
        )}
      </div>
      <Dropzone onFile={onFile} onDemo={onDemo} />
    </div>
  );
}

export function Landing() {
  const nav = useNavigate();
  const { plans } = useSession();
  const go = (f: File | "demo") => {
    setPendingSample(f);
    nav("/app/new");
  };
  return (
    <>
      <section className="hero">
        <div className="hero-inner">
          <div className="hero-copy">
            <h1>Drop a sample.<br />Get a beat.</h1>
            <p className="lead">ChopLab turns any piece of audio into four finished instrumentals: chopped, arranged, with drums and 808s in key. Then it hands you the WAV, the stems and the MIDI.</p>
            <ul className="hero-points">
              <li><Icon name="check" size={18} /> Free to try, no account needed{plans ? ` for your first ${plans.guest_generations} beats` : ""}</li>
              <li><Icon name="check" size={18} /> About a minute from upload to four flips</li>
              <li><Icon name="check" size={18} /> Boom bap, trap, drill and lo-fi</li>
            </ul>
          </div>
          <Machine onFile={go} onDemo={() => go("demo")} />
        </div>
      </section>

      <section className="section" id="how">
        <div className="section-head"><h2>From sample to song in three steps</h2></div>
        <ol className="steps">
          {STEPS.map((s, i) => (
            <li key={s.title}>
              <span className="no" aria-hidden="true">{i + 1}</span>
              <h3>{s.title}</h3>
              <p>{s.text}</p>
            </li>
          ))}
        </ol>
      </section>

      <section className="section" id="features">
        <div className="section-head">
          <h2>Built for people who finish beats</h2>
          <p className="lead">Fast enough to spark an idea, deep enough to keep working on it.</p>
        </div>
        <dl className="features">
          {FEATURES.map((f) => (
            <div key={f.title}>
              <Icon name={f.icon} size={24} />
              <dt>{f.title}</dt>
              <dd>{f.text}</dd>
            </div>
          ))}
        </dl>
      </section>

      <section className="section" id="pricing">
        <div className="section-head">
          <h2>Start free. Upgrade when it's time to release.</h2>
        </div>
        <Pricing />
      </section>

      <section className="section narrow" id="faq">
        <div className="section-head"><h2>Questions</h2></div>
        <div className="faq">
          {FAQ.map(([q, a]) => (
            <details key={q}>
              <summary>{q}</summary>
              <p>{a}</p>
            </details>
          ))}
        </div>
      </section>

      <section className="closing">
        <div>
          <h2>Got a sample sitting in a folder?</h2>
          <p>Find out what it could be.</p>
        </div>
        <button className="btn btn-primary btn-lg" onClick={() => nav("/app/new")}>Make a beat</button>
      </section>
    </>
  );
}
