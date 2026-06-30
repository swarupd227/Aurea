import Shell from "@/components/Shell";
import { AgentConsoleProvider } from "@/components/AgentConsole";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <AgentConsoleProvider>
      <Shell>{children}</Shell>
    </AgentConsoleProvider>
  );
}
