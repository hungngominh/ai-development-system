"""context_loader.py — Scan an existing project directory to pre-fill intake brief fields.

Heuristic scanners (no LLM needed):
  - package.json / requirements.txt / go.mod / Cargo.toml / pom.xml  → must_use_stack, team_skills
  - docker-compose.yml / Dockerfile                                   → data_sources, deployment_target
  - .env.example / .env.sample                                        → data_sources, existing_auth
  - .github/workflows/*.yml / terraform/                              → deployment_target
  - Any manifest present                                              → greenfield_or_brownfield=brownfield

LLM scanner (only when llm is provided):
  - README.md / README.rst (first 3000 chars)                        → problem_statement, scope_in, scope_out

Returns: dict[field_id, value] — caller injects into IntakeState.answers as source="context_loaded".
"""
from __future__ import annotations

import json
import re
import warnings
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scan_context_dir(
    path: Path,
    template,  # Template — used to validate field ids exist
    llm=None,
) -> dict[str, Any]:
    """Scan *path* for clues and return {field_id: value} for recognized fields.

    Only fields present in *template* are included. Unknown field ids are silently
    dropped so the loader stays forward-compatible with template changes.
    """
    result: dict[str, Any] = {}

    _merge(result, _scan_manifests(path))
    _merge(result, _scan_docker(path))
    _merge(result, _scan_envfile(path))
    _merge(result, _scan_ci(path))
    _merge(result, _scan_readme(path, llm))

    # Filter to fields that actually exist in the template
    valid_ids = {f.id for f in template.fields}
    filtered = {k: v for k, v in result.items() if k in valid_ids}
    return filtered


# ---------------------------------------------------------------------------
# Heuristic scanners
# ---------------------------------------------------------------------------

def _scan_manifests(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {}
    stack: list[str] = []
    skills: list[str] = []

    pkg = path / "package.json"
    if pkg.exists():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
            stack.append("Node.js")
            skills.append("JavaScript/TypeScript")
            deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
            notable = _top_notable_npm(deps)
            stack.extend(notable)
        except Exception:
            stack.append("Node.js")

    req = path / "requirements.txt"
    if req.exists():
        try:
            lines = req.read_text(encoding="utf-8").splitlines()
            pkgs = [_pkg_name(l) for l in lines if l.strip() and not l.startswith("#")]
            stack.append("Python")
            skills.append("Python")
            stack.extend(_top_notable_pip(pkgs))
        except Exception:
            stack.append("Python")

    pyproject = path / "pyproject.toml"
    if pyproject.exists() and "Python" not in stack:
        stack.append("Python")
        skills.append("Python")

    gomod = path / "go.mod"
    if gomod.exists():
        stack.append("Go")
        skills.append("Go")

    cargo = path / "Cargo.toml"
    if cargo.exists():
        stack.append("Rust")
        skills.append("Rust")

    pom = path / "pom.xml"
    if pom.exists():
        stack.extend(["Java", "Maven"])
        skills.append("Java")

    gemfile = path / "Gemfile"
    if gemfile.exists():
        stack.extend(["Ruby", "Rails"])
        skills.append("Ruby")

    # .NET / C# — detect via .sln or .csproj files
    sln_files = list(path.glob("*.sln"))
    csproj_files = list(path.glob("**/*.csproj"))[:1]  # at least one
    if sln_files or csproj_files:
        # Try to detect .NET version from Dockerfile base image
        dotnet_version = _detect_dotnet_version(path)
        stack.append(f".NET{f' {dotnet_version}' if dotnet_version else ''}")
        stack.append("C#")
        skills.append("C#/.NET")

    if stack:
        result["must_use_stack"] = _dedup(stack)
        result["greenfield_or_brownfield"] = "brownfield"
    if skills:
        result["team_skills"] = _dedup(skills)

    return result


def _scan_docker(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {}
    data_sources: list[str] = []

    compose = path / "docker-compose.yml"
    if not compose.exists():
        compose = path / "docker-compose.yaml"

    if compose.exists():
        try:
            text = compose.read_text(encoding="utf-8")
            services = _detect_compose_services(text)
            data_sources.extend(services)
            result["deployment_target"] = "Docker / containerized"
        except Exception:
            pass

    dockerfile = path / "Dockerfile"
    if dockerfile.exists() and "deployment_target" not in result:
        result["deployment_target"] = "Docker / containerized"

    if data_sources:
        result["data_sources"] = _dedup(data_sources)

    return result


def _scan_envfile(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {}
    data_sources: list[str] = []
    auth_hints: list[str] = []

    for candidate in (".env.example", ".env.sample", ".env.template"):
        envfile = path / candidate
        if envfile.exists():
            try:
                for line in envfile.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    key = line.split("=")[0].strip().upper()
                    if any(k in key for k in ("DATABASE_URL", "POSTGRES", "MYSQL", "MONGO", "REDIS_URL")):
                        label = _db_label_from_key(key)
                        if label:
                            data_sources.append(label)
                    if any(k in key for k in ("AUTH_", "JWT_", "SESSION_", "OAUTH_", "SSO_", "SAML_")):
                        label = _auth_label_from_key(key)
                        if label and label not in auth_hints:
                            auth_hints.append(label)
            except Exception:
                pass
            break  # only read first found

    if data_sources:
        result["data_sources"] = _dedup(data_sources)
    if auth_hints:
        result["existing_auth"] = ", ".join(auth_hints)

    return result


def _scan_ci(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {}

    # terraform
    if (path / "terraform").is_dir() or list(path.glob("*.tf")):
        result["deployment_target"] = "Terraform-managed cloud"
        return result

    # Jenkinsfile → Docker + Jenkins CI
    if (path / "Jenkinsfile").exists():
        result["deployment_target"] = "Docker / Jenkins CI"
        return result

    # GitHub Actions
    gha_dir = path / ".github" / "workflows"
    if gha_dir.is_dir():
        for wf in gha_dir.glob("*.yml"):
            try:
                text = wf.read_text(encoding="utf-8").lower()
                if "aws-actions" in text or "amazonaws" in text:
                    result["deployment_target"] = "AWS"
                    break
                if "google-github-actions" in text or "gcloud" in text:
                    result["deployment_target"] = "GCP"
                    break
                if "azure/" in text or "azure-webapps" in text:
                    result["deployment_target"] = "Azure"
                    break
            except Exception:
                pass

    return result


def _detect_dotnet_version(path: Path) -> str | None:
    dockerfile = path / "Dockerfile"
    if dockerfile.exists():
        try:
            for line in dockerfile.read_text(encoding="utf-8").splitlines():
                m = re.search(r"dotnet(?:/sdk|/aspnet|/runtime)?:(\d+(?:\.\d+)?)", line, re.IGNORECASE)
                if m:
                    return m.group(1)
        except Exception:
            pass
    return None


# ---------------------------------------------------------------------------
# README scanner
# ---------------------------------------------------------------------------

_README_SYSTEM = """\
You are a technical analyst. Given the start of a project README, extract:
- problem_statement: 1-3 sentences describing the core problem this project solves (not features list)
- scope_in: list of 3-7 main capabilities/features the project provides
- scope_out: list of things explicitly NOT in scope (may be empty list if not mentioned)

Return ONLY a JSON object with keys "problem_statement" (string), "scope_in" (list of strings), "scope_out" (list of strings).
No markdown fences, no extra text."""

_README_USER_TPL = "README content:\n\n{content}\n\nExtract the brief fields."


def _scan_readme(path: Path, llm=None) -> dict[str, Any]:
    readme_text = _read_readme(path)
    if not readme_text:
        return {}

    if llm is not None:
        return _scan_readme_with_llm(readme_text, llm)
    return _scan_readme_heuristic(readme_text)


def _scan_readme_with_llm(text: str, llm) -> dict[str, Any]:
    content = text[:3000]
    try:
        raw = llm.complete(_README_SYSTEM, _README_USER_TPL.format(content=content))
        cleaned = re.sub(r"```(?:json)?\s*(.*?)\s*```", r"\1", raw, flags=re.DOTALL).strip()
        parsed = json.loads(cleaned)
    except Exception as exc:
        warnings.warn(f"context_loader: README LLM scan failed ({exc}); using heuristic fallback")
        return _scan_readme_heuristic(text)

    result: dict[str, Any] = {}
    ps = parsed.get("problem_statement")
    if isinstance(ps, str) and ps.strip():
        result["problem_statement"] = ps.strip()
    si = parsed.get("scope_in")
    if isinstance(si, list) and si:
        result["scope_in"] = [str(x).strip() for x in si if str(x).strip()]
    so = parsed.get("scope_out")
    if isinstance(so, list) and so:
        result["scope_out"] = [str(x).strip() for x in so if str(x).strip()]
    return result


def _scan_readme_heuristic(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    paragraphs = [p.strip() for p in re.split(r"\n\n+", text) if p.strip()]
    for para in paragraphs:
        lines = [
            l for l in para.splitlines()
            if l.strip()
            and not l.strip().startswith("#")   # headings
            and not l.strip().startswith(">")   # blockquotes / metadata
            and not l.strip().startswith("|")   # tables
            and "![" not in l                   # badges
            and not re.match(r"^\s*[-*]\s+\[", l)  # checklist items
        ]
        text_block = " ".join(lines).strip()
        if len(text_block) > 40:
            result["problem_statement"] = text_block[:500]
            break
    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_readme(path: Path) -> str:
    # Check root and common doc subdirs
    candidates = [
        path / "README.md", path / "README.rst", path / "README.txt", path / "README",
        path / "docs" / "PRD.md", path / "docs" / "README.md",
    ]
    for f in candidates:
        if f.exists():
            try:
                return f.read_text(encoding="utf-8", errors="replace")
            except Exception:
                pass
    return ""


_NOTABLE_NPM = {
    "react", "next", "vue", "nuxt", "angular", "svelte",
    "express", "fastify", "koa", "nestjs", "@nestjs/core",
    "prisma", "typeorm", "sequelize", "mongoose",
    "graphql", "apollo-server", "trpc",
    "postgres", "pg", "mysql2", "redis",
    "jest", "vitest", "playwright", "cypress",
}

def _top_notable_npm(deps: dict) -> list[str]:
    found = [k for k in deps if k.lower() in _NOTABLE_NPM or k.lower().lstrip("@").split("/")[0] in _NOTABLE_NPM]
    return sorted(found)[:5]


_NOTABLE_PIP = {
    "django", "flask", "fastapi", "starlette", "tornado",
    "sqlalchemy", "alembic", "peewee",
    "celery", "redis", "aioredis",
    "pandas", "numpy", "scikit-learn", "torch", "tensorflow",
    "pytest", "httpx", "requests",
}

def _top_notable_pip(pkgs: list[str]) -> list[str]:
    found = [p for p in pkgs if p.lower() in _NOTABLE_PIP]
    return sorted(found)[:5]


def _pkg_name(line: str) -> str:
    return re.split(r"[>=<!~^;]", line)[0].strip()


_COMPOSE_SERVICE_PATTERNS = [
    (r"\bpostgres\b|\bpostgresql\b", "PostgreSQL"),
    (r"\bmysql\b|\bmariadb\b", "MySQL"),
    (r"\bmongodb\b|\bmongo\b", "MongoDB"),
    (r"\bredis\b", "Redis"),
    (r"\belasticsearch\b", "Elasticsearch"),
    (r"\bkafka\b", "Kafka"),
    (r"\brabbitmq\b", "RabbitMQ"),
    (r"\bminio\b", "MinIO / S3"),
    (r"\bnginx\b", "Nginx"),
]

def _detect_compose_services(text: str) -> list[str]:
    text_lower = text.lower()
    found = []
    for pattern, label in _COMPOSE_SERVICE_PATTERNS:
        if re.search(pattern, text_lower):
            found.append(label)
    return found


def _db_label_from_key(key: str) -> str | None:
    if "POSTGRES" in key or "DATABASE_URL" in key:
        return "PostgreSQL"
    if "MYSQL" in key:
        return "MySQL"
    if "MONGO" in key:
        return "MongoDB"
    if "REDIS" in key:
        return "Redis"
    return None


def _auth_label_from_key(key: str) -> str | None:
    if "OAUTH" in key:
        return "OAuth"
    if "JWT" in key:
        return "JWT"
    if "SAML" in key:
        return "SAML/SSO"
    if "SESSION" in key:
        return "Session-based auth"
    return None


def _dedup(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _merge(base: dict, extra: dict) -> None:
    for k, v in extra.items():
        if k not in base:
            base[k] = v
        elif isinstance(base[k], list) and isinstance(v, list):
            base[k] = _dedup(base[k] + v)
