"""
MARK XL — Agent Executor
Replaces google.generativeai with local Ollama via core.llm_client.
"""
import datetime
import json
import random
import re
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from agent.error_handler import ErrorDecision, analyze_error, generate_fix
from agent.planner import create_plan, replan
from core.llm_client import call_llm_text
from core.logger import get_logger
from core.paths import BASE_DIR

log = get_logger("executor")


# ---------------------------------------------------------------------------
# Code generation helper (replaces _run_generated_code with Gemini)
# ---------------------------------------------------------------------------

def _load_exec_config() -> bool:
    """Returns allow_code_execution flag from config (default False — fail closed)."""
    from core.security import code_execution_allowed
    return code_execution_allowed()


def _run_generated_code(description: str, speak: Callable | None = None) -> str:
    if speak:
        speak("Writing custom code for this task, sir.")

    home      = Path.home()
    desktop   = home / "Desktop"
    downloads = home / "Downloads"
    documents = home / "Documents"

    if not desktop.exists():
        try:
            import winreg
            key     = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
            )
            desktop = Path(winreg.QueryValueEx(key, "Desktop")[0])
        except Exception:
            pass

    system = (
        "You are an expert Python developer. "
        "Write clean, complete, working Python code. "
        "Use standard library + common packages. "
        "Install missing packages with subprocess + pip if needed. "
        "Return ONLY the Python code. No explanation, no markdown, no backticks.\n\n"
        f"SYSTEM PATHS:\n"
        f"  Desktop   = r'{desktop}'\n"
        f"  Downloads = r'{downloads}'\n"
        f"  Documents = r'{documents}'\n"
        f"  Home      = r'{home}'\n"
    )
    prompt = f"Write Python code to accomplish this task:\n\n{description}"

    try:
        code = call_llm_text(prompt, system=system)
        code = re.sub(r"```(?:python)?", "", code).strip().rstrip("`").strip()

        # Always persist to a visible audit folder — never anonymous temp files
        audit_dir = home / "JarvisGeneratedCode"
        audit_dir.mkdir(parents=True, exist_ok=True)
        stamp      = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        audit_path = audit_dir / f"task_{stamp}.py"
        audit_path.write_text(
            f"# Task: {description}\n# Generated: {stamp}\n\n{code}",
            encoding="utf-8",
        )
        log.info("Code saved for review: %s", audit_path)
        log.debug("Generated code (first 500 chars):\n%s", code[:500])

        allow_exec = _load_exec_config()
        if not allow_exec:
            msg = (
                f"Code written to {audit_path} — auto-execution disabled. "
                'Set "allow_code_execution": true in config/api_keys.json to enable, sir.'
            )
            if speak:
                speak("Code saved to JarvisGeneratedCode for your review, sir.")
            return msg

        log.info("Running: %s", audit_path)
        result = subprocess.run(
            [sys.executable, str(audit_path)],
            capture_output=True, text=True,
            timeout=120, cwd=str(home),
        )

        output = result.stdout.strip()
        error  = result.stderr.strip()

        if result.returncode == 0 and output:
            return output
        elif result.returncode == 0:
            return "Task completed successfully."
        elif error:
            raise RuntimeError(f"Code error: {error[:400]}")
        return "Completed."

    except subprocess.TimeoutExpired:
        raise RuntimeError("Generated code timed out after 120 seconds.")
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"Generated code failed: {e}")


# ---------------------------------------------------------------------------
# Context injection
# ---------------------------------------------------------------------------

def _detect_language(text: str) -> str:
    try:
        return call_llm_text(
            f"What language is this text written in? "
            f"Reply with ONLY the language name in English (e.g. Turkish, English, French).\n\n"
            f"Text: {text[:200]}"
        ).strip()
    except Exception:
        return "English"


def _translate_to_goal_language(content: str, goal: str) -> str:
    if not goal:
        return content
    try:
        target_lang = _detect_language(goal)
        log.info("Translating to: %s", target_lang)
        prompt = (
            f"You are a professional translator. "
            f"Translate the following text into {target_lang}.\n"
            f"IMPORTANT:\n"
            f"- Translate EVERYTHING, leave nothing in English\n"
            f"- Keep all facts, numbers, and data intact\n"
            f"- Keep the structure and formatting\n"
            f"- Output ONLY the translated text, nothing else\n\n"
            f"Text to translate:\n{content[:4000]}"
        )
        translated = call_llm_text(prompt)
        log.info("Translation done (%s)", target_lang)
        return translated
    except Exception as e:
        log.warning("Translation failed: %s", e)
        return content


def _inject_context(params: dict, tool: str, step_results: dict, goal: str = "") -> dict:
    if not step_results:
        return params
    params = dict(params)
    if tool == "file_controller" and params.get("action") in ("write", "create_file"):
        content = params.get("content", "")
        if not content or len(content) < 50:
            all_results = [
                v for v in step_results.values()
                if v and len(v) > 100 and v not in ("Done.", "Completed.")
            ]
            if all_results:
                combined   = "\n\n---\n\n".join(all_results)
                translated = _translate_to_goal_language(combined, goal)
                params["content"] = translated
                log.info("Injected + translated content")
    return params


# ---------------------------------------------------------------------------
# Tool routing
# ---------------------------------------------------------------------------

def _call_tool(tool: str, parameters: dict, speak: Callable | None) -> str:
    """Route a single tool call through the canonical dispatcher.

    ``generated_code`` is preserved as an explicit, security-gated path; any
    other unregistered tool now returns a clean error instead of silently
    executing LLM-generated code.
    """
    if tool == "generated_code":
        description = parameters.get("description", "")
        if not description:
            raise ValueError("generated_code requires a 'description' parameter.")
        return _run_generated_code(description, speak=speak)

    from core.tool_runtime import ToolContext, dispatch

    ctx = ToolContext(player=None, speak=speak)
    try:
        return dispatch(tool, parameters, ctx)
    except KeyError:
        log.warning("Unknown tool '%s' — no fallback", tool)
        return f"Unknown tool: {tool}"


# ---------------------------------------------------------------------------
# AgentExecutor
# ---------------------------------------------------------------------------

class AgentExecutor:

    MAX_REPLAN_ATTEMPTS = 3

    def execute(
        self,
        goal:          str,
        speak:         Callable | None        = None,
        cancel_flag:   threading.Event | None = None,
        on_step_start: Callable | None        = None,
    ) -> str:
        log.info("Goal: %s", goal)

        replan_attempts = 0
        completed_steps: list = []
        step_results:    dict = {}
        plan = create_plan(goal)

        while True:
            steps = plan.get("steps", [])
            if not steps:
                msg = "I couldn't create a valid plan for this task, sir."
                if speak: speak(msg)
                return msg

            success      = True
            failed_step  = None
            failed_error = ""

            for step in steps:
                if cancel_flag and cancel_flag.is_set():
                    if speak: speak("Task cancelled, sir.")
                    return "Task cancelled."

                step_num = step.get("step", "?")
                tool     = step.get("tool", "generated_code")
                desc     = step.get("description", "")
                params   = step.get("parameters", {})
                params   = _inject_context(params, tool, step_results, goal=goal)

                log.info("Step %s: [%s] %s", step_num, tool, desc)
                if on_step_start:
                    try:
                        on_step_start(step_num, tool, desc)
                    except Exception:
                        pass

                attempt = 1
                step_ok = False

                while attempt <= 3:
                    if cancel_flag and cancel_flag.is_set():
                        break
                    try:
                        result = _call_tool(tool, params, speak)
                        step_results[step_num] = result
                        completed_steps.append(step)
                        log.info("Step %s done: %s", step_num, str(result)[:100])
                        step_ok = True
                        break

                    except Exception as e:
                        error_msg = str(e)
                        log.error("Step %s attempt %d failed: %s", step_num, attempt, error_msg)

                        recovery = analyze_error(step, error_msg, attempt=attempt)
                        decision = recovery["decision"]
                        user_msg = recovery.get("user_message", "")

                        if speak and user_msg:
                            speak(user_msg)

                        if decision == ErrorDecision.RETRY:
                            attempt += 1
                            import time
                            time.sleep(min(2 ** attempt, 30) + random.uniform(0, 1))
                            continue

                        elif decision == ErrorDecision.SKIP:
                            log.info("Skipping step %s", step_num)
                            completed_steps.append(step)
                            step_ok = True
                            break

                        elif decision == ErrorDecision.ABORT:
                            msg = f"Task aborted, sir. {recovery.get('reason', '')}"
                            if speak: speak(msg)
                            return msg

                        else:  # REPLAN
                            fix_suggestion = recovery.get("fix_suggestion", "")
                            if fix_suggestion and tool != "generated_code":
                                try:
                                    fixed_step = generate_fix(step, error_msg, fix_suggestion)
                                    if speak: speak("Trying an alternative approach, sir.")
                                    res = _call_tool(
                                        fixed_step["tool"],
                                        fixed_step["parameters"],
                                        speak,
                                    )
                                    step_results[step_num] = res
                                    completed_steps.append(step)
                                    step_ok = True
                                    break
                                except Exception as fix_err:
                                    log.warning("Fix failed: %s", fix_err)

                            failed_step  = step
                            failed_error = error_msg
                            success      = False
                            break

                if not step_ok and not failed_step:
                    failed_step  = step
                    failed_error = "Max retries exceeded"
                    success      = False

                if not success:
                    break

            if success:
                return self._summarize(goal, completed_steps, speak)

            if replan_attempts >= self.MAX_REPLAN_ATTEMPTS:
                msg = f"Task failed after {replan_attempts} replan attempts, sir."
                if speak: speak(msg)
                return msg

            if speak: speak("Adjusting my approach, sir.")
            replan_attempts += 1
            plan = replan(goal, completed_steps, failed_step, failed_error)

    def _summarize(self, goal: str, completed_steps: list, speak: Callable | None) -> str:
        fallback  = f"All done, sir. Completed {len(completed_steps)} steps for: {goal[:60]}."
        steps_str = "\n".join(f"- {s.get('description', '')}" for s in completed_steps)
        prompt    = (
            f'User goal: "{goal}"\n'
            f"Completed steps:\n{steps_str}\n\n"
            "Write a single natural sentence summarising what was accomplished. "
            "Address the user as 'sir'. Be direct and positive."
        )
        try:
            summary = call_llm_text(prompt)
            if summary:
                if speak: speak(summary)
                return summary
        except Exception:
            pass
        if speak: speak(fallback)
        return fallback
