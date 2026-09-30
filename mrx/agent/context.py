"""Short-term working context so 'it', 'that', 'the second one', 'same folder' resolve to something real."""
from __future__ import annotations

from typing import Any

ORDINALS = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4,
            "fifth": 5, "5th": 5, "last": -1, "next": 1}


class Context:
    def __init__(self):
        self.last_path: str | None = None
        self.last_folder: str | None = None
        self.last_app: str | None = None
        self.last_url: str | None = None
        self.last_video: dict | None = None
        self.last_results: list[dict] = []      # ordered list the user may refer to by position
        self.last_results_kind: str | None = None
        self.last_command: str | None = None
        self.history: list[dict] = []           # [{role, text}] for text-only continuity

    def add_turn(self, role: str, text: str) -> None:
        self.history.append({"role": role, "text": text})
        del self.history[:-20]

    def update_from(self, tool: str, args: dict, result: Any) -> None:
        r = result if isinstance(result, dict) else {}
        if tool in ("create_folder", "delete_folder"):
            if r.get("path"):
                self.last_folder = self.last_path = r["path"]
        elif tool in ("create_file", "modify_file", "append_file", "copy_file", "move_file", "rename_file", "read_file", "file_info", "open_path", "delete_file"):
            p = r.get("path") or args.get("path") or args.get("dst")
            if p:
                self.last_path = p
                self.last_folder = p if r.get("is_dir") else str(__import__("pathlib").Path(p).parent)
        elif tool == "list_directory" and r.get("path"):
            self.last_folder = r["path"]
            self.last_results, self.last_results_kind = r.get("entries", []), "files"
        elif tool == "search_files":
            self.last_results, self.last_results_kind = r.get("matches", []), "files"
        elif tool in ("open_application", "close_application", "restart_application"):
            self.last_app = args.get("name") or self.last_app
        elif tool in ("navigate", "search_web"):
            self.last_url = r.get("url") or self.last_url
            if tool == "search_web":
                self.last_results, self.last_results_kind = r.get("results", []), "links"
        elif tool in ("youtube_search",):
            self.last_results, self.last_results_kind = r.get("results", []), "videos"
        elif tool == "play_youtube":
            self.last_video = {"video_id": r.get("video_id"), "title": r.get("title")}
        elif tool == "get_live_news":
            self.last_results, self.last_results_kind = r.get("articles", []), "articles"
        elif tool == "download_file" and r.get("path"):
            self.last_path = r["path"]

    def pick(self, ordinal: str) -> dict | None:
        n = ORDINALS.get(ordinal.lower())
        if n is None or not self.last_results:
            return None
        try:
            return self.last_results[n - 1] if n > 0 else self.last_results[n]
        except IndexError:
            return None

    def summary(self) -> str:
        bits = []
        if self.last_app:
            bits.append(f"last application: {self.last_app}")
        if self.last_path:
            bits.append(f"last file/folder: {self.last_path}")
        if self.last_folder:
            bits.append(f"current folder: {self.last_folder}")
        if self.last_url:
            bits.append(f"browser page: {self.last_url}")
        if self.last_video:
            bits.append(f"last video: {self.last_video}")
        if self.last_results:
            brief = [(r.get("title") or r.get("name") or r.get("text") or "")[:60] for r in self.last_results[:8]]
            bits.append(f"last list ({self.last_results_kind}, numbered from 1): {brief}")
        return "; ".join(bits) or "none yet"
