from alifTools.filters import FontVersionFilter
from ufoLib2 import Font
import pytest


@pytest.mark.parametrize(
    "fontVersion,major,minor",
    [
        (1.002, 1, 2),
        ("1.002", 1, 2),
        ("1.002-43aa05", 1, 2),
        ("v1.002", 1, 2),
        ("v1.002-43aa05", 1, 2),
        ("1.9", 1, 900),
    ],
)
def test_font_version_filter(fontVersion, major, minor):
    font = Font()
    FontVersionFilter(fontVersion=fontVersion)(font)
    assert font.info.versionMajor == major
    assert font.info.versionMinor == minor
