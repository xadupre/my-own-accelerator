"""Project helpers."""


def create_empty_project(name: str = "my-own-accelerator") -> dict[str, object]:
    """Creates an in-memory empty project descriptor."""
    return {"name": name, "files": [], "dependencies": []}
