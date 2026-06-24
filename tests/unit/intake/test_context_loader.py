"""Tests for context_loader — heuristic project directory scanning."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_dev_system.intake.context_loader import (
    _scan_ci,
    _scan_docker,
    _scan_envfile,
    _scan_manifests,
    _scan_readme,
    scan_context_dir,
)
from ai_dev_system.intake.template import load_template


@pytest.fixture
def tpl():
    return load_template("generic_v1")


# ---------------------------------------------------------------------------
# _scan_manifests
# ---------------------------------------------------------------------------

class TestScanManifests:
    def test_package_json_sets_nodejs_stack(self, tmp_path):
        pkg = {"name": "myapp", "dependencies": {"express": "^4", "pg": "^8"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        result = _scan_manifests(tmp_path)
        assert "Node.js" in result["must_use_stack"]
        assert "express" in result["must_use_stack"] or "pg" in result["must_use_stack"]
        assert result["greenfield_or_brownfield"] == "brownfield"
        assert "JavaScript/TypeScript" in result["team_skills"]

    def test_requirements_txt_sets_python(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("fastapi\nsqlalchemy\npytest\n")
        result = _scan_manifests(tmp_path)
        assert "Python" in result["must_use_stack"]
        assert "fastapi" in result["must_use_stack"] or "sqlalchemy" in result["must_use_stack"]
        assert result["greenfield_or_brownfield"] == "brownfield"

    def test_go_mod_sets_go(self, tmp_path):
        (tmp_path / "go.mod").write_text("module example.com/myapp\n\ngo 1.21\n")
        result = _scan_manifests(tmp_path)
        assert "Go" in result["must_use_stack"]
        assert result["greenfield_or_brownfield"] == "brownfield"

    def test_cargo_toml_sets_rust(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text('[package]\nname = "myapp"\nversion = "0.1.0"\n')
        result = _scan_manifests(tmp_path)
        assert "Rust" in result["must_use_stack"]

    def test_empty_dir_returns_empty(self, tmp_path):
        result = _scan_manifests(tmp_path)
        assert result == {}

    def test_no_duplicate_stack_items(self, tmp_path):
        (tmp_path / "package.json").write_text('{"name":"a","dependencies":{"express":"^4"}}')
        result = _scan_manifests(tmp_path)
        assert len(result["must_use_stack"]) == len(set(result["must_use_stack"]))


# ---------------------------------------------------------------------------
# _scan_docker
# ---------------------------------------------------------------------------

class TestScanDocker:
    def test_docker_compose_detects_postgres(self, tmp_path):
        compose = """
version: "3"
services:
  db:
    image: postgres:15
  cache:
    image: redis:7
"""
        (tmp_path / "docker-compose.yml").write_text(compose)
        result = _scan_docker(tmp_path)
        assert "PostgreSQL" in result.get("data_sources", [])
        assert "Redis" in result.get("data_sources", [])
        assert "Docker" in result.get("deployment_target", "")

    def test_dockerfile_sets_deployment_target(self, tmp_path):
        (tmp_path / "Dockerfile").write_text("FROM python:3.12\n")
        result = _scan_docker(tmp_path)
        assert "Docker" in result.get("deployment_target", "")

    def test_empty_dir_returns_empty(self, tmp_path):
        result = _scan_docker(tmp_path)
        assert result == {}


# ---------------------------------------------------------------------------
# _scan_envfile
# ---------------------------------------------------------------------------

class TestScanEnvfile:
    def test_detects_postgres_from_database_url(self, tmp_path):
        (tmp_path / ".env.example").write_text("DATABASE_URL=postgres://localhost/mydb\n")
        result = _scan_envfile(tmp_path)
        assert "PostgreSQL" in result.get("data_sources", [])

    def test_detects_redis_from_redis_url(self, tmp_path):
        (tmp_path / ".env.example").write_text("REDIS_URL=redis://localhost:6379\n")
        result = _scan_envfile(tmp_path)
        assert "Redis" in result.get("data_sources", [])

    def test_detects_jwt_auth(self, tmp_path):
        (tmp_path / ".env.example").write_text("JWT_SECRET=changeme\nJWT_EXPIRY=3600\n")
        result = _scan_envfile(tmp_path)
        assert result.get("existing_auth") is not None
        assert "JWT" in result["existing_auth"]

    def test_ignores_comments_and_blanks(self, tmp_path):
        content = "# This is a comment\n\nDATABASE_URL=postgres://\n"
        (tmp_path / ".env.example").write_text(content)
        result = _scan_envfile(tmp_path)
        assert "PostgreSQL" in result.get("data_sources", [])

    def test_empty_dir_returns_empty(self, tmp_path):
        result = _scan_envfile(tmp_path)
        assert result == {}


# ---------------------------------------------------------------------------
# _scan_ci
# ---------------------------------------------------------------------------

class TestScanCi:
    def test_detects_aws_from_github_actions(self, tmp_path):
        wf_dir = tmp_path / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "deploy.yml").write_text("uses: aws-actions/configure-aws-credentials@v4\n")
        result = _scan_ci(tmp_path)
        assert result.get("deployment_target") == "AWS"

    def test_detects_gcp(self, tmp_path):
        wf_dir = tmp_path / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "deploy.yml").write_text("uses: google-github-actions/setup-gcloud@v2\n")
        result = _scan_ci(tmp_path)
        assert result.get("deployment_target") == "GCP"

    def test_detects_terraform(self, tmp_path):
        tf_dir = tmp_path / "terraform"
        tf_dir.mkdir()
        (tf_dir / "main.tf").write_text('provider "aws" {}\n')
        result = _scan_ci(tmp_path)
        assert "Terraform" in result.get("deployment_target", "")

    def test_empty_dir_returns_empty(self, tmp_path):
        result = _scan_ci(tmp_path)
        assert result == {}


# ---------------------------------------------------------------------------
# _scan_readme
# ---------------------------------------------------------------------------

class TestScanReadme:
    def test_heuristic_extracts_first_paragraph(self, tmp_path):
        readme = "# My Project\n\nThis is a warehouse management system for real-time inventory tracking.\n\n## Features\n- Track items\n"
        (tmp_path / "README.md").write_text(readme)
        result = _scan_readme(tmp_path, llm=None)
        assert "problem_statement" in result
        assert "warehouse" in result["problem_statement"].lower() or "inventory" in result["problem_statement"].lower()

    def test_no_readme_returns_empty(self, tmp_path):
        result = _scan_readme(tmp_path, llm=None)
        assert result == {}

    def test_readme_with_only_heading_skips(self, tmp_path):
        (tmp_path / "README.md").write_text("# Title\n\n![badge](url)\n")
        result = _scan_readme(tmp_path, llm=None)
        # Should not crash; may return empty or skip badge-only paragraphs
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# scan_context_dir (integration)
# ---------------------------------------------------------------------------

class TestScanContextDir:
    def test_full_node_project(self, tmp_path, tpl):
        pkg = {"name": "shop", "dependencies": {"express": "^4", "pg": "^8"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        (tmp_path / ".env.example").write_text("DATABASE_URL=postgres://localhost/shop\nJWT_SECRET=x\n")
        compose = "version: '3'\nservices:\n  db:\n    image: postgres:15\n  cache:\n    image: redis:7\n"
        (tmp_path / "docker-compose.yml").write_text(compose)
        (tmp_path / "README.md").write_text("# Shop\n\nOnline shop platform for SMBs.\n")

        result = scan_context_dir(tmp_path, tpl)

        assert "must_use_stack" in result
        assert "Node.js" in result["must_use_stack"]
        assert result.get("greenfield_or_brownfield") == "brownfield"
        assert "PostgreSQL" in result.get("data_sources", [])

    def test_empty_dir_returns_empty(self, tmp_path, tpl):
        result = scan_context_dir(tmp_path, tpl)
        assert result == {}

    def test_only_valid_field_ids_returned(self, tmp_path, tpl):
        pkg = {"name": "a", "dependencies": {"express": "^4"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        result = scan_context_dir(tmp_path, tpl)
        valid_ids = {f.id for f in tpl.fields}
        for fid in result:
            assert fid in valid_ids, f"Unknown field id returned: {fid}"

    def test_no_duplicate_list_values(self, tmp_path, tpl):
        # package.json + requirements.txt both present → no duplicate stack items
        (tmp_path / "package.json").write_text('{"name":"a","dependencies":{"express":"^4"}}')
        (tmp_path / "requirements.txt").write_text("fastapi\n")
        result = scan_context_dir(tmp_path, tpl)
        stack = result.get("must_use_stack", [])
        assert len(stack) == len(set(stack))
