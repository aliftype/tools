from alifTools.filters import FontVersionFilter
from ufoLib2 import Font


def test_font_version_filter():
    font = Font()
    FontVersionFilter(fontVersion=1.002)(font)
    assert font.info.versionMajor == 1
    assert font.info.versionMinor == 2

    font = Font()
    FontVersionFilter(fontVersion="1.002")(font)
    assert font.info.versionMajor == 1
    assert font.info.versionMinor == 2

    font = Font()
    FontVersionFilter(fontVersion="1.002-43aa05")(font)
    assert font.info.versionMajor == 1
    assert font.info.versionMinor == 2

    font = Font()
    FontVersionFilter(fontVersion="v1.002")(font)
    assert font.info.versionMajor == 1
    assert font.info.versionMinor == 2

    font = Font()
    FontVersionFilter(fontVersion="v1.002-43aa05")(font)
    assert font.info.versionMajor == 1
    assert font.info.versionMinor == 2
