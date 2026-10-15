"""One template version everywhere.

``TEMPLATE_VERSION`` is not cosmetic: a client project's template-drift CI derives
the tag of the merged template release from it (``v<TEMPLATE_VERSION>``) and
checks that tag is an ancestor of HEAD. A stale value points the check at the
wrong release. So the package version, the constant and the newest CHANGELOG
release must agree.
"""

import re
import tomllib
from pathlib import Path

from shared import TEMPLATE_VERSION

_ROOT = Path(__file__).resolve().parent.parent


def test_template_version_matches_package_version() -> None:
    pyproject = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["version"] == TEMPLATE_VERSION


def test_template_version_is_the_newest_changelog_release() -> None:
    changelog = (_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    releases = re.findall(r"^## \[(\d+\.\d+\.\d+)\]", changelog, flags=re.MULTILINE)
    assert releases, "CHANGELOG has no released sections"
    assert releases[0] == TEMPLATE_VERSION
