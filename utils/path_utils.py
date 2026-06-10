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
def is_symlink(path: Path) -> bool:
    """Check if the path is a symlink.
    Args:
        path: The path to check.
    Returns:
        bool: True if the path is a symlink, False otherwise.
    """
    return path.is_symlink()
def is_regular_file(path: Path) -> bool:
    """Check if the path is a regular file.
    Args:
        path: The path to check.
    Returns:
        bool: True if the path is a regular file, False otherwise.
    """
    return path.is_file()
def is_regular_dir(path: Path) -> bool:
    """Check if the path is a regular directory.
    Args:
        path: The path to check.
    Returns:
        bool: True if the path is a regular directory, False otherwise.
    """
    return path.is_dir()
def validate_file_size(path: Path, max_size: int) -> bool:
    """Check if the file size is within the allowed limit.
    Args:
        path: The file path to check.
        max_size: Maximum allowed size in bytes.
    Returns:
        bool: True if the file size is within limits, False otherwise.
    """
    try:
        return path.stat().st_size <= max_size
    except (OSError, FileNotFoundError):
        return False
def safe_resolve(path: Path) -> Path:
    """Resolve a path while rejecting symlinks.
    Args:
        path: The path to resolve.
    Returns:
        Path: The resolved path.
    Raises:
        ValueError: If the path is a symlink or resolution fails.
    """
    if is_symlink(path):
        raise ValueError(f"Symlinks are not allowed: {path}")
    try:
        return path.resolve()
    except (RuntimeError, FileNotFoundError) as e:
        raise ValueError(f"Path resolution failed: {path}") from e
def read_file_safely(file_path: Path, max_size: int = 1048576) -> str:
    """Read a file safely, ensuring it exists, is a regular file, and is size-limited.
    Args:
        file_path: Path to the file to read.
        max_size: Maximum allowed file size in bytes (default: 1MB).
    Returns:
        str: The file content.
    Raises:
        ValueError: If the path is unsafe, not a regular file, or exceeds size limits.
    """
    if not is_regular_file(file_path):
        raise ValueError(f"Path is not a regular file: {file_path}")
    if not validate_file_size(file_path, max_size):
        raise ValueError(f"File size exceeds limit ({max_size} bytes): {file_path}")
    return file_path.read_text(encoding="utf-8")
def read_json_safely(file_path: Path, max_size: int = 1048576) -> dict:
    """Read and parse a JSON file safely.
    Args:
        file_path: Path to the JSON file to read.
        max_size: Maximum allowed file size in bytes (default: 1MB).
    Returns:
        dict: The parsed JSON content.
    Raises:
        ValueError: If the path is unsafe, not a regular file, exceeds size limits, or is invalid JSON.
    """
    import json
    content = read_file_safely(file_path, max_size)
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in file: {file_path}") from e
def write_file_safely(file_path: Path, content: str, root_dir: Path | None = None) -> None:
    """Write content to a file safely, ensuring the target directory exists and is confined.
    Args:
        file_path: Path to the file to write.
        content: Content to write.
        root_dir: Optional root directory to confine the path to.
    Raises:
        ValueError: If the path is unsafe, escapes root_dir, or is not a regular directory.
    """
    if root_dir is not None:
        root_abs = root_dir.resolve()
        file_abs = file_path.resolve()
        try:
            # Check if file_abs is under root_abs
            file_abs.relative_to(root_abs)
        except ValueError:
            raise ValueError(f"Path escapes root directory: {file_path}") from None

    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
def create_directory_safely(dir_path: Path, root_dir: Path | None = None) -> None:
    """Create a directory safely, ensuring it is confined to the root directory if provided.
    Args:
        dir_path: Path to the directory to create.
        root_dir: Optional root directory to confine the path to.
    Raises:
        ValueError: If the path escapes root_dir.
    """
    if root_dir is not None:
        root_abs = root_dir.resolve()
        dir_abs = dir_path.resolve()
        try:
            # Check if dir_abs is under root_abs
            dir_abs.relative_to(root_abs)
        except ValueError:
            raise ValueError(f"Path escapes root directory: {dir_path}") from None

    dir_path.mkdir(parents=True, exist_ok=True)
