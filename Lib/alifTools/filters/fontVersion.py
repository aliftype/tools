from ufo2ft.filters import BaseFilter


class FontVersionFilter(BaseFilter):
    _kwargs = {"fontVersion": 1}

    def __call__(self, font, glyphSet=None):
        context = self.set_context(font, glyphSet)

        fontVersion = self.options.fontVersion
        if isinstance(fontVersion, str):
            if "-" in fontVersion:
                fontVersion = fontVersion.split("-")[0]
            if fontVersion.startswith("v"):
                fontVersion = fontVersion[1:]
            fontVersion = float(fontVersion)

        versionMajor = int(fontVersion)
        versionMinor = int(round((fontVersion - versionMajor) * 1000))

        font.info.versionMajor = versionMajor
        font.info.versionMinor = versionMinor

        return context.modified
