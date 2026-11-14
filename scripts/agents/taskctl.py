#!/usr/bin/env python3
"""Task board and gate tool for the improvement plan (docs/improvements/).

Deterministic gates so that the orchestrator (Haiku) and the middle agents never improvise gh commands.
Stdlib only. Run from the repository root with any Python >= 3.9:

    python3 scripts/agents/taskctl.py validate                 # G0: task files are consistent
    python3 scripts/agents/taskctl.py order                    # execution order, parallel batches
    python3 scripts/agents/taskctl.py board-init [--dry-run]   # create/refresh the GitHub Project and issues
    python3 scripts/agents/taskctl.py board-check              # G1
    python3 scripts/agents/taskctl.py status                   # board as a table
    python3 scripts/agents/taskctl.py ready                    # G2: what may be dispatched now
    python3 scripts/agents/taskctl.py set T05 "In Progress" --agent middle-sub:T05 [--pr 12] [--note "..."]
    python3 scripts/agents/taskctl.py check-pr T05 12 [--no-tests]   # G5
    python3 scripts/agents/taskctl.py check-pr chunk:B-lookup-paths 80   # G5 for a chunk PR (one commit per task)
    python3 scripts/agents/taskctl.py sync                     # G7: merged PRs -> Done, unblocked -> Ready

Exit code 0 = gate passed / command succeeded, 1 = gate failed, 2 = usage or environment error.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[2]
TASKS_DIR = ROOT / "docs" / "improvements" / "tasks"
OWNER = os.environ.get("TASKCTL_OWNER", "bbetter")
REPO = os.environ.get("TASKCTL_REPO", "bbetter/cs2-scoreboard-watch")
PROJECT_TITLE = os.environ.get("TASKCTL_PROJECT", "cs2-scoreboard-watch")
LABEL = "improvement-plan"
STATUSES = ["Todo", "Ready", "In Progress", "Review", "Done", "Blocked"]
STATUS_COLORS = {"Todo": "GRAY", "Ready": "BLUE", "In Progress": "YELLOW", "Review": "PURPLE", "Done": "GREEN",
                 "Blocked": "RED"}
TEXT_FIELDS = ["Task", "Agent", "Depends", "PR", "Chunk"]
NUMBER_FIELDS = ["Wave"]
REQUIRED_KEYS = ["id", "title", "wave", "depends_on", "review", "size", "golden", "files"]
APPROVAL_PHRASE = "AUTO_MERGE - approved by"
FORBIDDEN_PATHS = ["data/*", ".env", ".env.*"]
PR_BODY_SECTIONS = ["## Summary", "## Verification", "## Docs delta"]
# Checks that are non-blocking on main (continue-on-error in .github/workflows/tests.yml, PR #30).
# A failed run of these does not fail the CI-green gate. Pending runs still block.
NON_BLOCKING_CHECKS = {"unittest (Python 3.13)"}


def _main_checkout() -> Path:
    """The primary checkout (owner of .venv), also when running from a linked worktree."""
    try:
        common = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=ROOT,
                                capture_output=True, text=True, check=True).stdout.strip()
        return Path(common).parent
    except (OSError, subprocess.CalledProcessError):
        return ROOT


VENV_PY = _main_checkout() / ".venv" / "bin" / "python"


class GateError(Exception):
    pass


# ----------------------------------------------------------------------------------------------- task files

@dataclass
class Task:
    id: str
    title: str
    wave: int
    depends_on: list[str]
    review: str
    size: str
    golden: list[str]
    files: list[str]
    path: Path
    body: str = ""
    chunk: str = ""
    deps_closure: set[str] = field(default_factory=set)


def _parse_scalar(raw: str) -> Any:
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        return [x.strip() for x in inner.split(",") if x.strip()] if inner else []
    if re.fullmatch(r"-?\d+", raw):
        return int(raw)
    return raw


def parse_task(path: Path) -> Task:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    if not m:
        raise GateError(f"{path.name}: missing front matter")
    meta: dict[str, Any] = {}
    current_list: Optional[str] = None
    for line in m.group(1).splitlines():
        if not line.strip():
            continue
        item = re.match(r"^\s+-\s+(.*)$", line)
        if item and current_list:
            meta[current_list].append(item.group(1).strip())
            continue
        kv = re.match(r"^([a-z_]+):\s*(.*)$", line)
        if not kv:
            raise GateError(f"{path.name}: cannot parse front matter line {line!r}")
        key, val = kv.group(1), kv.group(2)
        if val == "":
            meta[key] = []
            current_list = key
        else:
            meta[key] = _parse_scalar(val)
            current_list = None
    missing = [k for k in REQUIRED_KEYS if k not in meta]
    if missing:
        raise GateError(f"{path.name}: missing keys {missing}")
    for k in ("depends_on", "golden", "files"):
        if not isinstance(meta[k], list):
            raise GateError(f"{path.name}: {k} must be a list")
    return Task(id=str(meta["id"]), title=str(meta["title"]), wave=int(meta["wave"]),
                depends_on=list(meta["depends_on"]), review=str(meta["review"]), size=str(meta["size"]),
                golden=list(meta["golden"]), files=list(meta["files"]), path=path, body=m.group(2),
                chunk=str(meta.get("chunk") or ""))


def load_tasks() -> dict[str, Task]:
    tasks: dict[str, Task] = {}
    for path in sorted(TASKS_DIR.glob("T*.md")):
        t = parse_task(path)
        if t.id in tasks:
            raise GateError(f"duplicate task id {t.id}")
        if not path.name.startswith(t.id + "-"):
            raise GateError(f"{path.name}: file name must start with '{t.id}-'")
        tasks[t.id] = t
    if not tasks:
        raise GateError(f"no tasks in {TASKS_DIR}")
    for t in tasks.values():
        for d in t.depends_on:
            if d not in tasks:
                raise GateError(f"{t.id}: unknown dependency {d}")
    _compute_closures(tasks)
    return tasks


def _compute_closures(tasks: dict[str, Task]) -> None:
    state: dict[str, int] = {}

    def visit(tid: str, stack: list[str]) -> set[str]:
        if state.get(tid) == 1:
            raise GateError("dependency cycle: " + " -> ".join(stack + [tid]))
        if state.get(tid) == 2:
            return tasks[tid].deps_closure
        state[tid] = 1
        closure: set[str] = set()
        for d in tasks[tid].depends_on:
            closure.add(d)
            closure |= visit(d, stack + [tid])
        tasks[tid].deps_closure = closure
        state[tid] = 2
        return closure

    for tid in tasks:
        visit(tid, [])


def chunk_map(tasks: dict[str, Task]) -> dict[str, list[Task]]:
    """chunk name -> member tasks in dependency order. A chunk is one PR with one commit per member task."""
    out: dict[str, list[Task]] = {}
    for t in tasks.values():
        if t.chunk:
            out.setdefault(t.chunk, []).append(t)
    for members in out.values():
        members.sort(key=lambda t: (len(t.deps_closure), t.id))  # a dependent always has a larger closure
    return out


def resolve_tasks(spec: str, tasks: dict[str, Task]) -> list[Task]:
    """'T05', 'T05,T06' or 'chunk:<name>' -> tasks in dependency order."""
    if spec.startswith("chunk:"):
        members = chunk_map(tasks).get(spec[len("chunk:"):])
        if not members:
            raise GateError(f"unknown chunk {spec}")
        return members
    ids = [x.strip() for x in spec.split(",") if x.strip()]
    unknown = [i for i in ids if i not in tasks]
    if unknown or not ids:
        raise GateError(f"unknown task(s) {unknown or spec!r}")
    return sorted((tasks[i] for i in ids), key=lambda t: (len(t.deps_closure), t.id))


_PR_TITLE_TASKS = re.compile(r"^\[(T\d+(?:\+T\d+)*)\] ")


def pr_title_tasks(title: str) -> list[str]:
    m = _PR_TITLE_TASKS.match(title or "")
    return m.group(1).split("+") if m else []


def _patterns_overlap(a: str, b: str) -> bool:
    return a == b or fnmatch.fnmatch(a, b) or fnmatch.fnmatch(b, a)


def _file_allowed(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, p) for p in patterns)


def topo_levels(tasks: dict[str, Task]) -> list[list[Task]]:
    """Level n = tasks whose longest dependency chain has length n. Tasks of one level are independent."""
    depth: dict[str, int] = {}

    def d(tid: str) -> int:
        if tid not in depth:
            depth[tid] = 0 if not tasks[tid].depends_on else 1 + max(d(x) for x in tasks[tid].depends_on)
        return depth[tid]

    levels: dict[int, list[Task]] = {}
    for tid in tasks:
        levels.setdefault(d(tid), []).append(tasks[tid])
    return [sorted(levels[k], key=lambda t: t.id) for k in sorted(levels)]


def cmd_validate(_args) -> int:
    tasks = load_tasks()
    errors: list[str] = []
    for t in tasks.values():
        for dep in t.depends_on:
            if tasks[dep].wave > t.wave:
                errors.append(f"{t.id} (wave {t.wave}) depends on {dep} from a later wave {tasks[dep].wave}")
        for g in t.golden:
            if not _file_allowed(g, t.files):
                errors.append(f"{t.id}: golden file {g} is not in files")
        if t.review not in ("none", "lead"):
            errors.append(f"{t.id}: review must be none|lead")
        for section in ("## Мета", "## Що зробити", "## Критерії приймання", "## Перевірка", "## Docs delta"):
            if section not in t.body:
                errors.append(f"{t.id}: body is missing section '{section}'")
    ids = sorted(tasks)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            ta, tb = tasks[a], tasks[b]
            if a in tb.deps_closure or b in ta.deps_closure:
                continue  # ordered by dependencies, may share files
            shared = sorted({fa for fa in ta.files for fb in tb.files if _patterns_overlap(fa, fb)})
            if shared:
                errors.append(f"{a} and {b} can run in parallel but share files: {shared}")
    for name, members in chunk_map(tasks).items():
        ids = {m.id for m in members}
        waves = sorted({m.wave for m in members})
        if len(waves) > 1:
            errors.append(f"chunk {name}: members have different waves {waves}")
        for m in members:
            for d in sorted(m.deps_closure - ids):
                between = sorted(tasks[d].deps_closure & ids)
                if between:
                    errors.append(f"chunk {name}: {d} depends on {between} and is needed by {m.id}; "
                                  "it would have to land inside the chunk PR")
    if errors:
        print("G0 FAILED")
        for e in errors:
            print("  -", e)
        return 1
    print(f"G0 OK: {len(tasks)} tasks, {len(chunk_map(tasks))} chunks, no cycles, parallel tasks have disjoint files")
    return 0


def cmd_order(_args) -> int:
    tasks = load_tasks()
    for n, level in enumerate(topo_levels(tasks)):
        mode = "PARALLEL" if len(level) > 1 else "SEQUENTIAL"
        items = ", ".join(f"{t.id}(wave {t.wave}, deps {','.join(t.depends_on) or '-'}"
                          f"{', chunk ' + t.chunk if t.chunk else ''})" for t in level)
        print(f"step {n}: [{mode}] {items}")
    return 0


# ----------------------------------------------------------------------------------------------- gh helpers

def gh(*args: str, input_text: Optional[str] = None, check: bool = True) -> str:
    if not shutil.which("gh"):
        raise GateError("gh CLI not found")
    proc = subprocess.run(["gh", *args], input=input_text, capture_output=True, text=True, cwd=ROOT)
    if check and proc.returncode != 0:
        raise GateError(f"gh {' '.join(args[:3])} failed: {(proc.stderr or proc.stdout).strip()[:500]}")
    return proc.stdout


def gh_json(*args: str) -> Any:
    out = gh(*args)
    return json.loads(out) if out.strip() else None


def graphql(query: str, variables: dict) -> dict:
    payload = json.dumps({"query": query, "variables": variables})
    data = json.loads(gh("api", "graphql", "--input", "-", input_text=payload))
    if data.get("errors"):
        raise GateError(f"graphql error: {data['errors']}")
    return data["data"]


def find_project() -> Optional[dict]:
    data = gh_json("project", "list", "--owner", OWNER, "--format", "json", "--limit", "100")
    for p in (data or {}).get("projects", []):
        if p.get("title") == PROJECT_TITLE:
            return p
    return None


def project_fields(number: int) -> dict[str, dict]:
    data = gh_json("project", "field-list", str(number), "--owner", OWNER, "--format", "json", "--limit", "50")
    return {f["name"]: f for f in (data or {}).get("fields", [])}


def status_field_name(fields: dict[str, dict]) -> str:
    return "Stage" if "Stage" in fields else "Status"


ITEMS_QUERY = """
query($id: ID!, $after: String) {
  node(id: $id) {
    ... on ProjectV2 {
      items(first: 100, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          content { ... on Issue { number title url state } }
          fieldValues(first: 30) {
            nodes {
              ... on ProjectV2ItemFieldTextValue { text field { ... on ProjectV2FieldCommon { name } } }
              ... on ProjectV2ItemFieldNumberValue { number field { ... on ProjectV2FieldCommon { name } } }
              ... on ProjectV2ItemFieldSingleSelectValue { name field { ... on ProjectV2FieldCommon { name } } }
            }
          }
        }
      }
    }
  }
}
"""


@dataclass
class Board:
    number: int
    id: str
    fields: dict[str, dict]
    status_field: str
    items: dict[str, dict]  # task id -> {"item_id", "issue", "url", "values": {field: value}}


def load_board() -> Board:
    proj = find_project()
    if not proj:
        raise GateError(f"project '{PROJECT_TITLE}' not found for {OWNER}; run board-init")
    number, pid = int(proj["number"]), proj["id"]
    fields = project_fields(number)
    items: dict[str, dict] = {}
    after = None
    while True:
        data = graphql(ITEMS_QUERY, {"id": pid, "after": after})["node"]["items"]
        for node in data["nodes"]:
            content = node.get("content") or {}
            m = re.match(r"^\[(T\d+)\]", content.get("title") or "")
            if not m:
                continue
            values: dict[str, Any] = {}
            for fv in node["fieldValues"]["nodes"]:
                fname = ((fv or {}).get("field") or {}).get("name")
                if not fname:
                    continue
                values[fname] = fv.get("text", fv.get("number", fv.get("name")))
            items[m.group(1)] = {"item_id": node["id"], "issue": content.get("number"), "url": content.get("url"),
                                 "state": content.get("state"), "values": values}
        if not data["pageInfo"]["hasNextPage"]:
            break
        after = data["pageInfo"]["endCursor"]
    return Board(number, pid, fields, status_field_name(fields), items)


def set_field(board: Board, item_id: str, name: str, value: Any) -> None:
    f = board.fields[name]
    base = ["project", "item-edit", "--id", item_id, "--project-id", board.id, "--field-id", f["id"]]
    if name == board.status_field:
        opt = next((o for o in f.get("options", []) if o["name"] == value), None)
        if not opt:
            raise GateError(f"field {name} has no option {value!r}")
        gh(*base, "--single-select-option-id", opt["id"])
    elif name in NUMBER_FIELDS:
        gh(*base, "--number", str(value))
    else:
        gh(*base, "--text", str(value))


def issue_comment(issue: int, text: str) -> None:
    gh("issue", "comment", str(issue), "--repo", REPO, "--body", text)


# ----------------------------------------------------------------------------------------------- board-init

def _ensure_status_options(number: int, fields: dict[str, dict], dry: bool) -> None:
    status = fields.get("Status")
    have = [o["name"] for o in (status or {}).get("options", [])]
    if status and have == STATUSES:
        return
    if "Stage" in fields:
        return
    if dry:
        print(f"[dry-run] would set Status options to {STATUSES} (now {have})")
        return
    opts = [{"name": s, "color": STATUS_COLORS[s], "description": ""} for s in STATUSES]
    query = """
    mutation($field: ID!, $opts: [ProjectV2SingleSelectFieldOptionInput!]) {
      updateProjectV2Field(input: {fieldId: $field, singleSelectOptions: $opts}) {
        projectV2Field { ... on ProjectV2SingleSelectField { id } }
      }
    }"""
    try:
        if not status:
            raise GateError("no Status field")
        graphql(query, {"field": status["id"], "opts": opts})
        print("Status options set:", ", ".join(STATUSES))
    except GateError as exc:
        print(f"could not edit Status options ({exc}); creating single-select field 'Stage' instead")
        gh("project", "field-create", str(number), "--owner", OWNER, "--name", "Stage",
           "--data-type", "SINGLE_SELECT", "--single-select-options", ",".join(STATUSES))
        print("NOTE: group the board view by 'Stage' in the GitHub UI once.")


def cmd_board_init(args) -> int:
    dry = args.dry_run
    tasks = load_tasks()
    if cmd_validate(args) != 0:
        return 1
    proj = find_project()
    if not proj:
        if dry:
            print(f"[dry-run] would create project '{PROJECT_TITLE}' for {OWNER} and link {REPO}")
            print(f"[dry-run] would create labels, {len(tasks)} issues and project items")
            return 0
        proj = gh_json("project", "create", "--owner", OWNER, "--title", PROJECT_TITLE, "--format", "json")
        print(f"created project #{proj['number']}")
    number = int(proj["number"])
    if not dry:
        gh("project", "link", str(number), "--owner", OWNER, "--repo", REPO, check=False)
    fields = project_fields(number)
    _ensure_status_options(number, fields, dry)
    for name in TEXT_FIELDS + NUMBER_FIELDS:
        if name not in fields:
            if dry:
                print(f"[dry-run] would create field {name}")
                continue
            gh("project", "field-create", str(number), "--owner", OWNER, "--name", name,
               "--data-type", "NUMBER" if name in NUMBER_FIELDS else "TEXT")
            print(f"created field {name}")
    if not dry:
        gh("label", "create", LABEL, "--repo", REPO, "--color", "5319e7", "--force",
           "--description", "Task of docs/improvements/PLAN.md")
        for w in sorted({t.wave for t in tasks.values()}):
            gh("label", "create", f"wave:{w}", "--repo", REPO, "--color", "c5def5", "--force")

    issues = gh_json("issue", "list", "--repo", REPO, "--label", LABEL, "--state", "all", "--limit", "200",
                     "--json", "number,title,url") if not dry else []
    by_task: dict[str, dict] = {}
    for iss in issues or []:
        m = re.match(r"^\[(T\d+)\]", iss["title"])
        if m:
            if m.group(1) in by_task:
                raise GateError(f"duplicate issues for {m.group(1)}: #{by_task[m.group(1)]['number']}, #{iss['number']}")
            by_task[m.group(1)] = iss

    order = [t for level in topo_levels(tasks) for t in level]
    for t in order:
        if t.id in by_task:
            continue
        dep_refs = ", ".join(f"#{by_task[d]['number']}" for d in t.depends_on) if not dry else ", ".join(t.depends_on)
        body = (f"**Task file:** `{t.path.relative_to(ROOT)}`  \n**Wave:** {t.wave}  \n"
                f"**Depends on:** {dep_refs or '—'}  \n**Review:** {t.review}\n\n---\n\n{t.body}")
        if dry:
            print(f"[dry-run] would create issue [{t.id}] {t.title} (depends: {dep_refs or '-'})")
            continue
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as fh:
            fh.write(body)
            body_path = fh.name
        try:
            url = gh("issue", "create", "--repo", REPO, "--title", f"[{t.id}] {t.title}", "--body-file", body_path,
                     "--label", LABEL, "--label", f"wave:{t.wave}").strip().splitlines()[-1]
        finally:
            os.unlink(body_path)
        by_task[t.id] = {"number": int(url.rstrip("/").split("/")[-1]), "url": url, "title": t.title}
        print(f"created issue #{by_task[t.id]['number']} for {t.id}")
    if dry:
        return 0

    board = load_board()
    for t in order:
        if t.id not in board.items:
            gh("project", "item-add", str(number), "--owner", OWNER, "--url", by_task[t.id]["url"])
    board = load_board()
    for t in order:
        item = board.items[t.id]
        vals = item["values"]
        wanted = {"Task": t.id, "Wave": t.wave, "Depends": ",".join(t.depends_on) or "-", "Chunk": t.chunk or "-"}
        for name, value in wanted.items():
            if vals.get(name) != value and not (name == "Wave" and vals.get(name) == float(value)):
                set_field(board, item["item_id"], name, value)
        if not vals.get(board.status_field):
            set_field(board, item["item_id"], board.status_field, "Ready" if not t.depends_on else "Todo")
    print(f"board ready: https://github.com/users/{OWNER}/projects/{number}")
    return cmd_board_check(args)


def cmd_board_check(_args) -> int:
    tasks = load_tasks()
    board = load_board()
    errors = []
    for tid, t in tasks.items():
        item = board.items.get(tid)
        if not item:
            errors.append(f"{tid}: no project item")
            continue
        v = item["values"]
        if v.get("Task") != tid:
            errors.append(f"{tid}: Task field is {v.get('Task')!r}")
        if v.get(board.status_field) not in STATUSES:
            errors.append(f"{tid}: {board.status_field} is {v.get(board.status_field)!r}")
        if v.get("Wave") not in (t.wave, float(t.wave)):
            errors.append(f"{tid}: Wave is {v.get('Wave')!r}, expected {t.wave}")
        if (v.get("Depends") or "-") != (",".join(t.depends_on) or "-"):
            errors.append(f"{tid}: Depends is {v.get('Depends')!r}")
        if (v.get("Chunk") or "-") != (t.chunk or "-"):
            errors.append(f"{tid}: Chunk is {v.get('Chunk')!r}, expected {t.chunk or '-'!r}")
    extra = sorted(set(board.items) - set(tasks))
    if extra:
        errors.append(f"items without task file: {extra}")
    if errors:
        print("G1 FAILED")
        for e in errors:
            print("  -", e)
        return 1
    print(f"G1 OK: {len(tasks)} issues on project #{board.number}, fields complete")
    return 0


# ----------------------------------------------------------------------------------------------- status / set

def cmd_status(_args) -> int:
    tasks = load_tasks()
    board = load_board()
    print(f"{'task':5} {'issue':>6}  {'status':12} {'agent':18} {'pr':>5}  title")
    for tid in sorted(tasks):
        item = board.items.get(tid, {"values": {}, "issue": None})
        v = item["values"]
        print(f"{tid:5} {('#' + str(item['issue'])) if item['issue'] else '-':>6}  "
              f"{str(v.get(board.status_field) or '-'):12} {str(v.get('Agent') or '-'):18} "
              f"{str(v.get('PR') or '-'):>5}  {tasks[tid].title}")
    return 0


def cmd_set(args) -> int:
    if args.status not in STATUSES:
        raise GateError(f"status must be one of {STATUSES}")
    tasks = load_tasks()
    if args.task not in tasks:
        raise GateError(f"unknown task {args.task}")
    board = load_board()
    item = board.items.get(args.task)
    if not item:
        raise GateError(f"{args.task} is not on the board")
    old = item["values"].get(board.status_field)
    set_field(board, item["item_id"], board.status_field, args.status)
    if args.agent is not None:
        set_field(board, item["item_id"], "Agent", args.agent)
    if args.pr is not None:
        set_field(board, item["item_id"], "PR", str(args.pr))
    who = args.agent or item["values"].get("Agent") or "unknown"
    lines = [f"🤖 **{who}**: `{old or '-'}` → `{args.status}`"]
    if args.pr is not None:
        lines.append(f"PR: #{args.pr}")
    if args.note:
        lines.append(args.note)
    issue_comment(item["issue"], "\n\n".join(lines))
    print(f"{args.task}: {old} -> {args.status}")
    return 0


# ----------------------------------------------------------------------------------------------- ready / sync

def main_ci_green() -> tuple[bool, str]:
    runs = gh_json("run", "list", "--repo", REPO, "--branch", "main", "--workflow", "tests.yml", "--limit", "1",
                   "--json", "conclusion,status,headSha,url") or []
    if not runs:
        return False, "no CI run on main"
    r = runs[0]
    return (r.get("status") == "completed" and r.get("conclusion") == "success"), f"{r.get('conclusion')} {r.get('url')}"


def open_pr_for(tid: str) -> Optional[int]:
    prs = gh_json("pr", "list", "--repo", REPO, "--state", "open", "--search", f"{tid} in:title",
                  "--json", "number,title") or []
    for pr in prs:
        if tid in pr_title_tasks(pr["title"]):
            return int(pr["number"])
    return None


def cmd_ready(_args) -> int:
    tasks = load_tasks()
    board = load_board()
    st = {tid: board.items.get(tid, {"values": {}})["values"].get(board.status_field) for tid in tasks}
    green, info = main_ci_green()
    chunks = chunk_map(tasks)
    units: list[tuple[str, list[Task]]] = []
    seen: set[str] = set()
    for tid, t in sorted(tasks.items()):
        if tid in seen:
            continue
        members = chunks[t.chunk] if t.chunk else [t]
        ids = {m.id for m in members}
        seen |= ids
        if any(st[m.id] not in ("Todo", "Ready") for m in members):
            continue
        if any(st.get(d) != "Done" for m in members for d in m.depends_on if d not in ids):
            continue
        pr = next((p for p in (open_pr_for(m.id) for m in members) if p), None)
        if pr:
            print(f"  skip {t.chunk or tid}: open PR #{pr}")
            continue
        units.append((t.chunk, members))
    print(f"main CI: {info}")
    if not green:
        print("G2 FAILED: main is not green — dispatch nothing, report to the user")
        return 1
    if not units:
        print("G2: nothing ready (waiting for merges or all done)")
        return 0
    print("G2 OK — dispatch as follows:")
    for chunk, members in units:
        if chunk:
            print(f"  chunk {chunk}: {'+'.join(m.id for m in members)}  "
                  "(one PR, one commit per task, in this order)")
            for m in members:
                print(f"    {m.id} {m.title}  ({m.path.relative_to(ROOT)})")
        else:
            m = members[0]
            print(f"  {m.id} {m.title}  ({m.path.relative_to(ROOT)})")
    if len(units) > 1:
        print(f"PARALLEL: these {len(units)} units are independent (validated by G0); one lead or sub-agent each.")
    else:
        print("SEQUENTIAL: one unit.")
    return 0


def cmd_sync(_args) -> int:
    tasks = load_tasks()
    board = load_board()
    st = {tid: board.items[tid]["values"].get(board.status_field) for tid in tasks if tid in board.items}
    green, info = main_ci_green()
    changed = False
    for tid in sorted(tasks):
        item = board.items.get(tid)
        if not item or st.get(tid) != "Review":
            continue
        pr = item["values"].get("PR")
        if not pr:
            continue
        data = gh_json("pr", "view", str(pr), "--repo", REPO, "--json", "state,mergedAt")
        if data and data.get("state") == "MERGED":
            if not green:
                print(f"{tid}: PR #{pr} merged, but main CI is not green ({info}) — leaving in Review")
                continue
            set_field(board, item["item_id"], board.status_field, "Done")
            issue_comment(item["issue"], f"🤖 **orchestrator**: PR #{pr} merged, main CI green → `Done` (G7)")
            gh("issue", "close", str(item["issue"]), "--repo", REPO, check=False)
            st[tid] = "Done"
            changed = True
            print(f"{tid}: Review -> Done")
        elif data and data.get("state") == "CLOSED":
            print(f"{tid}: PR #{pr} was closed without merge — needs attention")
    for tid, t in sorted(tasks.items()):
        if st.get(tid) == "Todo" and all(st.get(d) == "Done" for d in t.depends_on):
            item = board.items[tid]
            set_field(board, item["item_id"], board.status_field, "Ready")
            issue_comment(item["issue"], "🤖 **orchestrator**: all dependencies are Done → `Ready`")
            changed = True
            print(f"{tid}: Todo -> Ready")
    if not changed:
        print("sync: nothing changed")
    return 0


# ----------------------------------------------------------------------------------------------- check-pr (G5)

def _run_tests_at(ref: str) -> tuple[int, int, str]:
    """Run the suite in a temporary worktree at ref. Returns (ran, failures_or_errors, tail)."""
    py = str(VENV_PY) if VENV_PY.exists() else sys.executable
    tmp = tempfile.mkdtemp(prefix="taskctl-")
    wt = Path(tmp) / "wt"
    subprocess.run(["git", "worktree", "add", "--detach", str(wt), ref], cwd=ROOT, check=True,
                   capture_output=True, text=True)
    try:
        proc = subprocess.run([py, "-m", "unittest", "discover", "-s", "tests"], cwd=wt, capture_output=True,
                              text=True, timeout=900)
        out = proc.stderr + proc.stdout
        ran = int(m.group(1)) if (m := re.search(r"^Ran (\d+) tests?", out, re.M)) else 0
        bad = 0 if proc.returncode == 0 else 1
        summary = [ln for ln in out.splitlines() if re.match(r"^(Ran \d+|OK|FAILED|ERROR:|FAIL:)", ln)]
        return ran, bad, "\n".join(summary[-12:])
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(wt)], cwd=ROOT, capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)


def _failed_checks(rollup: list[dict]) -> list[str]:
    """Completed checks with a failing conclusion, excluding NON_BLOCKING_CHECKS."""
    return [c.get("name") for c in rollup
            if (c.get("conclusion") or "").upper() not in ("SUCCESS", "SKIPPED", "NEUTRAL", "")
            and (c.get("status") or "COMPLETED") == "COMPLETED"
            and c.get("name") not in NON_BLOCKING_CHECKS]


def cmd_check_pr(args) -> int:
    tasks = load_tasks()
    members = resolve_tasks(args.task, tasks)
    label = "+".join(m.id for m in members)
    allowed_files = [p for m in members for p in m.files]
    allowed_golden = [g for m in members for g in m.golden]
    pr = gh_json("pr", "view", str(args.pr), "--repo", REPO, "--json",
                 "title,body,baseRefName,headRefName,headRefOid,state,commits,files,statusCheckRollup")
    fails: list[str] = []
    ok: list[str] = []

    def check(cond: bool, good: str, bad: str) -> None:
        (ok if cond else fails).append(good if cond else bad)

    check(pr["title"].startswith(f"[{label}] "), "title prefix", f"title must start with '[{label}] ': {pr['title']!r}")
    check(pr["baseRefName"] == "main", "base is main", f"base is {pr['baseRefName']}")
    check(pr["state"] == "OPEN", "PR open", f"PR state is {pr['state']}")
    commits = pr.get("commits") or []
    check(len(commits) == len(members), f"{len(members)} commit(s), one per task",
          f"{len(commits)} commits for {len(members)} task(s) (AGENTS.md: one dedicated commit per task)")
    heads = [(c.get("messageHeadline") or "") for c in commits]
    commit_tasks = [m.group(1) for h in heads if (m := re.match(r"^\[(T\d+)\] ", h))]
    check(sorted(commit_tasks) == sorted(m.id for m in members) and len(commit_tasks) == len(heads),
          "each commit is '[Txx] ...' for a distinct task of the PR",
          f"commit headlines must be '[Txx] ...', one per task {label}: {heads}")
    for c in commits:
        msg = (c.get("messageHeadline") or "") + "\n" + (c.get("messageBody") or "")
        check(APPROVAL_PHRASE not in msg, "no auto-merge phrase in commits",
              "commit message contains the auto-merge approval phrase — only the owner may write it")
    files = [f["path"] for f in pr.get("files") or []]
    outside = [f for f in files if not _file_allowed(f, allowed_files)]
    check(not outside, f"{len(files)} files, all within task scope", f"files outside task scope: {outside}")
    forbidden = [f for f in files if _file_allowed(f, FORBIDDEN_PATHS)]
    check(not forbidden, "no data/ or .env", f"forbidden paths in PR: {forbidden}")
    golden_touched = [f for f in files if f.startswith("tests/golden/")]
    bad_golden = [f for f in golden_touched if f not in allowed_golden]
    check(not bad_golden, "golden files within allowed list", f"golden files changed without permission: {bad_golden}")
    body = pr.get("body") or ""
    missing = [s for s in PR_BODY_SECTIONS if s not in body]
    check(not missing, "PR body sections present", f"PR body is missing sections: {missing}")
    if golden_touched:
        check("Golden diff" in body, "golden diff explained", "golden changed but PR body has no 'Golden diff' section")
    check(APPROVAL_PHRASE not in body, "no auto-merge phrase in body", "PR body contains the auto-merge phrase")

    rollup = pr.get("statusCheckRollup") or []
    pending = [c.get("name") for c in rollup if (c.get("status") or "COMPLETED") != "COMPLETED"]
    failed = _failed_checks(rollup)
    has_unit = any((c.get("name") or "").startswith("unittest") for c in rollup)
    check(bool(rollup) and has_unit and not pending and not failed, "CI green",
          f"CI not green: pending={pending} failed={failed} unittest-present={has_unit}")

    diff = gh("pr", "diff", str(args.pr), "--repo", REPO)
    added_xfail = [ln for ln in diff.splitlines() if ln.startswith("+") and "expectedFailure" in ln]
    check(not added_xfail, "no expectedFailure added", f"expectedFailure added: {added_xfail[:3]}")
    added_skip = [ln for ln in diff.splitlines() if ln.startswith("+") and re.search(r"unittest\.skip|@skip", ln)]
    check(not added_skip, "no skips added", f"skip added: {added_skip[:3]}")

    if not args.no_tests:
        subprocess.run(["git", "fetch", "-q", "origin", "main", f"pull/{args.pr}/head"], cwd=ROOT, check=True)
        base_ran, base_bad, _ = _run_tests_at("origin/main")
        head_ran, head_bad, tail = _run_tests_at(pr["headRefOid"])
        check(head_bad == 0, f"tests pass on PR head ({head_ran})", f"tests fail on PR head:\n{tail}")
        check(head_ran >= base_ran, f"test count {head_ran} >= main {base_ran}",
              f"test count dropped: {head_ran} < main {base_ran}")
        if base_bad:
            fails.append("tests fail on origin/main itself — stop and tell the user")

    for line in ok:
        print("  ok  ", line)
    for line in fails:
        print("  FAIL", line)
    if fails:
        print(f"G5 FAILED for {label} PR #{args.pr}")
        return 1
    lead_review = any(m.review == "lead" for m in members)
    print(f"G5 OK for {label} PR #{args.pr}" + (" (lead review still required: review=lead)" if lead_review else ""))
    return 0


# ----------------------------------------------------------------------------------------------- main

def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="taskctl", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("validate").set_defaults(fn=cmd_validate)
    sub.add_parser("order").set_defaults(fn=cmd_order)
    p = sub.add_parser("board-init")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_board_init)
    sub.add_parser("board-check").set_defaults(fn=cmd_board_check)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("ready").set_defaults(fn=cmd_ready)
    sub.add_parser("sync").set_defaults(fn=cmd_sync)
    p = sub.add_parser("set")
    p.add_argument("task")
    p.add_argument("status")
    p.add_argument("--agent")
    p.add_argument("--pr", type=int)
    p.add_argument("--note")
    p.set_defaults(fn=cmd_set)
    p = sub.add_parser("check-pr")
    p.add_argument("task", help="Txx, Txx,Tyy or chunk:<name>")
    p.add_argument("pr", type=int)
    p.add_argument("--no-tests", action="store_true", help="skip running the suite locally (CI is still required)")
    p.set_defaults(fn=cmd_check_pr)
    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except GateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
