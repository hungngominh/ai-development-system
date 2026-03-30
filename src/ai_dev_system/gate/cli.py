import copy


class CLIGateIO:
    """Interactive terminal Gate IO. Presents brief, allows field editing."""

    def present(self, brief: dict) -> None:
        print("\n=== Initial Brief ===")
        print(f"  Raw Idea (immutable): {brief['raw_idea']}")
        print(f"  Problem: {brief['problem'] or '(not specified)'}")
        print(f"  Target Users: {brief['target_users'] or '(not specified)'}")
        print(f"  Goal: {brief['goal'] or '(not specified)'}")
        hard = brief.get("constraints", {}).get("hard", [])
        soft = brief.get("constraints", {}).get("soft", [])
        print(f"  Hard Constraints: {hard or '(none)'}")
        print(f"  Soft Constraints: {soft or '(none)'}")
        scope = brief.get("scope", {})
        print(f"  Scope: type={scope.get('type')}, complexity={scope.get('complexity_hint')}")
        print(f"  Assumptions: {brief.get('assumptions', []) or '(none)'}")
        print(f"  Success Signals: {brief.get('success_signals', []) or '(none)'}")
        print()

    def collect_edit(self, brief: dict) -> dict:
        updated = copy.deepcopy(brief)
        print("Edit fields (press Enter to keep current, type new value to change):")
        for field in ["problem", "target_users", "goal"]:
            current = updated[field] or "(empty)"
            val = input(f"  {field} [{current}]: ").strip()
            if val:
                updated[field] = val
        val = input(f"  scope.type [{updated['scope']['type']}]: ").strip()
        if val:
            updated["scope"]["type"] = val
        val = input(f"  scope.complexity [{updated['scope']['complexity_hint']}]: ").strip()
        if val:
            updated["scope"]["complexity_hint"] = val
        return updated

    def confirm(self, brief: dict) -> bool:
        self.present(brief)
        answer = input("Confirm this brief? (y/n): ").strip().lower()
        return answer in ("y", "yes")
