from my_own_accelerator import create_empty_project
from unittests.ext_test_case import ExtTestCase


class TestProject(ExtTestCase):
    def test_create_empty_project_default(self) -> None:
        project = create_empty_project()
        self.assertEqual(project, {"name": "my-own-accelerator", "files": [], "dependencies": []})

    def test_create_empty_project_custom_name(self) -> None:
        project = create_empty_project("demo")
        self.assertEqual(project["name"], "demo")
        self.assertEqual(project["files"], [])
        self.assertEqual(project["dependencies"], [])
