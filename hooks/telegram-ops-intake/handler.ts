import { execFile } from "node:child_process";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);
const INTAKE = "/home/aaron/.openclaw/scripts/operator_event.py";

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

export default async function handler(event: {
  type: string;
  action: string;
  sessionKey?: string;
  context?: unknown;
  messages: string[];
}) {
  if (event.type !== "message" || event.action !== "sent") return;
  const context = asRecord(event.context);
  if (context.channelId !== "telegram" || context.success !== true) return;
  const content = typeof context.content === "string" ? context.content.trim() : "";
  if (!content) return;
  const target = typeof context.to === "string" ? context.to : String(context.to || "");
  try {
    await execFileAsync(
      "/usr/bin/python3",
      [
        INTAKE, "ingest", "--content", content,
        "--source", "openclaw-message-sent", "--channel", "telegram",
        "--target", target, "--session-key", event.sessionKey || "",
      ],
      { timeout: 3_000, maxBuffer: 256 * 1024 },
    );
  } catch (error) {
    console.warn(`[telegram-ops-intake] durable intake failed: ${String(error)}`);
  }
}
