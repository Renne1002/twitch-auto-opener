from __future__ import annotations

import re
from datetime import datetime

_TEMPLATE_PATTERN = re.compile(r"\{([^{}]+)\}")


class TitleTemplateError(ValueError):
    pass


class TitleTemplateRenderer:
    @staticmethod
    def validate(template: str) -> None:
        unknown: list[str] = []
        for raw in _TEMPLATE_PATTERN.findall(template):
            if raw == "id":
                continue
            if raw.startswith("ts:") and len(raw) > 3:
                continue
            unknown.append(raw)

        if unknown:
            labels = ", ".join(sorted(set(unknown)))
            raise TitleTemplateError(
                f"unknown placeholder(s): {labels}; allowed placeholders are id and ts:<strftime>"
            )

    @staticmethod
    def render(template: str, *, user_id: str, captured_at: datetime) -> str:
        TitleTemplateRenderer.validate(template)

        def replacer(match: re.Match[str]) -> str:
            raw = match.group(1)
            if raw == "id":
                return user_id
            if raw.startswith("ts:"):
                return captured_at.strftime(raw[3:])
            raise TitleTemplateError(f"unsupported placeholder: {raw}")

        return _TEMPLATE_PATTERN.sub(replacer, template)
