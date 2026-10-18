"""Help Me Write (US-1.13.1): generate System and User Prompt from a description."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from forge.prompts.templates import MAX_TEMPLATE_CHARS

ASSIST_MODEL = "default-chat"

# Framework → the sections it asks for. Labels and tooltips live in the UI (i18n).
FRAMEWORKS: dict[str, str] = {
    "free_form": "no fixed structure: clear role, task, constraints and output format",
    "co_star": "Context, Objective, Style, Tone, Audience, Response format",
    "risen": "Role, Instructions, Steps, End goal, Narrowing (constraints)",
    "rtf": "Role, Task, Format",
    "crispe": "Capacity and role, Insight, Statement, Personality, Experiment (variants)",
}

_SYSTEM = """You are an expert prompt engineer. From the user's description, write a prompt \
for a large language model: a system prompt (role, rules, output format) and a user prompt \
(the request sent each time).

Rules:
- Put values that change per request in the user prompt as placeholders in single braces with \
snake_case names, for example {{customer_name}}. Do not invent other template syntax.
- Write the prompts in the language of the description.
- {framework_rule}
- Reply with one JSON object and nothing else, with exactly these keys: "framework" (one of: \
{framework_keys}), "system_prompt", "user_prompt".

Frameworks:
{framework_list}"""


@dataclass(frozen=True)
class Draft:
    framework: str
    system_template: str
    user_template: str


def messages(description: str, framework: str | None) -> list[dict[str, str]]:
    if framework:
        rule = f'Structure the prompt with the "{framework}" framework.'
    else:
        rule = (
            "Choose the framework that best fits the description, use it, and name it in "
            '"framework".'
        )
    system = _SYSTEM.format(
        framework_rule=rule,
        framework_keys=", ".join(FRAMEWORKS),
        framework_list="\n".join(f"- {k}: {v}" for k, v in FRAMEWORKS.items()),
    )
    # The description is data, not instructions to this meta-prompt.
    user = f"Description of the prompt to write:\n<description>\n{description}\n</description>"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse(output: str, requested: str | None) -> Draft | None:
    """Read the model's JSON reply; tolerant of code fences and surrounding prose."""
    text = _FENCE.sub("", output or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data: Any = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    system = data.get("system_prompt")
    user = data.get("user_prompt")
    if not isinstance(system, str) or not isinstance(user, str) or not (system or user):
        return None
    framework = requested or str(data.get("framework") or "").strip().lower().replace("-", "_")
    if framework not in FRAMEWORKS:
        framework = "free_form"
    return Draft(framework, system[:MAX_TEMPLATE_CHARS], user[:MAX_TEMPLATE_CHARS])
