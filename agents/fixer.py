"""Fixer agent: suggests changes. It NEVER edits files or touches infrastructure."""

# Ordered smallest -> largest within each instance family (placeholder ladder).
SIZE_LADDER = {
    "m5": ["m5.large", "m5.xlarge", "m5.2xlarge"],
    "c5": ["c5.xlarge"],
    "r5": ["r5.large"],
    "t3": ["t3.micro", "t3.medium"],
}


def next_smaller(instance_type):
    """Return the next smaller size in the same family, or None."""
    family = instance_type.split(".")[0]
    ladder = SIZE_LADDER.get(family, [])
    if instance_type in ladder:
        index = ladder.index(instance_type)
        if index > 0:
            return ladder[index - 1]
    return None


def suggest_fix(finding):
    """Return a human-readable suggestion dict for a Finding, or None if no action."""
    if finding.status == "idle":
        action = "Review for shutdown or schedule it to run only when needed"
    elif finding.status == "underused":
        smaller = next_smaller(finding.instance_type)
        if not smaller:
            return None
        action = "Consider right-sizing from %s to %s" % (finding.instance_type, smaller)
    else:
        return None

    return {
        "resource_id": finding.resource_id,
        "status": finding.status,
        "suggestion": action,
        "auto_apply": False,  # suggestions are advisory only
    }
