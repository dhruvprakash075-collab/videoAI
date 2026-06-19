from pathlib import Path


def is_safe_path(root_dir: Path, user_path: str) -> bool:
    """Check if the user-provided path is confined to the root directory.
    Args:
        root_dir: The root directory to confine paths to.
        user_path: The user-provided path to validate.
    Returns:
        bool: True if the path is safe, False otherwise.
    """
    try:
        root = root_dir.resolve()
        full_path = (root / user_path).resolve()
        return root in full_path.parents or root == full_path
    except (RuntimeError, FileNotFoundError):
        # Handle symlink loops or non-existent paths
        return False
