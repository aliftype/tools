from ufo2ft.filters import BaseFilter


class ClearPlaceholdersFilter(BaseFilter):
    _kwargs = {"outlines": False, "anchors": True, "width": True}

    def set_context(self, font, glyphSet):
        context = super().set_context(font, glyphSet)
        context.font = font
        return context

    def filter(self, glyph):
        outlines = self.options.outlines
        anchors = self.options.anchors
        width = self.options.width
        upem = self.context.font.info.unitsPerEm
        if glyph.lib.get("com.schriftgestaltung.Glyphs.category") == "Placeholder":
            if outlines:
                glyph.clearContours()
                glyph.clearComponents()
            if anchors:
                glyph.clearAnchors()
            if width:
                glyph.width = upem
            return outlines or anchors or width
        return False
