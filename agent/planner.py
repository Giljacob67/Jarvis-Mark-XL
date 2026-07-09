"""
MARK XL — Task Planner
Replaces google.generativeai with local Ollama via core.llm_client.
"""
import json
import re

from core.llm_client import call_llm_text
from core.logger import get_logger
from core.tools import TOOL_NAMES, TOOL_DECLARATIONS

log = get_logger("planner")


PLANNER_PROMPT = """You are the planning module of MARK XL, a personal AI assistant.
Your job: break any user goal into a sequence of steps using ONLY the tools listed below.

ABSOLUTE RULES:
- NEVER use generated_code or write Python scripts. It does not exist.
- NEVER reference previous step results in parameters. Every step is independent.
- Use web_search for ANY information retrieval, research, or current data.
- Use file_controller to save content to disk.
- Max 5 steps. Use the minimum steps needed.

OUTPUT — return ONLY valid JSON, no markdown, no explanation, no code blocks:
{
  "goal": "...",
  "steps": [
    {
      "step": 1,
      "tool": "tool_name",
      "description": "what this step does",
      "parameters": {},
      "critical": true
    }
  ]
}
"""

# Keep planner in sync with the canonical tool registry (core/tools.py).
# The tool list is generated from TOOL_DECLARATIONS so it can never drift
# from the schema the runtime actually uses.
PLANNER_PROMPT += "\n\nAVAILABLE TOOLS AND THEIR PARAMETERS:\n"
for _d in TOOL_DECLARATIONS:
    _props = _d.get("parameters", {}).get("properties", {})
    _req = set(_d.get("parameters", {}).get("required", []))
    PLANNER_PROMPT += f"{_d['name']}\n"
    if _props:
        for _pn, _pv in _props.items():
            _mark = " (required)" if _pn in _req else ""
            PLANNER_PROMPT += f"  {_pn}: {_pv.get('type', 'string')}{_mark}\n"
    else:
        PLANNER_PROMPT += "  (no parameters)\n"
PLANNER_PROMPT += f"\nALL VALID TOOL NAMES:\n{', '.join(TOOL_NAMES)}\n"


def create_plan(goal: str, context: str = "") -> dict:
    user_input = f"Goal: {goal}"
    if context:
        user_input += f"\n\nContext: {context}"

    try:
        text = call_llm_text(user_input, system=PLANNER_PROMPT)
        text = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()

        plan = json.loads(text)
        if "steps" not in plan or not isinstance(plan["steps"], list):
            raise ValueError("Invalid plan structure")

        for step in plan["steps"]:
            if step.get("tool") == "generated_code":
                log.warning("generated_code in step %s — replacing with web_search", step.get("step"))
                step["tool"]       = "web_search"
                step["parameters"] = {"query": step.get("description", goal)[:200]}

        log.info("Plan: %d steps", len(plan["steps"]))
        for s in plan["steps"]:
            log.debug("  Step %s: [%s] %s", s["step"], s["tool"], s["description"])
        return plan

    except json.JSONDecodeError as e:
        log.warning("JSON parse failed: %s", e)
        return _fallback_plan(goal)
    except Exception as e:
        log.warning("Planning failed: %s", e)
        return _fallback_plan(goal)


def _fallback_plan(goal: str) -> dict:
    log.info("Fallback plan")
    return {
        "goal":  goal,
        "steps": [
            {
                "step":        1,
                "tool":        "web_search",
                "description": f"Search for: {goal}",
                "parameters":  {"query": goal},
                "critical":    True,
            }
        ],
    }


def replan(goal: str, completed_steps: list, failed_step: dict, error: str) -> dict:
    completed_summary = "\n".join(
        f"  - Step {s['step']} ({s['tool']}): DONE" for s in completed_steps
    )
    prompt = f"""Goal: {goal}

Already completed:
{completed_summary if completed_summary else '  (none)'}

Failed step: [{failed_step.get('tool')}] {failed_step.get('description')}
Error: {error}

Create a REVISED plan for the remaining work only. Do not repeat completed steps."""

    try:
        text = call_llm_text(prompt, system=PLANNER_PROMPT)
        text = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()
        plan = json.loads(text)

        for step in plan.get("steps", []):
            if step.get("tool") == "generated_code":
                step["tool"]       = "web_search"
                step["parameters"] = {"query": step.get("description", goal)[:200]}

        log.info("Revised plan: %d steps", len(plan.get("steps", [])))
        return plan
    except Exception as e:
        log.warning("Replan failed: %s", e)
        return _fallback_plan(goal)
