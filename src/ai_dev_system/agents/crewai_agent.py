import concurrent.futures
import os
from typing import Optional

import crewai

from ai_dev_system.agents.base import AgentResult, PromotedOutput


class CrewAIAgent:
    """Production agent that delegates task execution to a CrewAI crew."""

    def __init__(self, llm_model: str, llm_api_key: str, verbose: bool = False) -> None:
        self._llm_model = llm_model  # LiteLLM-style, e.g. "anthropic/claude-opus-4-5"
        self._llm_api_key = llm_api_key
        self._verbose = verbose

    def run(
        self,
        task_id: str,
        output_path: str,
        promoted_outputs=(),
        context: Optional[dict] = None,
        timeout_s: float = 3600.0,
        file_rules: list = (),
    ) -> AgentResult:
        # Step 1: ensure output directory exists
        os.makedirs(output_path, exist_ok=True)

        # Step 2: extract task description
        task_description: str
        if context is not None and isinstance(context.get("task_description"), str) and context["task_description"]:
            task_description = context["task_description"]
        elif context is not None and isinstance(context.get("SPEC_BUNDLE"), str) and os.path.isdir(context["SPEC_BUNDLE"]):
            spec_dir = context["SPEC_BUNDLE"]
            md_files = sorted(
                f for f in os.listdir(spec_dir) if f.endswith(".md")
            )[:3]
            contents = []
            for fname in md_files:
                try:
                    with open(os.path.join(spec_dir, fname), "r", encoding="utf-8") as fh:
                        contents.append(fh.read())
                except OSError:
                    pass
            task_description = "\n\n".join(contents)[:3000]
            if not task_description.strip():
                task_description = (
                    f"Task {task_id}: implement the required functionality "
                    f"and write output files to {output_path}"
                )
        else:
            task_description = (
                f"Task {task_id}: implement the required functionality and write output files to {output_path}"
            )

        # Step 3: build promoted output names
        promoted_list = list(promoted_outputs)
        file_names = [po.name for po in promoted_list] if promoted_list else ["output.txt"]

        # Step 4: build implementation task description
        impl_description = (
            f"{task_description}\n\n"
            f"Write ALL output files to this exact directory: {output_path}\n"
            f"Required output files: {file_names}"
        )

        # Step 5: create LLM object
        llm = crewai.LLM(model=self._llm_model, api_key=self._llm_api_key)

        # Step 6: create CrewAI Agent objects
        coder_backstory = "Expert software developer who writes clean, working code and delivers files to the specified output directory."
        if file_rules:
            coder_backstory += "\n\nApply these rules:\n" + "\n".join(f"- {r}" for r in file_rules)
        coder = crewai.Agent(
            role="Software Engineer",
            goal=f"Implement the development task: {task_description[:200]}",
            backstory=coder_backstory,
            llm=llm,
            allow_delegation=False,
            verbose=self._verbose,
        )
        reviewer = crewai.Agent(
            role="QA Engineer",
            goal="Review the implementation and verify it meets all requirements",
            backstory="Expert reviewer focused on correctness, completeness, and code quality.",
            llm=llm,
            allow_delegation=False,
            verbose=self._verbose,
        )

        # Step 7: create CrewAI Task objects
        impl_task = crewai.Task(
            description=impl_description,
            expected_output=f"All required files written to {output_path}",
            agent=coder,
        )
        review_task = crewai.Task(
            description=f"Review the implementation files in {output_path} and confirm they are correct and complete.",
            expected_output="Review summary confirming the implementation is correct and all required files exist.",
            agent=reviewer,
        )

        # Step 8: create Crew
        crew = crewai.Crew(
            agents=[coder, reviewer],
            tasks=[impl_task, review_task],
            process=crewai.Process.sequential,
            verbose=self._verbose,
        )

        # Step 9: execute with timeout
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = executor.submit(crew.kickoff)
        try:
            future.result(timeout=timeout_s)
        except concurrent.futures.TimeoutError:
            # Python threads cannot be forcefully killed; the background thread
            # may continue running. shutdown(wait=False) ensures we don't block here.
            executor.shutdown(wait=False)
            return AgentResult(
                output_path=output_path,
                error=f"CrewAI execution timed out after {timeout_s}s",
            )
        except Exception as exc:
            executor.shutdown(wait=False)
            return AgentResult(output_path=output_path, error=str(exc))
        executor.shutdown(wait=True)  # Clean shutdown on success

        # Step 10: verify promoted outputs exist
        for po in promoted_list:
            file_path = os.path.join(output_path, po.name)
            if not os.path.exists(file_path):
                return AgentResult(
                    output_path=output_path,
                    error=f"Missing promoted output: {po.name}",
                )

        # Step 11: return success
        return AgentResult(output_path=output_path, promoted_outputs=promoted_list)


def make_crewai_agent() -> CrewAIAgent:
    """Factory that builds a CrewAIAgent from environment variables."""
    provider = os.environ.get("LLM_PROVIDER", "")
    model = os.environ.get("LLM_MODEL", "")
    if not provider or provider not in ("anthropic", "openai"):
        raise RuntimeError("LLM_PROVIDER must be 'anthropic' or 'openai'")
    if not model:
        raise RuntimeError("LLM_MODEL is required")
    if provider == "anthropic":
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        litellm_model = f"anthropic/{model}"
    else:
        key = os.environ.get("OPENAI_API_KEY", "")
        litellm_model = model
    if not key:
        raise RuntimeError(f"{provider.upper()}_API_KEY is required")
    return CrewAIAgent(llm_model=litellm_model, llm_api_key=key)
