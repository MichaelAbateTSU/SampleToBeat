import type { ReactNode } from "react";
import { BRAND, LEGAL_ENTITY, SUPPORT_EMAIL } from "../brand";

const UPDATED = "12 November 2026";

function Doc({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="section narrow legal">
      <h1>{title}</h1>
      <p className="muted">Last updated {UPDATED}</p>
      {children}
      <p>Questions: <a href={`mailto:${SUPPORT_EMAIL}`}>{SUPPORT_EMAIL}</a></p>
    </section>
  );
}

export function Terms() {
  return (
    <Doc title="Terms of Service">
      <p>These terms are an agreement between you and {LEGAL_ENTITY} ("we") for your use of {BRAND}. By using the service you accept them.</p>
      <h2>1. What {BRAND} does</h2>
      <p>You upload audio. {BRAND} analyses it and builds instrumental beats around it: it chops and re-times your audio and adds drum programming, drum sounds, a bass line, an arrangement and a mixdown ("generated material").</p>
      <h2>2. Your uploads</h2>
      <p>You keep all rights you have in the audio you upload. You give us permission to store and process it only to provide the service to you. We do not use your uploads to train machine-learning models, and we do not share them with other users.</p>
      <p><strong>You are responsible for your samples.</strong> Only upload audio you created or have permission to use. We do not check, clear or license third-party recordings or compositions, and nothing we provide gives you rights in someone else's work. If you release music containing a sample you don't have rights to, that is your responsibility.</p>
      <h2>3. License to generated material</h2>
      <p>On the Free plan you may use generated material for personal, non-commercial purposes. On paid plans you receive a worldwide, perpetual, royalty-free license to use generated material commercially, including in released and monetised music. Exports made while you were on a paid plan keep their commercial license if you later cancel. The drum sounds remain ours; you may use them inside your beats but not resell them as a sample pack.</p>
      <h2>4. Accounts</h2>
      <p>Keep your password private; you are responsible for activity on your account. You must be old enough to enter a contract where you live. You can delete your account at any time from the account page, which removes your uploads, beats and exports.</p>
      <h2>5. Plans and billing</h2>
      <p>Paid plans renew automatically each month or year until cancelled. You can cancel at any time from the account page; your plan stays active until the end of the period already paid for. Payments are processed by Stripe. Except where the law requires otherwise, payments are not refundable. Plan limits (generations, upload size, formats) are shown on the pricing page and may change for future billing periods with notice.</p>
      <h2>6. Acceptable use</h2>
      <p>Don't upload unlawful content, try to access other people's data, overload or probe the service, resell access, or use automated means to create accounts or generations in bulk.</p>
      <h2>7. Copyright complaints</h2>
      <p>If you believe material on {BRAND} infringes your copyright, email {SUPPORT_EMAIL} with the work concerned, where it appears, and your contact details. We remove infringing material and close the accounts of repeat infringers.</p>
      <h2>8. Availability and changes</h2>
      <p>We work to keep {BRAND} running but provide it "as is", without warranties. We may change or discontinue features; if we shut the service down we will give you time to download your work.</p>
      <h2>9. Liability</h2>
      <p>To the extent the law allows, we are not liable for indirect or consequential losses, and our total liability is limited to the amount you paid us in the twelve months before the claim. You agree to cover claims that arise from audio you uploaded without the right to use it.</p>
      <h2>10. Changes to these terms</h2>
      <p>If we make material changes we will tell you by email or in the app before they take effect.</p>
    </Doc>
  );
}

export function Privacy() {
  return (
    <Doc title="Privacy Policy">
      <p>This explains what {LEGAL_ENTITY} collects when you use {BRAND}, and why.</p>
      <h2>What we store</h2>
      <ul>
        <li><strong>Account:</strong> your email address and a salted hash of your password. We never store the password itself.</li>
        <li><strong>Your work:</strong> the audio you upload, the analysis of it, and the beats and export files made from it.</li>
        <li><strong>Usage:</strong> how many generations you've run this month, so plan limits can be applied.</li>
        <li><strong>Billing:</strong> your plan, subscription status, and Stripe customer and subscription IDs. Card details go to Stripe and never reach our servers.</li>
        <li><strong>Logs:</strong> standard server logs (IP address, time, request) kept for security and troubleshooting.</li>
      </ul>
      <h2>Cookies</h2>
      <p>One cookie, <code>choplab_session</code>, keeps you signed in (or remembers your guest session) for up to 30 days. It is essential for the service to work. We set no advertising or tracking cookies.</p>
      <h2>How we use it</h2>
      <p>To run the service for you, apply plan limits, take payment, send account emails such as password resets, and keep the service secure. We don't sell personal data, and we don't use your audio to train models.</p>
      <h2>Who else handles it</h2>
      <p>Stripe processes payments. Our hosting and email providers process data on our behalf to run the service. We disclose data if the law requires it.</p>
      <h2>Keeping and deleting</h2>
      <p>Your data is kept while your account exists. Deleting a project removes its audio and files. Deleting your account removes everything tied to it from our systems. Guest sessions that are never turned into an account are deleted, with their uploads, once the session expires.</p>
      <h2>Your rights</h2>
      <p>Depending on where you live, you may have the right to access, correct, export or erase your data and to object to some processing. Email us and we'll respond within 30 days.</p>
    </Doc>
  );
}
