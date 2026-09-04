"""Allowlist enforcement and sensitive-data redaction."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from computer_use.models import ActionType, Locator


class PolicyViolation(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class Policy:
    def __init__(self, path: Path):
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        self.allowed_hosts = set(raw.get("allowed_hosts") or [])
        self.allowed_path_prefixes = list(raw.get("allowed_path_prefixes") or ["/"])
        self.blocked_path_prefixes = list(raw.get("blocked_path_prefixes") or [])
        self.allowed_actions = set(raw.get("allowed_actions") or [])
        self.blocked_actions = set(raw.get("blocked_actions") or [])
        self.risky_text_patterns = [
            re.compile(p) for p in (raw.get("risky_text_patterns") or [])
        ]
        self.sensitive_param_names = {
            n.lower() for n in (raw.get("sensitive_param_names") or [])
        }

    def check_action(self, action: ActionType | str) -> None:
        name = action.value if isinstance(action, ActionType) else str(action)
        if name in self.blocked_actions:
            raise PolicyViolation(f"Action blocked by policy: {name}")
        if self.allowed_actions and name not in self.allowed_actions:
            raise PolicyViolation(f"Action not on allowlist: {name}")

    def check_url(self, url: str) -> None:
        parsed = urlparse(url)
        # about:blank / empty before first navigation
        if not parsed.netloc and parsed.scheme in {"", "about"}:
            return
        host = parsed.netloc
        if self.allowed_hosts and host not in self.allowed_hosts:
            raise PolicyViolation(f"Host not allowed: {host}")
        path = parsed.path or "/"
        if self._matches_blocked(path):
            raise PolicyViolation(f"Path blocked (risky/irreversible): {path}")
        if not self._matches_allowed(path):
            raise PolicyViolation(f"Path not on allowlist: {path}")

    def check_locator_risk(self, locator: Locator | None, visible_text: str = "") -> None:
        """Block risky controls by locator identity — not ambient page text."""
        if locator is None:
            return
        hay = " ".join(
            filter(
                None,
                [
                    locator.value,
                    locator.name,
                    locator.role,
                    # Optional short hint from caller (control label), not full page body
                    visible_text if len(visible_text) <= 80 else "",
                ],
            )
        )
        for pat in self.risky_text_patterns:
            if pat.search(hay):
                raise PolicyViolation(
                    f"Risky/irreversible control blocked by policy: matched {pat.pattern}"
                )

    def redact_mapping(self, data: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, v in data.items():
            if k.lower() in self.sensitive_param_names:
                out[k] = "[REDACTED]"
            elif isinstance(v, dict):
                out[k] = self.redact_mapping(v)
            elif isinstance(v, str) and self._looks_sensitive(k, v):
                out[k] = "[REDACTED]"
            else:
                out[k] = v
        return out

    def redact_text(self, text: str) -> str:
        # Member IDs are demo identifiers, kept. Tokens/passwords redacted if present.
        text = re.sub(
            r"(?i)(password|token|secret|ssn)\s*[:=]\s*\S+",
            r"\1=[REDACTED]",
            text,
        )
        return text

    def _matches_allowed(self, path: str) -> bool:
        for prefix in self.allowed_path_prefixes:
            if self._prefix_match(path, prefix):
                return True
        return False

    def _matches_blocked(self, path: str) -> bool:
        for prefix in self.blocked_path_prefixes:
            if self._prefix_match(path, prefix):
                return True
        return False

    @staticmethod
    def _prefix_match(path: str, pattern: str) -> bool:
        # Support simple * segment wildcards: /members/*/close
        if "*" not in pattern:
            return path == pattern or path.startswith(pattern.rstrip("*"))
        regex = "^" + re.escape(pattern).replace(r"\*", "[^/]+") + "($|/)"
        return re.match(regex, path) is not None

    def _looks_sensitive(self, key: str, value: str) -> bool:
        return key.lower() in self.sensitive_param_names or bool(
            re.search(r"(?i)(bearer\s+\S+|sk-[A-Za-z0-9]+)", value)
        )
