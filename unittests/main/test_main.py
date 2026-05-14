import runpy
from unittest.mock import patch

from my_own_accelerator.ext_test_case import ExtTestCase


class TestMain(ExtTestCase):
    def test_package_main_executes_review_pr_main(self) -> None:
        with (
            patch("my_own_accelerator.commands.review_pr.main", return_value=0) as mocked,
            self.assertRaises(SystemExit) as ctx,
        ):
            runpy.run_module("my_own_accelerator", run_name="__main__")
        self.assertEqual(ctx.exception.code, 0)
        mocked.assert_called_once_with()
