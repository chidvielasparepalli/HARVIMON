"""Persona selection with dynamic file discovery and configuration access.

No central personality registry exists here. IDs and aliases are derived from
the individual PROFILE files discovered by Personality.loader.
"""

from copy import deepcopy
import re

from .loader import discover_personalities


class PersonalityManager:
    def __init__(self, default="tony", intensity=0.75):
        self._intensity = self._validate_intensity(intensity)
        self._active_id = ""
        try:
            self.set_personality(default)
        except KeyError:
            available = discover_personalities()
            fallback = "tony" if "tony" in available else next(iter(available), "")
            if not fallback:
                raise
            self._active_id = fallback

    @staticmethod
    def _validate_intensity(value):
        value = float(value)
        if not 0 <= value <= 1:
            raise ValueError("intensity must be between 0.0 and 1.0")
        return value

    @staticmethod
    def _normalize(value):
        value = str(value or "").strip().lower()
        value = re.sub(r"[^a-z0-9]+", " ", value)
        return re.sub(r"\s+", " ", value).strip()

    def _resolve(self, personality_id):
        personalities = discover_personalities()
        raw = self._normalize(personality_id)
        if not raw:
            raise KeyError("Personality name is empty")

        # Exact filename IDs remain the canonical identifiers.
        for key in personalities:
            if self._normalize(key) == raw:
                return key

        # Human-friendly aliases are derived from each profile's own name.
        for key, profile in personalities.items():
            if self._normalize(profile.get("name")) == raw:
                return key

            # Also accept common "first last" / "last first" wording without
            # creating a central alias table.
            tokens = self._normalize(profile.get("name")).split()
            if len(tokens) >= 2:
                if self._normalize(" ".join(reversed(tokens))) == raw:
                    return key
                if len(tokens) == 2:
                    for token in tokens:
                        if token == raw:
                            return key

        raise KeyError(f"Unknown personality: {personality_id}")

    @property
    def active_id(self):
        return self._active_id

    @property
    def intensity(self):
        return self._intensity

    def set_intensity(self, value):
        self._intensity = self._validate_intensity(value)
        return self._intensity

    def resolve_personality(self, personality_id):
        """Return the canonical file ID for a human-friendly personality name."""
        return self._resolve(personality_id)

    def set_personality(self, personality_id):
        self._active_id = self._resolve(personality_id)
        return self.current()

    def current(self):
        return deepcopy(discover_personalities()[self._active_id])

    def available(self):
        return [
            {"id": key, "name": value["name"], "description": value["description"]}
            for key, value in discover_personalities().items()
        ]

    def voice_config(self):
        return deepcopy(self.current()["voice"])

    def theme_config(self):
        return deepcopy(self.current().get("theme", {}))

    def greeting(self):
        return self.current().get("greeting", "")

    def system_prompt(self, base_prompt=""):
        p = self.current()
        b = p.get("behavior", p.get("interaction", {}))
        rules = "\n".join("- " + rule for rule in b.get("rules", []))
        prefix = base_prompt.strip() + "\n\n" if base_prompt.strip() else ""
        return prefix + (
            "CURRENT PERSONALITY PROFILE\n"
            "The personality changes communication style only. It never overrides "
            "safety, permissions, honesty, or task requirements.\n"
            f"Persona ID: {self._active_id}\n"
            f"Persona: {p['name']}\n"
            f"Description: {p['description']}\n"
            f"Style: {b.get('style', 'natural')}\n"
            f"Humor: {b.get('humor', 'as appropriate')}\n"
            f"Warmth: {b.get('warmth', 'helpful')}\n"
            f"Verbosity: {b.get('verbosity', 'as needed')}\n"
            f"Intensity: {self._intensity:.2f}/1.00. Scale style only, not helpfulness or safety.\n"
            "Rules:\n" + rules
        )
