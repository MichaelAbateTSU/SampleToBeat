import { useNavigate } from "react-router-dom";
import { Dropzone } from "../components/Dropzone";
import { Icon } from "../components/Icon";
import { Pricing } from "../components/Pricing";
import { Waveform } from "../components/Waveform";
import { DEMOS } from "../demos";
import { setPendingSample } from "../handoff";
import { usePlayer } from "../player";
import { useSession } from "../session";

const STEPS = [
  { icon: "upload", title: "Drop in a sample", text: "A two-bar loop, a single piano stab, thirty seconds of an old record. If it's audio, it works." },
  { icon: "wave", title: "ChopLab reads it", text: "It finds the tempo, key and downbeat, picks the strongest phrases and slices them on the transients." },
  { icon: "spark", title: "Get four finished flips", text: "Each one a full arrangement — intro, hooks, verses, outro — with drums and an 808 that follow your sample's chords." },
];

const FEATURES = [
  { icon: "music", title: "Any sample, not just loops", text: "Tight loops lock to their exact tempo. Loose snippets get re-timed to the grid. One-shots are played as a melody in key." },
  { icon: "layers", title: "Four flips, four ideas", text: "A straight loop flip, hard chops, a dark pitched-down take and a creative one with reverses and stutters. Reroll any of them." },
  { icon: "sliders", title: "A studio, not a slot machine", text: "Change the tempo, swing, drum kit and mix. Mute parts. Rebuild from a different chop. What you hear is what you export." },
  { icon: "download", title: "Files a producer can use", text: "Mastered WAV, separate track stems, and MIDI for the drums, 808 and chop triggers — drop them straight into your DAW." },
  { icon: "shield", title: "Your sample stays yours", text: "No model is trained on your uploads. The engine is signal processing and music theory, so every sound traces back to your sample or our drum kits." },
  { icon: "file", title: "A license with each export", text: "Paid plans include a commercial license for everything ChopLab generates, in plain words, saved next to your files." },
];

const FAQ = [
  ["What kind of audio can I upload?", "WAV, MP3, FLAC, AIFF, OGG or M4A. Anything from a tenth of a second to several minutes. Short clips are treated as the sample itself; longer recordings are searched for the best loops and chops."],
  ["Is this an AI song generator?", "No. ChopLab doesn't invent audio from a text prompt. It analyses the sample you give it and builds a beat around it with a deterministic engine — chopping, time-stretching, drum programming, a key-aware 808 line, arrangement and mastering. That's why results stay faithful to your sample and why you can edit every part."],
  ["Can I release what I make?", "The Pro and Studio plans include a commercial license for everything ChopLab adds. The sample you upload is a separate matter: if it comes from someone else's record, you need their permission to release it. We can't clear samples for you."],
  ["Which styles does it make?", "Boom bap, trap, drill and lo-fi today, each with its own drum kit, swing, tempo range and 808 behaviour. More are on the way."],
  ["Do I need an account to try it?", "No. Make a few beats and listen first. You'll only be asked to sign up when you want to download or keep going."],
  ["How do I cancel?", "From your account page, in two clicks. You keep your plan until the end of the period you paid for, and your projects stay in your library on the free plan."],
];

export function Landing() {
  const nav = useNavigate();
  const player = usePlayer();
  const { plans } = useSession();
  const go = (f: File | "demo") => {
    setPendingSample(f);
    nav("/app/new");
  };
  return (
    <>
      <section className="hero">
        <div className="hero-glow" aria-hidden="true" />
        <div className="hero-inner">
          <div className="hero-copy">
            <div className="eyebrow">Sample-based beat maker</div>
            <h1>Drop a sample.<br /><span className="grad">Get a beat.</span></h1>
            <p className="lead">ChopLab turns any piece of audio into four finished instrumentals — chopped, arranged, with drums and 808s in key. Then hands you the WAV, the stems and the MIDI.</p>
            <ul className="hero-points">
              <li><Icon name="check" size={16} /> Free to try, no account needed</li>
              <li><Icon name="check" size={16} /> About 30 seconds per beat</li>
              <li><Icon name="check" size={16} /> Boom bap, trap, drill, lo-fi</li>
            </ul>
          </div>
          <div className="hero-drop">
            <Dropzone onFile={go} onDemo={() => go("demo")} />
            {plans && <p className="fineprint center">{plans.guest_generations} free beats before you need an account.</p>}
          </div>
        </div>
      </section>

      <section className="section" id="hear">
        <div className="section-head center">
          <h2>Hear the difference</h2>
          <p className="lead">One eight-bar loop went in. These came out — unedited, straight from the engine.</p>
        </div>
        <div className="demo-grid">
          {DEMOS.map((d) => {
            const on = player.isPlaying(d.id);
            return (
              <button key={d.id} className={`demo-card${d.source ? " demo-source" : ""}${on ? " on" : ""}`} onClick={() => player.toggle({ id: d.id, url: d.url, title: d.title, subtitle: d.note, peaks: d.peaks, duration: d.duration })}>
                <span className="play-btn" aria-hidden="true"><Icon name={on ? "pause" : "play"} /></span>
                <span className="demo-text">
                  <span className="demo-label">{d.source ? "The sample" : "ChopLab made"}</span>
                  <span className="demo-title">{d.title}</span>
                  <span className="demo-note">{d.note}</span>
                </span>
                <Waveform peaks={d.peaks} progress={player.track?.id === d.id && player.duration ? player.time / player.duration : 0} height={40} />
              </button>
            );
          })}
        </div>
      </section>

      <section className="section" id="how">
        <div className="section-head center">
          <h2>From sample to song in three steps</h2>
        </div>
        <div className="steps">
          {STEPS.map((s, i) => (
            <div className="step" key={s.title}>
              <div className="step-num">{i + 1}</div>
              <div className="step-icon"><Icon name={s.icon} size={24} /></div>
              <h3>{s.title}</h3>
              <p>{s.text}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="section" id="features">
        <div className="section-head center">
          <h2>Built for people who finish beats</h2>
          <p className="lead">Fast enough to spark ideas, deep enough to keep working on them.</p>
        </div>
        <div className="features">
          {FEATURES.map((f) => (
            <div className="feature" key={f.title}>
              <div className="feature-icon"><Icon name={f.icon} size={22} /></div>
              <h3>{f.title}</h3>
              <p>{f.text}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="section" id="pricing">
        <div className="section-head center">
          <h2>Start free. Upgrade when it's time to release.</h2>
        </div>
        <Pricing />
      </section>

      <section className="section narrow" id="faq">
        <div className="section-head center"><h2>Questions</h2></div>
        <div className="faq">
          {FAQ.map(([q, a]) => (
            <details key={q}>
              <summary>{q}</summary>
              <p>{a}</p>
            </details>
          ))}
        </div>
      </section>

      <section className="section cta">
        <h2>Got a sample sitting in a folder?</h2>
        <p className="lead">Find out what it could be.</p>
        <button className="btn btn-primary btn-lg" onClick={() => nav("/app/new")}>Make a beat <Icon name="arrow" /></button>
      </section>
    </>
  );
}
