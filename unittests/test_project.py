from my_own_accelerator import create_empty_project


def test_create_empty_project_default() -> None:
    project = create_empty_project()
    assert project == {"name": "my-own-accelerator", "files": [], "dependencies": []}


def test_create_empty_project_custom_name() -> None:
    project = create_empty_project("demo")
    assert project["name"] == "demo"
    assert project["files"] == []
    assert project["dependencies"] == []
