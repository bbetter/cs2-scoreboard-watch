"""Frontend modules: static checks on web/*.js and index.html, plus the Node suites (skipped when node is absent).

Static checks (pure Python, always run):
  - every `import {...} from "/x.js"` points to an existing file and imports only names it exports
  - every name a module uses that another web module declares is declared locally or imported
  - the import graph has no cycles
  - index.html loads only module scripts that exist, and has no inline on* handlers

Node checks (run when `node` is on PATH):
  - tests/js/smoke_imports.mjs links and runs every module (stub DOM)
  - tests/js/sources_test.mjs: registry chips, tabs, status mapping, escaping, uid invariance
  - tests/js/render_snapshot.mjs --new output equals tests/golden/frontend_render_golden.json (generated
    from the single-file app.js before the module split)
"""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
JS = ROOT / "tests" / "js"
GOLDEN = ROOT / "tests" / "golden" / "frontend_render_golden.json"
NODE = shutil.which("node")
# table-view.js is a self-contained IIFE: it exports nothing and is not part of the import graph.
MODULE_FILES = sorted(p for p in WEB.glob("*.js") if p.name != "table-view.js")

IMPORT = re.compile(r'^import \{([^}]*)\} from "([^"]+)";', re.M)
EXPORT_DECL = re.compile(r"^export (?:const|let|function|async function) ([A-Za-z_$][\w$]*)", re.M)
DECL = re.compile(r"^(?:export )?(?:const|let|function|async function) ([A-Za-z_$][\w$]*)", re.M)
TOKEN = re.compile(r"(?<![\w$.])([A-Za-z_$][\w$]*)")
COMMENT = re.compile(r"(^|\s)//[^\n]*", re.M)
INLINE_HANDLER = re.compile(r"\son[a-z]+\s*=", re.I)


def _strip_imports(text: str) -> str:
    return IMPORT.sub("", text)


def _names(text: str) -> list[str]:
    return [n.strip() for n in text.split(",") if n.strip()]


class ModuleGraph:
    def __init__(self):
        self.text = {p.name: p.read_text(encoding="utf-8") for p in MODULE_FILES}
        self.exports = {m: set(EXPORT_DECL.findall(t)) for m, t in self.text.items()}
        self.declared = {m: set(DECL.findall(t)) for m, t in self.text.items()}
        self.imports = {}
        for m, t in self.text.items():
            self.imports[m] = {}
            for names, src in IMPORT.findall(t):
                self.imports[m].setdefault(src, set()).update(_names(names))


class StaticModuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = ModuleGraph()

    def test_module_files_exist_and_are_not_empty(self):
        self.assertTrue(MODULE_FILES)
        for p in MODULE_FILES:
            self.assertTrue(p.read_text(encoding="utf-8").strip(), p.name)

    def test_import_targets_exist_and_names_are_exported(self):
        problems = []
        for mod, sources in self.graph.imports.items():
            for src, names in sources.items():
                target = src.lstrip("/")
                if not src.startswith("/") or not (WEB / target).is_file():
                    problems.append(f"{mod}: import from {src!r} is not an existing web/ file")
                    continue
                missing = names - self.graph.exports.get(target, set())
                if missing:
                    problems.append(f"{mod}: {sorted(missing)} not exported by {target}")
        self.assertEqual(problems, [])

    def test_every_used_cross_module_name_is_declared_or_imported(self):
        problems = []
        all_declared = set().union(*self.graph.declared.values())
        for mod, text in self.graph.text.items():
            body = _strip_imports(text)
            code = COMMENT.sub(r"\1", body).replace("...", "   ")
            imported = set().union(*self.graph.imports[mod].values()) if self.graph.imports[mod] else set()
            local = self.graph.declared[mod]
            for name in set(TOKEN.findall(code)):
                if name in all_declared and name not in local and name not in imported:
                    problems.append(f"{mod} uses {name!r} without declaring or importing it")
        self.assertEqual(sorted(problems), [])

    def test_import_graph_is_acyclic(self):
        graph = {m: {src.lstrip("/") for src in srcs} for m, srcs in self.graph.imports.items()}
        state = {}

        def visit(node, stack):
            state[node] = "visiting"
            for dep in graph.get(node, ()):
                if state.get(dep) == "visiting":
                    raise AssertionError(f"import cycle: {' -> '.join(stack + [dep])}")
                if dep not in state:
                    visit(dep, stack + [dep])
            state[node] = "done"

        for m in graph:
            if m not in state:
                visit(m, [m])

    def test_index_html_loads_module_scripts_that_exist(self):
        html = (WEB / "index.html").read_text(encoding="utf-8")
        scripts = re.findall(r"<script\b([^>]*)>", html)
        self.assertTrue(scripts)
        for attrs in scripts:
            self.assertIn('type="module"', attrs, f"classic script still in index.html: {attrs!r}")
            src = re.search(r'src="([^"]+)"', attrs)
            self.assertIsNotNone(src, attrs)
            self.assertTrue((WEB / src.group(1).lstrip("/")).is_file(), src.group(1))
        self.assertIsNone(INLINE_HANDLER.search(html), "inline on* handler in index.html")

    def test_modules_use_absolute_paths_only(self):
        for mod, sources in self.graph.imports.items():
            for src in sources:
                self.assertTrue(src.startswith("/"), f"{mod}: relative import {src!r}")


@unittest.skipUnless(NODE, "node is not installed")
class NodeFrontendTests(unittest.TestCase):
    def _node(self, *args):
        return subprocess.run(
            [NODE, "--import", str(JS / "register.mjs"), *args],
            cwd=ROOT, capture_output=True, text=True, timeout=60,
        )

    def test_every_module_links_and_runs_with_a_stub_dom(self):
        res = self._node(str(JS / "smoke_imports.mjs"))
        self.assertEqual(res.returncode, 0, res.stderr[-3000:])

    def test_sources_registry_suite(self):
        res = self._node(str(JS / "sources_test.mjs"))
        self.assertEqual(res.returncode, 0, res.stdout[-3000:] + res.stderr[-1000:])

    def test_render_output_matches_pre_split_golden(self):
        res = self._node(str(JS / "render_snapshot.mjs"), "--new")
        self.assertEqual(res.returncode, 0, res.stderr[-3000:])
        self.assertEqual(json.loads(res.stdout), json.loads(GOLDEN.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
