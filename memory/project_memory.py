"""
Project-scoped memory — separate context per project.
Each project lives in ~/.jarvis/projects/NAME/
  memory.json   — key-value facts for this project
  history.jsonl — conversation log (one JSON object per line)
Inspired by ada_v2 project context management.

Usage:
  pm = ProjectMemory("my_app")
  pm.remember("stack", "FastAPI + React")
  pm.log("user", "What endpoints do we need?")
  ctx = pm.get_context(last_n=10)
"""
import json
import time
from pathlib import Path
from threading import Lock

_PROJECTS_ROOT = Path.home() / ".jarvis" / "projects"
_lock = Lock()


class ProjectMemory:
    def __init__(self, name: str):
        self.name    = name.strip().lower().replace(" ", "_")
        self._dir    = _PROJECTS_ROOT / self.name
        self._mem    = self._dir / "memory.json"
        self._hist   = self._dir / "history.jsonl"
        self._dir.mkdir(parents=True, exist_ok=True)

    # ── Memory (key-value facts) ─────────────────────────────────────────

    def load(self) -> dict:
        if not self._mem.exists():
            return {}
        try:
            return json.loads(self._mem.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def remember(self, key: str, value: str) -> None:
        with _lock:
            data = self.load()
            data[key] = {"value": value, "updated": time.strftime("%Y-%m-%d")}
            self._mem.write_text(
                json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
            )

    def forget(self, key: str) -> None:
        with _lock:
            data = self.load()
            data.pop(key, None)
            self._mem.write_text(
                json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
            )

    def format_memory(self) -> str:
        data = self.load()
        if not data:
            return ""
        lines = [f"[PROJECT: {self.name}]"]
        for k, v in data.items():
            val = v["value"] if isinstance(v, dict) else v
            lines.append(f"  {k}: {val}")
        return "\n".join(lines)

    # ── History (conversation log) ───────────────────────────────────────

    def log(self, role: str, content: str) -> None:
        entry = {
            "ts":      time.strftime("%Y-%m-%dT%H:%M:%S"),
            "role":    role,
            "content": content,
        }
        with _lock:
            with self._hist.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def get_history(self, last_n: int = 20) -> list[dict]:
        if not self._hist.exists():
            return []
        try:
            lines = self._hist.read_text(encoding="utf-8").strip().splitlines()
            entries = [json.loads(l) for l in lines if l.strip()]
            return entries[-last_n:]
        except Exception:
            return []

    def get_context(self, last_n: int = 10) -> str:
        """Return project memory + recent history formatted for LLM injection."""
        parts = []
        mem_str = self.format_memory()
        if mem_str:
            parts.append(mem_str)
        history = self.get_history(last_n)
        if history:
            parts.append(f"[Recent history — {self.name}]")
            for h in history:
                parts.append(f"  {h['role']}: {h['content'][:300]}")
        return "\n".join(parts) if parts else ""

    # ── Index project files ──────────────────────────────────────────────

    def index_files(self, project_dir: str | Path, extensions: tuple = (".py", ".md", ".json", ".txt")) -> int:
        """Scan project_dir and store file list in memory."""
        root   = Path(project_dir)
        files  = [str(p.relative_to(root)) for p in root.rglob("*") if p.is_file() and p.suffix in extensions]
        self.remember("indexed_files", json.dumps(files[:200]))  # cap at 200
        return len(files)


# ── Module-level active project ──────────────────────────────────────────────

_active: ProjectMemory | None = None


def set_active_project(name: str) -> ProjectMemory:
    global _active
    _active = ProjectMemory(name)
    print(f"[ProjectMemory] Active project: {name}")
    return _active


def get_active_project() -> ProjectMemory | None:
    return _active


def list_projects() -> list[str]:
    if not _PROJECTS_ROOT.exists():
        return []
    return [p.name for p in _PROJECTS_ROOT.iterdir() if p.is_dir()]
