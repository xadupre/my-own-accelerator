import runpy
from unittest.mock import patch

from moa.ext_test_case import ExtTestCase


class TestMain(ExtTestCase):
    def test_package_main_executes_review_pr_main(self) -> None:
        with (
            patch("moa.commands.review_pr.main", return_value=0) as mocked,
            self.assertRaises(SystemExit) as ctx,
        ):
            runpy.run_module("moa", run_name="__main__")
        self.assertEqual(ctx.exception.code, 0)
        mocked.assert_called_once_with()
