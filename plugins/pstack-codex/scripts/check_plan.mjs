#!/usr/bin/env node
/*
Codex plan checker for pstack's Multi-phase plan playbook.

Derived from pstack 0.15.2 `skills/poteto-mode/scripts/check-plan.mjs`, which stays byte-identical
in this package. Every upstream structural check is retained with its original message: prose rules,
H1 and intro length, the "How to read this" markers, the Program checklist H3 order, the PR sub-block
order and required boxes, the verification rule on every verify block, ten numbered live lanes with a
screenshot and a pass predicate each, the four perf boxes in order, the review gate rule, the close
section and the appendices. Only evidenced host and model assumptions differ. See
docs/native-workflows.md for the mapping and its proof status.

  upstream assumption                              Codex requirement
  Ten lanes on `grok-4.6-fast-xhigh`               Ten lanes on `<model>` where <model> is the explicit
                                                   `swarm workers` entry of the model policy, or --lanes-model
  arm a `/goal`                                    call `create_goal` on the operator's explicit go on this
                                                   plan, which names the goal; a hold leaves the goal active
  `git show origin/main:pstack/...` at every tick  re-read from the pinned installed package
                                                   (`<plugin>/skills/poteto-mode/playbooks/<stem>.md`)
  30-minute terminal `/loop` or cloud-sleeper      30-minute native `automation_update` heartbeat attached
                                                   to the thread, cadence stated in words; the schedule
                                                   encoding is a tool argument and never plan text
  each live lane on its own cloud VM               unchanged. No cloud placement is exposed here, so the
                                                   boot recipe states that a lane reports blocked until its
                                                   own isolated runtime exists; a git worktree alone is
                                                   never runtime isolation
  stand a stuck lane down, dispatch at once        confirm the stop and reconcile its effects first
  (no close cleanup)                               Close the program pauses the heartbeat (`PAUSED`) and
                                                   calls `update_goal` on the verified done condition

Passing this checker verifies the plan's form. It does not certify that an isolated runtime per lane,
a goal, or a heartbeat exists on the host.

Usage: node check_plan.mjs <plan.md> [--policy <models.json>] [--lanes-model <exact-model>]
Exit 0 when the plan passes, 1 when it has problems, 2 on a usage or policy error.
The policy defaults to $PSTACK_MODEL_CONFIG, else $CODEX_HOME/pstack/models.json, else
~/.codex/pstack/models.json. The lane model is never invented: a missing policy needs --lanes-model,
and --lanes-model must agree with an explicit policy entry.
*/
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const PLUGIN_ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const PLAYBOOKS = path.join(PLUGIN_ROOT, "skills/poteto-mode/playbooks");
const LANES_ROLE = "swarm workers";
const BACKEND_EFFORTS = {
	native: ["none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"],
	claude: ["low", "medium", "high", "xhigh", "max"],
	grok: ["low", "medium", "high", "xhigh", "max"],
};
const ALIASES = ["inherit-parent", "auto"];
const TOKEN = /^[^\s\u0000-\u001f\u007f\u0085\ufeff]+(?![\s\S])/;

const RULE =
	"Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.";
const SUB_BLOCKS = [
	"Depends on.",
	"Files.",
	"Build.",
	"You see.",
	"Verify, unit.",
	"Verify, live.",
	"Verify, perf.",
	"Review gate.",
	"Merge.",
];
const PROGRAM_H3 = ["Arm the program", "Spawn owners", "PR mechanics", "Verdict and merge", "Boot recipe"];
const PINNED_PLAYBOOK = /skills\/poteto-mode\/playbooks\/([a-z0-9-]+)\.md/g;
const PROGRAM_MARKERS = [
	["create_goal", "arm the goal with the native tool on the operator's explicit go on this plan"],
	["automation_update", "arm the audit tick as a native heartbeat attached to this thread"],
	["heartbeat", "the audit tick is a native heartbeat, not a terminal loop"],
	[/30[- ]minute/, "state the audit cadence in words"],
	["status message", null],
	["pinned", "re-read the playbooks from the pinned installed package"],
	[/skills\/poteto-mode\/playbooks\/[a-z0-9-]+\.md/, "name the execution playbook by its packaged path"],
	["PAUSED", "the operator's hold pauses the heartbeat and leaves the goal active"],
	["reconcile", "confirm a stuck lane's stop and reconcile its effects before dispatching its replacement"],
];
const STALE_MARKERS = [
	["/loop", "arm the native automation_update heartbeat instead"],
	["cloud-sleeper", "no Codex cloud wake chain exists; a local root arms the native heartbeat"],
	["git show origin/main:pstack/", "re-read from the pinned installed package, not an application-repo path"],
];
const RAW_ENCODING = /RRULE:/;
const BOOT_MARKERS = [
	[/own cloud VM/, "each live lane runs on its own cloud VM or an explicitly approved isolated runtime, and a git worktree alone is not runtime isolation"],
	["blocked", "state that a lane without its own isolated runtime reports blocked instead of starting on the shared host"],
];
const BOOT_WORKTREE_PLACEMENT = /\b(in|on|into) (its|their|each|a|an) (own )?(git )?worktree\b|\bworktree add\b/i;
const BOOT_WORKTREE_EVIDENCE = [/\bports?\b/, /\bbrowsers?\b/, /\bdata\b/];
const CLOSE_MARKERS = ["update_goal", "PAUSED"];
const HOW_TO_READ_MARKERS = [
	"One box is one unit of work",
	"names the evidence",
	"Check a box only when its evidence exists",
	"playbooks/",
	RULE,
];
const PERF_ITEMS = ["Metric.", "Probe.", "Baseline.", "Rule."];
const BOX = /^\s*- \[[ x]\] (.*)$/;

class PolicyError extends Error {}

function usage(message) {
	console.error(message);
	console.error("Usage: node check_plan.mjs <plan.md> [--policy <models.json>] [--lanes-model <exact-model>]");
	process.exit(2);
}

function parseArgs(argv) {
	const options = { plan: null, policy: null, lanesModel: null };
	for (let i = 0; i < argv.length; i++) {
		const arg = argv[i];
		if (arg === "--policy" || arg === "--lanes-model") {
			const value = argv[i + 1];
			if (value === undefined || value.startsWith("--")) usage(`${arg} needs a value`);
			options[arg === "--policy" ? "policy" : "lanesModel"] = value;
			i++;
		} else if (arg.startsWith("--")) {
			usage(`unknown option ${arg}`);
		} else if (options.plan === null) {
			options.plan = arg;
		} else {
			usage(`unexpected argument ${arg}`);
		}
	}
	if (options.plan === null) usage("no plan file given");
	if (options.lanesModel !== null && (!TOKEN.test(options.lanesModel) || ALIASES.includes(options.lanesModel))) {
		usage("--lanes-model must be an exact model token, not an inheritance alias");
	}
	return options;
}

const isObject = (value) => value !== null && typeof value === "object" && !Array.isArray(value);

function expandHome(value) {
	return value === "~" || value.startsWith("~/") ? path.join(os.homedir(), value.slice(1)) : value;
}

function defaultPolicyPath(env) {
	const override = env.PSTACK_MODEL_CONFIG;
	if (override) {
		const expanded = expandHome(override);
		if (!path.isAbsolute(expanded)) throw new PolicyError("PSTACK_MODEL_CONFIG must be an absolute path");
		return expanded;
	}
	return path.join(env.CODEX_HOME ? expandHome(env.CODEX_HOME) : path.join(os.homedir(), ".codex"), "pstack/models.json");
}

function readPolicy(file) {
	let value;
	try {
		value = JSON.parse(fs.readFileSync(file, "utf8"));
	} catch (error) {
		throw new PolicyError(`cannot read model policy ${file}: ${error.message}`);
	}
	if (!isObject(value) || value.schema_version !== 1 || !isObject(value.roles)) {
		throw new PolicyError(`${file}: not a schema_version 1 pstack model policy with a roles object`);
	}
	return value;
}

function laneEntry(policy, file) {
	if (!Object.hasOwn(policy.roles, LANES_ROLE)) return null;
	const entry = policy.roles[LANES_ROLE];
	const where = `${file}: roles["${LANES_ROLE}"]`;
	if (!isObject(entry)) throw new PolicyError(`${where} must be one model entry, not a panel`);
	const backend = entry.backend;
	if (typeof backend !== "string" || !Object.hasOwn(BACKEND_EFFORTS, backend)) {
		throw new PolicyError(`${where}.backend must be native, claude, or grok`);
	}
	const model = entry.model;
	if (typeof model !== "string" || !TOKEN.test(model)) throw new PolicyError(`${where}.model must be an exact nonempty token without whitespace or controls`);
	if (ALIASES.includes(model)) {
		if (backend !== "native" || Object.hasOwn(entry, "effort")) {
			throw new PolicyError(`${where}: ${model} is a native-only alias and omits effort`);
		}
		return { alias: model, backend };
	}
	if (typeof entry.effort !== "string" || !BACKEND_EFFORTS[backend].includes(entry.effort)) {
		throw new PolicyError(`${where}.effort is missing or not a supported ${backend} token`);
	}
	return { model, backend, effort: entry.effort };
}

function resolveLanes(options, env) {
	const explicit = options.policy !== null;
	const file = explicit ? path.resolve(expandHome(options.policy)) : defaultPolicyPath(env);
	if (!fs.existsSync(file)) {
		if (explicit) throw new PolicyError(`model policy not found: ${file}`);
		if (options.lanesModel) return { model: options.lanesModel, backend: null, effort: null, source: "--lanes-model" };
		throw new PolicyError(`no model policy at ${file}; pass --policy <models.json> or --lanes-model <exact-model>`);
	}
	const entry = laneEntry(readPolicy(file), file);
	if (entry === null) {
		if (options.lanesModel) {
			return { model: options.lanesModel, backend: null, effort: null, source: `--lanes-model (${file} leaves "${LANES_ROLE}" unresolved)` };
		}
		throw new PolicyError(`${file}: roles["${LANES_ROLE}"] is unresolved; configure it or pass --lanes-model <exact-model>`);
	}
	if (entry.alias) {
		if (options.lanesModel) {
			return { model: options.lanesModel, backend: entry.backend, effort: null, source: `--lanes-model (${file} sets "${LANES_ROLE}" to ${entry.alias})` };
		}
		throw new PolicyError(`${file}: "${LANES_ROLE}" is ${entry.alias}; the lanes need an exact identity, pass --lanes-model <parent model>`);
	}
	if (options.lanesModel && options.lanesModel !== entry.model) {
		throw new PolicyError(`--lanes-model ${options.lanesModel} disagrees with ${file} "${LANES_ROLE}" ${entry.model}`);
	}
	return { ...entry, source: file };
}

const options = parseArgs(process.argv.slice(2));
let lanePolicy;
try {
	lanePolicy = resolveLanes(options, process.env);
} catch (error) {
	if (error instanceof PolicyError) usage(error.message);
	throw error;
}
const LANES = `Ten lanes on \`${lanePolicy.model}\` at the PR head`;

const file = options.plan;
let raw;
try {
	raw = fs.readFileSync(file, "utf8").split(/\r?\n/);
} catch (error) {
	usage(`cannot read plan ${file}: ${error.message}`);
}
const problems = [];
const fail = (line, message) => problems.push(`${file}:${line}: ${message}`);

let start = 0;
if (raw[0] === "---") {
	start = raw.indexOf("---", 1) + 1;
}

const lines = [];
let fence = false;
for (let i = start; i < raw.length; i++) {
	const text = raw[i];
	const n = i + 1;
	if (/^```/.test(text)) fence = !fence;
	lines.push({ n, text, code: fence });
	if (RAW_ENCODING.test(text)) fail(n, "raw RRULE string; state the cadence in words and keep the schedule encoding in the automation_update arguments");
	if (fence) continue;
	const prose = text
		.replace(/`[^`]*`/g, "`")
		.replace(/!\[[^\]]*\]\([^)]*\)/g, "")
		.replace(/\]\([^)]*\)/g, "]");
	if (/[–—]/.test(prose)) fail(n, "long dash");
	if (/[‘’“”]/.test(prose)) fail(n, "curly quote");
	if (/: \S/.test(prose)) fail(n, "mid-sentence colon");
}

const h2 = (l) => (!l.code && l.text.startsWith("## ") ? l.text.slice(3).trim() : null);
const sections = [];
for (const l of lines) {
	const title = h2(l);
	if (title !== null) sections.push({ title, n: l.n, body: [] });
	else if (sections.length) sections.at(-1).body.push(l);
}
const find = (title) => sections.find((s) => s.title === title);
const bodyText = (s) => s.body.map((l) => l.text).join("\n");
const boxes = (ls) => ls.filter((l) => !l.code && BOX.test(l.text)).map((l) => ({ n: l.n, text: l.text.match(BOX)[1] }));
const h3Body = (s, name) => {
	const out = [];
	let inside = false;
	for (const l of s.body) {
		if (!l.code && l.text.startsWith("### ")) {
			inside = l.text.slice(4).trim().startsWith(name);
			continue;
		}
		if (inside) out.push(l);
	}
	return out.map((l) => l.text).join("\n");
};
const lacks = (marker, remedy) => `lacks "${marker}"${remedy ? `; ${remedy}` : ""}`;

const h1 = lines.findIndex((l) => !l.code && l.text.startsWith("# "));
if (h1 === -1) fail(1, "no H1 title");
const howToRead = find("How to read this");
if (!howToRead) fail(1, 'no "## How to read this" section');
if (h1 !== -1 && howToRead) {
	const intro = lines.slice(h1 + 1).filter((l) => l.n < howToRead.n && l.text.trim() !== "");
	if (intro.length >= 10) fail(lines[h1].n, `intro is ${intro.length} lines, under ten required`);
	for (const marker of HOW_TO_READ_MARKERS) {
		if (!bodyText(howToRead).includes(marker)) fail(howToRead.n, `How to read this lacks "${marker}"`);
	}
}

const program = find("Program checklist");
if (!program) fail(1, 'no "## Program checklist" section');
else {
	const h3s = program.body.filter((l) => !l.code && l.text.startsWith("### ")).map((l) => l.text.slice(4).trim());
	let cursor = 0;
	for (const name of PROGRAM_H3) {
		const at = h3s.findIndex((t, i) => i >= cursor && t.startsWith(name));
		if (at === -1) fail(program.n, `Program checklist lacks "### ${name}" in order`);
		else cursor = at + 1;
	}
	const text = bodyText(program);
	for (const [marker, remedy] of PROGRAM_MARKERS) {
		const ok = marker instanceof RegExp ? marker.test(text) : text.includes(marker);
		if (!ok) fail(program.n, `Program checklist ${lacks(marker, remedy)}`);
	}
	for (const [marker, remedy] of STALE_MARKERS) {
		if (text.includes(marker)) fail(program.n, `Program checklist still uses Cursor "${marker}"; ${remedy}`);
	}
	for (const box of boxes(program.body)) {
		if (/\bhold\b/i.test(box.text) && box.text.includes("update_goal")) {
			fail(box.n, "Program checklist closes the goal on the operator's hold; a hold pauses the heartbeat and leaves the goal active");
		}
	}
	if (!fs.existsSync(PLAYBOOKS)) fail(program.n, `packaged playbooks not found at ${PLAYBOOKS}; run the checker from the installed package`);
	else {
		const seen = new Set();
		for (const match of text.matchAll(PINNED_PLAYBOOK)) {
			const stem = match[1];
			if (seen.has(stem)) continue;
			seen.add(stem);
			if (!fs.existsSync(path.join(PLAYBOOKS, `${stem}.md`))) fail(program.n, `Program checklist names playbook "${stem}", which the pinned package does not contain`);
		}
	}
	const boot = h3Body(program, "Boot recipe");
	for (const [marker, remedy] of BOOT_MARKERS) {
		const ok = marker instanceof RegExp ? marker.test(boot) : boot.includes(marker);
		if (!ok) fail(program.n, `Boot recipe ${lacks(marker, remedy)}`);
	}
	if (BOOT_WORKTREE_PLACEMENT.test(boot) && !BOOT_WORKTREE_EVIDENCE.every((word) => word.test(boot))) {
		fail(program.n, "Boot recipe places a lane in a worktree without separate port, browser, and data evidence; a worktree alone is not runtime isolation");
	}
}

const close = find("Close the program");
if (!close) fail(1, 'no "## Close the program" section');
else {
	for (const marker of CLOSE_MARKERS) {
		if (!bodyText(close).includes(marker)) fail(close.n, `Close the program lacks "${marker}"; pause the heartbeat and close the goal on the verified done condition`);
	}
}
const programIndex = sections.indexOf(program);
const closeIndex = sections.indexOf(close);
const prSections = programIndex === -1 || closeIndex === -1 ? [] : sections.slice(programIndex + 1, closeIndex);
if (prSections.length === 0) fail(1, "no PR sections between Program checklist and Close the program");

const report = [];
for (const pr of prSections) {
	const heads = [];
	for (const l of pr.body) {
		if (l.code) continue;
		const m = l.text.match(/^\*\*([^*]+)\*\*(.*)$/);
		if (m && SUB_BLOCKS.includes(m[1])) heads.push({ name: m[1], n: l.n, rest: m[2].trim(), lines: [] });
		else if (heads.length) heads.at(-1).lines.push(l);
	}
	const names = heads.map((h) => h.name);
	if (names.join("|") !== SUB_BLOCKS.join("|")) {
		fail(pr.n, `${pr.title}: sub-blocks are [${names.join(", ")}], expected [${SUB_BLOCKS.join(", ")}]`);
	}
	const block = (name) => heads.find((h) => h.name === name);
	const counts = {};
	for (const h of heads) counts[h.name] = boxes(h.lines).length;

	const depends = block("Depends on.");
	if (depends && depends.rest === "") fail(depends.n, `${pr.title}: Depends on names nothing`);
	for (const name of ["Files.", "Build.", "You see.", "Verify, unit.", "Merge."]) {
		const b = block(name);
		if (b && boxes(b.lines).length === 0) fail(b.n, `${pr.title}: ${name} has no box`);
	}
	for (const name of ["Verify, unit.", "Verify, live.", "Verify, perf."]) {
		const b = block(name);
		if (b && !b.rest.startsWith(RULE)) fail(b.n, `${pr.title}: ${name} does not open with the rule`);
	}

	const live = block("Verify, live.");
	if (live) {
		if (!live.rest.includes(LANES)) fail(live.n, `${pr.title}: Verify, live lacks "${LANES}"`);
		const lanes = boxes(live.lines).map((b) => ({ ...b, m: b.text.match(/^Lane (\d+)\. /) }));
		const numbers = lanes.filter((b) => b.m).map((b) => Number(b.m[1])).sort((a, b) => a - b);
		if (numbers.join(",") !== "1,2,3,4,5,6,7,8,9,10") fail(live.n, `${pr.title}: lanes are [${numbers.join(",")}], expected 1 to 10`);
		for (const lane of lanes) {
			if (!lane.m) fail(lane.n, `${pr.title}: live box is not a lane`);
			else if (!/Save `[^`]+`/.test(lane.text)) fail(lane.n, `${pr.title}: lane ${lane.m[1]} names no screenshot`);
			else if (!lane.text.includes("Pass when")) fail(lane.n, `${pr.title}: lane ${lane.m[1]} has no pass predicate`);
		}
	}

	const perf = block("Verify, perf.");
	if (perf) {
		const items = boxes(perf.lines).map((b) => b.text.split(" ")[0]);
		if (items.join("|") !== PERF_ITEMS.join("|")) fail(perf.n, `${pr.title}: perf boxes are [${items.join(", ")}], expected [${PERF_ITEMS.join(", ")}]`);
	}

	const gate = block("Review gate.");
	if (gate) {
		const gateBoxes = boxes(gate.lines);
		if (gate.rest.startsWith("None.")) {
			if (gateBoxes.length) fail(gate.n, `${pr.title}: Review gate says None but has boxes`);
		} else {
			const text = gate.lines.map((l) => l.text).join("\n");
			if (gateBoxes.length === 0) fail(gate.n, `${pr.title}: Review gate has no box`);
			for (const word of ["screenshot", "video", "operator"]) {
				if (!text.includes(word)) fail(gate.n, `${pr.title}: Review gate lacks "${word}"`);
			}
		}
	}

	const total = boxes(pr.body).length;
	const cells = SUB_BLOCKS.filter((s) => s !== "Depends on.").map((s) => `${s.replace(/[ ,.]+/g, "-").replace(/-$/, "").toLowerCase()}=${counts[s] ?? 0}`);
	report.push(`${pr.title}  boxes=${total}  ${cells.join(" ")}`);
}

if (closeIndex !== -1) {
	const tail = sections.slice(closeIndex + 1);
	for (const s of tail) {
		if (!s.title.startsWith("Appendix")) fail(s.n, `"## ${s.title}" after Close the program is not an appendix`);
	}
	if (!tail.some((s) => s.title.includes("Prototype evidence"))) fail(close.n, 'no "## Appendix ... Prototype evidence" section');
}

for (const line of report) console.log(line);
console.log(`lanes  model=${lanePolicy.model} backend=${lanePolicy.backend ?? "unspecified"} effort=${lanePolicy.effort ?? "unspecified"} source=${lanePolicy.source}`);
console.log("runtime  per-lane isolated runtime, goal, and heartbeat are host prerequisites this checker does not certify");
console.log(`${prSections.length} PR sections, ${problems.length} problems`);
for (const p of problems) console.error(p);
process.exit(problems.length ? 1 : 0);
