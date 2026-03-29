"""
Agent Pairing: Phân loại domain câu hỏi và chọn cặp agent phù hợp để tranh luận.

Mỗi câu hỏi brainstorming được phân loại theo domain (feature, ux, architecture, data, quality, security).
Tùy theo domain, hệ thống ghép 2 agent có góc nhìn đối lập để tạo ra tranh luận có chất lượng.
"""
from pathlib import Path

# Đường dẫn đến repo agency-agents (cùng workspace)
AGENTS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "agency-agents"

# Bảng ghép cặp agent theo domain câu hỏi
# Mỗi cặp được chọn dựa trên nguyên tắc "complementary tension":
# 2 agent có chuyên môn khác nhau, tự nhiên tạo ra góc nhìn đối lập
AGENT_PAIRINGS = {
    "feature": {
        "agent_a": {
            "role": "Product Manager",
            "prompt_path": "product/product-manager.md",
            "perspective": "Giá trị người dùng và business value",
        },
        "agent_b": {
            "role": "Backend Architect",
            "prompt_path": "engineering/engineering-backend-architect.md",
            "perspective": "Tính khả thi kỹ thuật và độ phức tạp",
        },
    },
    "ux": {
        "agent_a": {
            "role": "UX Designer",
            "prompt_path": "design/design-ux-designer.md",
            "perspective": "Trải nghiệm người dùng và usability",
        },
        "agent_b": {
            "role": "Product Manager",
            "prompt_path": "product/product-manager.md",
            "perspective": "Business constraints và feasibility",
        },
    },
    "architecture": {
        "agent_a": {
            "role": "Backend Architect",
            "prompt_path": "engineering/engineering-backend-architect.md",
            "perspective": "Thiết kế hệ thống lý tưởng và scalability",
        },
        "agent_b": {
            "role": "DevOps Specialist",
            "prompt_path": "engineering/engineering-devops-specialist.md",
            "perspective": "Vận hành thực tế: deploy, monitor, maintain",
        },
    },
    "data": {
        "agent_a": {
            "role": "Database Specialist",
            "prompt_path": "engineering/engineering-database-specialist.md",
            "perspective": "Data integrity, query optimization, schema design",
        },
        "agent_b": {
            "role": "Backend Architect",
            "prompt_path": "engineering/engineering-backend-architect.md",
            "perspective": "Toàn vẹn hệ thống và integration patterns",
        },
    },
    "quality": {
        "agent_a": {
            "role": "QA Engineer",
            "prompt_path": "testing/testing-qa-engineer.md",
            "perspective": "Test coverage và chất lượng sản phẩm",
        },
        "agent_b": {
            "role": "Backend Architect",
            "prompt_path": "engineering/engineering-backend-architect.md",
            "perspective": "Tốc độ delivery và pragmatic trade-offs",
        },
    },
    "security": {
        "agent_a": {
            "role": "Security Specialist",
            "prompt_path": "engineering/engineering-security-specialist.md",
            "perspective": "Bảo mật và compliance",
        },
        "agent_b": {
            "role": "Product Manager",
            "prompt_path": "product/product-manager.md",
            "perspective": "Usability và trải nghiệm người dùng",
        },
    },
}

# Từ khóa để phân loại domain câu hỏi
DOMAIN_KEYWORDS = {
    "feature": [
        "feature", "chức năng", "tính năng", "yêu cầu", "requirement",
        "user story", "scope", "priority", "mvp", "phạm vi",
    ],
    "ux": [
        "ux", "ui", "giao diện", "trải nghiệm", "design", "thiết kế",
        "navigation", "layout", "responsive", "mobile",
    ],
    "architecture": [
        "architecture", "kiến trúc", "microservice", "monolith", "api",
        "framework", "tech stack", "infrastructure", "deploy", "scale",
    ],
    "data": [
        "database", "db", "schema", "table", "sql", "nosql", "storage",
        "data model", "migration", "index", "query", "dữ liệu",
    ],
    "quality": [
        "test", "quality", "qa", "ci/cd", "pipeline", "coverage",
        "performance", "benchmark", "monitoring", "logging",
    ],
    "security": [
        "security", "auth", "authentication", "authorization", "bảo mật",
        "encrypt", "token", "session", "permission", "role", "oauth",
    ],
}

# Moderator agent — trung lập, tổng hợp kết quả
MODERATOR_CONFIG = {
    "role": "Business Analyst",
    "prompt_path": "product/product-business-analyst.md",
    "goal": "Tổng hợp tranh luận, xác định điểm đồng thuận và bất đồng, đưa ra khuyến nghị cuối cùng",
}


def load_agent_prompt(relative_path: str) -> str:
    """Đọc file prompt agent từ repo agency-agents."""
    filepath = AGENTS_DIR / relative_path
    if not filepath.exists():
        return (
            f"Bạn là chuyên gia trong lĩnh vực được giao. "
            f"(Lưu ý: file {relative_path} không tìm thấy trong agency-agents repo.)"
        )
    return filepath.read_text(encoding="utf-8")


def classify_question_domain(question: str) -> str:
    """Phân loại câu hỏi vào domain dựa trên từ khóa.

    Returns domain name (feature, ux, architecture, data, quality, security).
    Mặc định trả về 'feature' nếu không match domain nào.
    """
    question_lower = question.lower()
    scores = {}
    for domain, keywords in DOMAIN_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in question_lower)
        scores[domain] = score

    best_domain = max(scores, key=scores.get)
    if scores[best_domain] == 0:
        return "feature"
    return best_domain


def get_agent_pair(domain: str) -> dict:
    """Trả về config của cặp agent cho domain cụ thể."""
    if domain not in AGENT_PAIRINGS:
        domain = "feature"
    return AGENT_PAIRINGS[domain]


def get_moderator_config() -> dict:
    """Trả về config của Moderator agent."""
    return MODERATOR_CONFIG.copy()


def select_pair_for_question(question: str) -> dict:
    """Tiện ích: từ câu hỏi, trả về domain + cặp agent + moderator.

    Returns:
        {
            "question": str,
            "domain": str,
            "agent_a": {...},
            "agent_b": {...},
            "moderator": {...},
        }
    """
    domain = classify_question_domain(question)
    pair = get_agent_pair(domain)
    return {
        "question": question,
        "domain": domain,
        "agent_a": pair["agent_a"],
        "agent_b": pair["agent_b"],
        "moderator": get_moderator_config(),
    }


# ============================================================
# Demo / Test
# ============================================================

if __name__ == "__main__":
    test_questions = [
        "Nên dùng tech stack gì cho frontend?",
        "Database schema nên thiết kế như thế nào?",
        "Làm sao để đảm bảo bảo mật cho hệ thống authentication?",
        "Những feature nào cần có trong MVP?",
        "UI/UX của trang dashboard nên như thế nào?",
        "Hệ thống cần bao nhiêu test coverage?",
    ]

    print("=" * 60)
    print("AGENT PAIRING — TEST CLASSIFICATION")
    print("=" * 60)

    for q in test_questions:
        result = select_pair_for_question(q)
        print(f"\nQ: {q}")
        print(f"  Domain:  {result['domain']}")
        print(f"  Agent A: {result['agent_a']['role']} — {result['agent_a']['perspective']}")
        print(f"  Agent B: {result['agent_b']['role']} — {result['agent_b']['perspective']}")
