import { Pricing } from "../components/Pricing";

export function PricingPage() {
  return (
    <section className="section">
      <div className="section-head center">
        <h1>Simple pricing. Cancel any time.</h1>
        <p className="lead">Make and hear beats for free. Pay when you want release-quality files and a commercial license.</p>
      </div>
      <Pricing />
      <div className="pricing-notes">
        <p><strong>What's a generation?</strong> One run of the engine: up to four different flips of your sample, each a full arrangement. Only finished runs count.</p>
        <p><strong>What does the license cover?</strong> Everything ChopLab creates — drums, 808, arrangement, mix. It can't cover the sample you bring: if that's someone else's recording, clearing it is on you.</p>
      </div>
    </section>
  );
}
