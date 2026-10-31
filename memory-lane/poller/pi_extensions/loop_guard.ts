// Vendored, unchanged below this header: pi-death-loop-guard 0.1.2 (npm, MIT, tyanyin),
// extensions/loop-guard.ts; tarball sha512-vUPRsFkStuyCN3t+YzB013X8harxyfzIoJhjFO+yZRAhAir3zq9HLfyqHO2ywfs479Adh4d/a5lnPyOVzMsANQ==
// Its licence is loop_guard.LICENSE beside this file. Copied rather than depended on (31 Oct,
// #interfaces): one maintainer, no public source, and it runs inside every Pi turn with our tools,
// so its code shows in our diffs. pi_harness loads it with `pi -e` on every wake.

/**
 * pi-loop-guard — anti-repetition-loop guard extension for pi-coding-agent.
 *
 * Detection: N consecutive tool calls where (toolName + argument fingerprint +
 * result fingerprint) are all identical are treated as a death loop.
 *
 * Escalation: warnThreshold matches -> inject a steer warning message;
 *             blockThreshold matches -> block the tool call in the tool_call
 *             event (the model sees an error result);
 *             abortThreshold blocks within one user request -> ctx.abort().
 *
 * Design: docs/design.md (finalized after four review rounds).
 *
 * Types: pi loads .ts extensions directly via jiti; the entry point is
 * `export default function(pi)`. This file deliberately does not import any pi
 * package types (package names differ across distributions). The minimal
 * interfaces below mirror pi >= 0.84.1 dist/core/extensions/types.d.ts.
 */

import { createHash } from "node:crypto";

// ============================================================================
// Minimal self-declared extension API types (pi >= 0.84.1)
// ============================================================================

interface TextContent {
	type: "text";
	text: string;
}

interface ImageContent {
	type: "image";
	image?: string;
	url?: string;
	mimeType?: string;
}

interface ToolCallEvent {
	type: "tool_call";
	toolCallId: string;
	toolName: string;
	input: Record<string, unknown>;
}

interface ToolResultEvent {
	type: "tool_result";
	toolCallId: string;
	toolName: string;
	input: Record<string, unknown>;
	content: (TextContent | ImageContent)[];
	isError: boolean;
}

interface ToolCallEventResult {
	block?: boolean;
	reason?: string;
	/** Requires pi >= 0.84.1 (PR #7715); set only when useTerminate=true */
	terminate?: boolean;
}

interface BeforeAgentStartEvent {
	type: "before_agent_start";
	prompt: string;
	systemPrompt: string;
}

interface ExtensionContext {
	cwd: string;
	abort(): void;
	signal?: AbortSignal;
	isIdle(): boolean;
}

interface ExtensionAPI {
	on(event: "tool_call", handler: (event: ToolCallEvent, ctx: ExtensionContext) => ToolCallEventResult | void): void;
	on(event: "tool_result", handler: (event: ToolResultEvent, ctx: ExtensionContext) => void): void;
	on(event: "before_agent_start", handler: (event: BeforeAgentStartEvent, ctx: ExtensionContext) => void): void;
	sendMessage(
		message: { customType: string; content: string | (TextContent | ImageContent)[]; display: boolean },
		options?: { triggerTurn?: boolean; deliverAs?: "steer" | "followUp" | "nextTurn" },
	): void;
}

// ============================================================================
// Configuration (env vars LOOP_GUARD_*, defaults 3/5/3)
// ============================================================================

interface Config {
	enabled: boolean;
	warnThreshold: number;
	blockThreshold: number;
	abortThreshold: number;
	ignoreTools: Set<string>;
	ignoreErrors: boolean;
	useTerminate: boolean;
}

const DEFAULT_WARN = 3;
const DEFAULT_BLOCK = 5;
const DEFAULT_ABORT = 3;

function envInt(name: string, def: number): number {
	const raw = process.env[name];
	if (raw === undefined || raw === "") return def;
	const n = Number(raw);
	return Number.isInteger(n) && n >= 1 ? n : def;
}

function envBool(name: string, def: boolean): boolean {
	const raw = process.env[name];
	if (raw === undefined || raw === "") return def;
	const v = raw.trim().toLowerCase();
	return v === "1" || v === "true" || v === "yes" || v === "on";
}

function loadConfig(): Config {
	const cfg: Config = {
		enabled: envBool("LOOP_GUARD_ENABLED", true),
		warnThreshold: envInt("LOOP_GUARD_WARN_THRESHOLD", DEFAULT_WARN),
		blockThreshold: envInt("LOOP_GUARD_BLOCK_THRESHOLD", DEFAULT_BLOCK),
		abortThreshold: envInt("LOOP_GUARD_ABORT_THRESHOLD", DEFAULT_ABORT),
		ignoreTools: new Set(
			(process.env.LOOP_GUARD_IGNORE_TOOLS ?? "")
				.split(",")
				.map((s) => s.trim())
				.filter(Boolean),
		),
		ignoreErrors: envBool("LOOP_GUARD_IGNORE_ERRORS", false),
		useTerminate: envBool("LOOP_GUARD_USE_TERMINATE", false),
	};
	if (cfg.warnThreshold >= cfg.blockThreshold) {
		// Config invariant (design.md §5.4): warn must fire before block
		console.error(
			`[loop-guard] invalid config: LOOP_GUARD_WARN_THRESHOLD (${cfg.warnThreshold}) must be < LOOP_GUARD_BLOCK_THRESHOLD (${cfg.blockThreshold}); disabling`,
		);
		cfg.enabled = false;
	}
	return cfg;
}

// ============================================================================
// Fingerprints
// ============================================================================

/** Recursively sort all object keys so field order is irrelevant (design.md §5.3) */
function sortKeysRecursive(value: unknown): unknown {
	if (Array.isArray(value)) return value.map(sortKeysRecursive);
	if (value !== null && typeof value === "object") {
		const out: Record<string, unknown> = {};
		for (const k of Object.keys(value as Record<string, unknown>).sort()) {
			out[k] = sortKeysRecursive((value as Record<string, unknown>)[k]);
		}
		return out;
	}
	return value;
}

/** Argument fingerprint: recursive key sort then JSON serialization */
function argsFingerprint(input: Record<string, unknown>): string {
	return JSON.stringify(sortKeysRecursive(input));
}

/**
 * Result fingerprint: full streaming SHA-256 (no truncation).
 * Only TextContent.text is hashed; images count only (skipping base64 is a
 * cost tradeoff, design.md §6). Concatenation:
 * sha256(toolName + args + textContents... + #img:n), three segments to avoid
 * comparing long strings.
 */
function resultFingerprint(event: ToolResultEvent): string {
	const textParts: string[] = [];
	let imageCount = 0;
	for (const c of event.content) {
		if (c.type === "text") textParts.push(c.text);
		else imageCount++;
	}
	const h = createHash("sha256");
	h.update(event.toolName);
	h.update("\u0000");
	h.update(argsFingerprint(event.input));
	h.update("\u0000");
	for (const t of textParts) h.update(t).update("\u0001");
	h.update(`#img:${imageCount}`);
	return h.digest("hex");
}

/** Tool-level key: toolName + argument fingerprint */
function callKey(event: ToolCallEvent): string {
	return `${event.toolName}\u0000${argsFingerprint(event.input)}`;
}

// ============================================================================
// State and main logic
// ============================================================================

interface LoopState {
	lastKey: string | null;
	lastToolName: string | null;
	lastResultFp: string | null;
	streak: number;
	warned: boolean;
	blockAbortCount: number;
	/** Parallel-batch association: toolCallId -> key (toolCallId is unique per call; used only to link call<->result, never to store streak) */
	pending: Map<string, string>;
	/** Results collected for the current batch; at batch end the last one is compared against the previous serial result (design.md §5.2 rule 2) */
	batchResults: { key: string; fp: string }[];
}

function freshState(): LoopState {
	return {
		lastKey: null,
		lastToolName: null,
		lastResultFp: null,
		streak: 0,
		warned: false,
		blockAbortCount: 0,
		pending: new Map(),
		batchResults: [],
	};
}

export default function loopGuard(pi: ExtensionAPI): void {
	const cfg = loadConfig();
	if (!cfg.enabled) return;
	const state = freshState();

	function sendWarning(): void {
		if (state.lastToolName === null) return;
		pi.sendMessage(
			{
				customType: "loop-guard-warning",
				content:
					`loop-guard: you have called the same tool \`${state.lastToolName}(…)\` with identical arguments ` +
					`${cfg.warnThreshold} times and the result is unchanged; this looks like a stuck loop. ` +
					`Stop repeating it, change your approach, or give a final answer.`,
				display: true,
			},
			{ deliverAs: "steer" },
		);
	}

	function updateStreak(key: string, toolName: string, fp: string): void {
		if (key === state.lastKey && fp === state.lastResultFp) {
			state.streak++;
		} else {
			state.lastKey = key;
			state.lastToolName = toolName;
			state.lastResultFp = fp;
			state.streak = 1;
			state.warned = false;
		}
		if (state.streak === cfg.warnThreshold && !state.warned) {
			state.warned = true;
			sendWarning();
		}
	}

	pi.on("tool_call", (event, ctx) => {
		if (cfg.ignoreTools.has(event.toolName)) return;
		const key = callKey(event);
		// Block decision: same key and the completed streak already reached
		// blockThreshold-1 (allow N-1 executions, block the Nth)
		if (key === state.lastKey && state.streak >= cfg.blockThreshold - 1) {
			state.blockAbortCount++;
			const result: ToolCallEventResult = {
				block: true,
				reason:
					`loop-guard: ${cfg.blockThreshold} consecutive identical tool calls ` +
					`(the first ${cfg.blockThreshold - 1} produced identical results); blocked. ` +
					`Change your approach or summarize directly.`,
			};
			if (cfg.useTerminate) result.terminate = true;
			if (state.blockAbortCount >= cfg.abortThreshold) {
				ctx.abort();
			}
			return result;
		}
		// Register the in-flight call (blocked calls take the immediate branch
		// and never emit a tool_result event, so nothing leaks here)
		state.pending.set(event.toolCallId, key);
	});

	pi.on("tool_result", (event) => {
		if (cfg.ignoreTools.has(event.toolName)) return;
		const key = state.pending.get(event.toolCallId);
		state.pending.delete(event.toolCallId);
		if (key === undefined) return;
		// ignoreErrors: error results are skipped and the streak is unchanged (design.md §5.4)
		if (cfg.ignoreErrors && event.isError) return;
		state.batchResults.push({ key, fp: resultFingerprint(event) });
		if (state.pending.size > 0) return; // parallel batch not finished yet
		// Batch finished: compare the last result against the previous serial
		// result (intermediate results never advance the streak)
		const last = state.batchResults[state.batchResults.length - 1];
		state.batchResults = [];
		updateStreak(last.key, event.toolName, last.fp);
	});

	pi.on("before_agent_start", () => {
		// Each user request resets the block counter (abortThreshold boundary)
		// and the loop sequence
		state.blockAbortCount = 0;
		state.lastKey = null;
		state.lastToolName = null;
		state.lastResultFp = null;
		state.streak = 0;
		state.warned = false;
		state.pending.clear();
		state.batchResults = [];
	});
}
