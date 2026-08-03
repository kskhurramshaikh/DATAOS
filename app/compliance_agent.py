# Compliance Agent -- hard-rule tier
#
# This is a first, deliberately small slice of the compliance agent
# described in the DataOS 2.0 design: it evaluates an intent's context
# against governance rules and returns a decision *before* the router is
# ever consulted. Nothing gets routed to a tool without passing through
# here first.
#
# This version only implements the hard-rule tier (the OPA-equivalent
# layer for rules that reduce to a clean check). The semantic tier --
# retrieval over an uploaded policy corpus for anything that doesn't
# reduce to a clean rule -- is not built yet; it comes later, once this
# rail is proven and a real policy corpus exists to retrieve from.


class ComplianceDecision:
    def __init__(self, allowed: bool, applied_rules: list[str], notes: list[str]):
        self.allowed = allowed
        self.applied_rules = applied_rules
        self.notes = notes

    def to_dict(self):
        return {
            "allowed": self.allowed,
            "applied_rules": self.applied_rules,
            "notes": self.notes,
        }


def evaluate(intent: str, context: dict) -> ComplianceDecision:
    classification = context.get("dataset_classification", "UNSPECIFIED")
    applied_rules: list[str] = []
    notes: list[str] = []

    # Hard rule 1: RESTRICTED-classified data requires an explicit,
    # already-approved override before any intent can touch it.
    if classification == "RESTRICTED" and not context.get("override_approved"):
        applied_rules.append("restricted-data-requires-override")
        notes.append(
            "Dataset is classified RESTRICTED; this intent is blocked "
            "without an approved override flag on the request."
        )
        return ComplianceDecision(False, applied_rules, notes)

    # Hard rule 2: every validate_drift run must be logged for audit,
    # regardless of classification. This doesn't block anything -- it
    # demonstrates that even an "allowed" path still produces a
    # governance record, not a silent pass-through.
    if intent == "validate_drift":
        applied_rules.append("validate-drift-requires-audit-log")

    notes.append(
        f"Dataset classification '{classification}' cleared for intent "
        f"'{intent}'."
    )
    return ComplianceDecision(True, applied_rules, notes)
