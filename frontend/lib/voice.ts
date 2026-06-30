// Lightweight Web Speech API helpers for the Canvas voice assistant.

export function speak(text: string) {
  if (typeof window === "undefined" || !("speechSynthesis" in window)) return;
  try {
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.rate = 1.0;
    u.pitch = 1.0;
    window.speechSynthesis.speak(u);
  } catch {}
}

export function voiceSupported(): boolean {
  if (typeof window === "undefined") return false;
  return !!((window as any).SpeechRecognition || (window as any).webkitSpeechRecognition);
}

export function listenOnce(onResult: (text: string) => void, onEnd?: () => void): () => void {
  const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
  if (!SR) {
    onEnd?.();
    return () => {};
  }
  const rec = new SR();
  rec.lang = "en-NZ";
  rec.interimResults = false;
  rec.maxAlternatives = 1;
  rec.onresult = (e: any) => onResult(e.results[0][0].transcript);
  rec.onend = () => onEnd?.();
  rec.onerror = () => onEnd?.();
  rec.start();
  return () => rec.stop();
}
