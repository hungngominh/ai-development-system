import os
from ai_dev_system.spec_bundle import REQUIRED_FILES


def read_spec_bundle(content_ref: str) -> dict[str, str]:
    """Read spec bundle files from promoted artifact path."""
    result = {}
    for filename in REQUIRED_FILES:
        path = os.path.join(content_ref, filename)
        if os.path.exists(path):
            with open(path) as f:
                result[filename] = f.read()
        else:
            result[filename] = ""
    return result
