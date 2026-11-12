// Carries the file picked on the landing page into the create screen (File objects can't go through the URL).
let pending: File | "demo" | null = null;
export const setPendingSample = (f: File | "demo" | null) => { pending = f; };
export const takePendingSample = () => { const f = pending; pending = null; return f; };
