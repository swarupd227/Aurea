// Lightweight Web Speech API helpers: ask by voice in the composer, listen to a reply.
// Nothing here leaves the browser except through the browser's own speech service.

/** A reply as it should sound: the words of the markdown, not its punctuation. */
function plain(text: string): string {
  return text
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/^\s*\|?\s*:?-{2,}.*$/gm, " ")
    .replace(/[*#`_>|]/g, " ")
    .replace(/\s{2,}/g, " ")
    .trim();
}

/** Whether this browser can read text aloud. */
export function speechSupported(): boolean {
  return typeof window !== "undefined" && "speechSynthesis" in window;
}

// The reply being read now, so starting another (or stopping) tells the first it has ended.
let ending: (() => void) | null = null;

/** Read text aloud, stopping anything already being read. `onEnd` runs once it finishes or is stopped. */
export function speak(text: string, onEnd?: () => void) {
  if (!speechSupported()) {
    onEnd?.();
    return;
  }
  try {
    stopSpeaking();
    const u = new SpeechSynthesisUtterance(plain(text));
    u.rate = 1.0;
    u.pitch = 1.0;
    let ended = false;
    const done = () => {
      if (ended) return;
      ended = true;
      if (ending === done) ending = null;
      onEnd?.();
    };
    ending = done;
    u.onend = done;
    u.onerror = done;
    window.speechSynthesis.speak(u);
  } catch {
    onEnd?.();
  }
}

/** Stop reading aloud. */
export function stopSpeaking() {
  if (!speechSupported()) return;
  const end = ending;
  ending = null;
  try {
    window.speechSynthesis.cancel();
  } catch {}
  end?.();
}

/** Whether this browser can turn speech into text. */
export function voiceSupported(): boolean {
  if (typeof window === "undefined") return false;
  return !!((window as any).SpeechRecognition || (window as any).webkitSpeechRecognition);
}

/** Listen for one utterance. Returns a function that stops listening early. */
export function listenOnce(onResult: (text: string) => void, onEnd?: () => void): () => void {
  const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
  if (!SR) {
    onEnd?.();
    return () => {};
  }
  try {
    const rec = new SR();
    rec.lang = navigator.language || "en-NZ";
    rec.interimResults = false;
    rec.maxAlternatives = 1;
    rec.onresult = (e: any) => onResult(e.results[0][0].transcript);
    rec.onend = () => onEnd?.();
    rec.onerror = () => onEnd?.();
    rec.start();
    return () => {
      try {
        rec.stop();
      } catch {}
    };
  } catch {
    // Already listening elsewhere, or the microphone was refused.
    onEnd?.();
    return () => {};
  }
}
