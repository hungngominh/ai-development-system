from ai_dev_system.debate.domains import (
    DEFAULT_DOMAIN,
    DOMAIN_ALIASES,
    DOMAINS,
    resolve_domain,
)


def test_resolve_canonical_id_hit():
    canonical, recognized = resolve_domain("backend")
    assert canonical == "backend"
    assert recognized is True


def test_resolve_alias_hit():
    canonical, recognized = resolve_domain("react")
    assert canonical == "frontend"
    assert recognized is True


def test_resolve_unknown_defaults_to_backend():
    canonical, recognized = resolve_domain("blockchain")
    assert canonical == DEFAULT_DOMAIN
    assert canonical == "backend"
    assert recognized is False


def test_resolve_case_insensitive():
    canonical, recognized = resolve_domain("KUBERNETES")
    assert canonical == "infra"
    assert recognized is True


def test_resolve_strips_whitespace():
    canonical, recognized = resolve_domain("  product  ")
    assert canonical == "product"
    assert recognized is True


def test_domain_count_is_fifteen():
    assert len(DOMAINS) == 15


def test_product_behavioral_domains_present():
    for d in ("psychology", "growth", "research"):
        assert d in DOMAINS


def test_new_aliases_resolve():
    from ai_dev_system.debate.domains import resolve_domain
    assert resolve_domain("behavior") == ("psychology", True)
    assert resolve_domain("retention") == ("growth", True)
    assert resolve_domain("user-research") == ("research", True)


def test_aliases_resolve_to_canonical_ids_only():
    for alias, target in DOMAIN_ALIASES.items():
        assert target in DOMAINS, f"alias {alias!r} → {target!r} is not a canonical domain"
