import hashlib
import os

def checksum_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

def checksum_folder(folder_path: str) -> str:
    entries = []
    for root, dirs, files in os.walk(folder_path):
        dirs.sort()
        for filename in sorted(files):
            abs_path = os.path.join(root, filename)
            rel_path = os.path.relpath(abs_path, folder_path)
            # Normalize path separators for cross-platform determinism
            rel_path = rel_path.replace("\\", "/")
            entries.append(f"{rel_path}:{checksum_file(abs_path)}")
    combined = "\n".join(entries)
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()

def checksum_artifact(content_ref: str) -> tuple[str, int]:
    if os.path.isfile(content_ref):
        return checksum_file(content_ref), os.path.getsize(content_ref)
    checksum = checksum_folder(content_ref)
    size = sum(
        os.path.getsize(os.path.join(root, f))
        for root, _, files in os.walk(content_ref)
        for f in files
    )
    return checksum, size
