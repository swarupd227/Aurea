"use client";
import { useEffect, useRef, useState } from "react";
import { Brain, Cpu, ShieldCheck, UserCheck, Send, Sparkles, Check, Loader2, ShieldAlert, Zap } from "lucide-react";
import { api } from "@/lib/api";
import { streamRun } from "@/lib/stream";
import { titleCase } from "@/lib/format";
import RecommendationCard from "./RecommendationCard";

type Phase = "idle" | "active" | "await" | "done";

export default function AgentStream({
  agentKey,
  subjectType,
  subjectId,
  label,
  onDecided,
}: {
  agentKey: string;
  subjectType?: string;
  subjectId?: string;
  label?: string;
  onDecided?: () => void;
}) {
  const started = useRef(false);
  const [agentName, setAgentName] = useState<string>(titleCase(agentKey));
  const [tier, setTier] = useState<string>("");
  const [sense, setSense] = useState<Phase>("idle");
  const [senseDetail, setSenseDetail] = useState("");
  const [reason, setReason] = useState<Phase>("idle");
  const [reasonLabel, setReasonLabel] = useState("Reasoning over the client brain");
  const [check, setCheck] = useState<Phase>("idle");
  const [checkDetail, setCheckDetail] = useState("Suitability vs mandate · fair-conduct · pre-trade compliance");
  const [checkFlagged, setCheckFlagged] = useState(false);
  const [decide, setDecide] = useState<Phase>("idle");
  const [act, setAct] = useState<Phase>("idle");
  const [rationale, setRationale] = useState("");
  const [empty, setEmpty] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [rec, setRec] = useState<any>(null);
  const [autonomous, setAutonomous] = useState(false);
  const [pending, setPending] = useState(false);
  const [finished, setFinished] = useState(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    streamRun({ agent_key: agentKey, subject_type: subjectType, subject_id: subjectId }, (ev) => {
      switch (ev.phase) {
        case "start": setAgentName(ev.agent); setTier(ev.tier); break;
        case "sense":
          if (ev.status === "start") setSense("active");
          else { setSense("done"); setSenseDetail(ev.detail || ""); }
          break;
        case "think":
          if (ev.status === "start") { setReason("active"); setReasonLabel(ev.label || reasonLabel); }
          // reason completes when the rationale is fully written (below)
          break;
        case "rationale":
          if (ev.status === "done") setReason("done");
          else if (ev.chunk) setRationale((r) => r + ev.chunk);
          break;
        case "check":
          if (ev.status === "start") { setReason("done"); setCheck("active"); }
          else { setCheck("done"); setCheckDetail(ev.detail || checkDetail); setCheckFlagged((ev.flags || 0) > 0); }
          break;
        case "surveillance": setCheckFlagged(true); break;  // legacy
        case "empty":
          setReason("done"); setCheck("done"); setDecide("done"); setAct("done");
          setEmpty(ev.summary); setFinished(true);
          break;
        case "done":
          setCheck("done"); setAutonomous(!!ev.autonomous); setPending(!!ev.pending); setFinished(true);
          if (ev.autonomous) { setDecide("done"); setAct("done"); }
          else { setDecide("await"); setAct("idle"); }   // Tier-2: paused at the human gate
          if (ev.recommendation_id) {
            api(`/api/studio/recommendations/${ev.recommendation_id}`).then(setRec).catch(() => {});
          }
          break;
        case "error": setError(ev.message || "Run failed"); setFinished(true); break;
      }
    })
      .catch((e) => setError(e.message))
      .finally(() => setFinished(true));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const running = !finished;
  const loadingRec = finished && !rec && !empty && !error;
  const decided = rec && rec.status !== "proposed";

  // When the adviser decides, complete the Decide & Act steps.
  useEffect(() => {
    if (decided) { setDecide("done"); setAct("done"); }
  }, [decided]);

  const decideDetail = autonomous ? "Auto-approved within policy"
    : decided ? `${titleCase(rec.status)} by you`
    : pending ? "Awaiting your approval — approve / modify / dismiss"
    : "Adviser reviews";
  const actDetail = autonomous || decided ? "Routed to the OMS · written to the decision ledger"
    : "Pending your approval";

  return (
    <div className="card overflow-hidden">
      <div className="p-4">
        <div className="flex items-center gap-2">
          <span className="h-8 w-8 rounded-lg bg-navy-800 text-white flex items-center justify-center">
            {running ? <Loader2 size={16} className="animate-spin" /> : <Sparkles size={16} />}
          </span>
          <div>
            <div className="font-semibold text-ink text-sm flex items-center gap-2">
              {agentName}
              {running && <span className="inline-flex items-center gap-1 text-xs text-navy-500"><span className="h-1.5 w-1.5 rounded-full bg-positive animate-pulse" /> working</span>}
              {autonomous && <span className="chip bg-[#efe2f0] text-[#6b4b78]"><Zap size={11} /> acted autonomously</span>}
            </div>
            {label && <div className="text-xs text-ink-muted">{label}</div>}
          </div>
        </div>

        {/* sense → reason → check → decide → act */}
        <div className="mt-3 space-y-1.5">
          <Step icon={Brain} state={sense} label="Sense" detail={senseDetail || "Gathering signals from the brain"} />
          <Step icon={Cpu} state={reason} label="Reason" detail={reasonLabel} />
          <Step icon={ShieldCheck} state={check} label="Check" detail={checkDetail} flagged={checkFlagged} />
          <Step icon={UserCheck} state={decide} label="Decide" detail={decideDetail} />
          <Step icon={Send} state={act} label="Act" detail={empty || actDetail} />
        </div>

        {rationale && (
          <div className="mt-3 rounded-lg bg-navy-50/60 border border-navy-100 p-3">
            <div className="tile-label mb-1">Reasoning</div>
            <p className="text-sm text-ink-soft whitespace-pre-line leading-relaxed">
              {rationale}{running && reason !== "done" && <span className="inline-block w-1.5 h-4 bg-navy-400 ml-0.5 animate-pulse align-middle" />}
            </p>
          </div>
        )}
        {loadingRec && <div className="mt-2 text-xs text-ink-muted flex items-center gap-1"><Loader2 size={12} className="animate-spin" /> Recording to the ledger…</div>}
        {empty && <div className="mt-2 text-sm text-ink-muted">{empty}</div>}
        {error && <div className="mt-2 text-sm text-critical">{error}</div>}
      </div>

      {rec && (
        <div className="border-t border-navy-100/70 p-2">
          <RecommendationCard rec={rec} defaultOpen onDecided={(u) => {
            setRec(u); onDecided?.();
            if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent("aurea:decided"));
          }} />
        </div>
      )}
    </div>
  );
}

function Step({ icon: Icon, state, label, detail, flagged }: { icon: any; state: Phase; label: string; detail: string; flagged?: boolean }) {
  const circle =
    state === "done" ? (flagged ? "bg-caution/10 text-caution" : "bg-positive/10 text-positive")
    : state === "active" ? "bg-navy-100 text-navy-700"
    : state === "await" ? "bg-caution/10 text-caution"
    : "bg-navy-50 text-navy-400";
  return (
    <div className={`flex items-center gap-2.5 ${state === "idle" ? "opacity-45" : ""}`}>
      <span className={`h-6 w-6 rounded-md flex items-center justify-center ${circle}`}>
        {state === "done" ? <Check size={13} />
          : state === "active" ? <Loader2 size={13} className="animate-spin" />
          : <Icon size={13} />}
      </span>
      <span className="text-sm font-medium text-ink w-14">{label}</span>
      <span className={`text-xs flex-1 ${state === "await" ? "text-caution" : "text-ink-muted"}`}>{detail}</span>
    </div>
  );
}
