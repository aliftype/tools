# Copyright 2026 Khaled Hosny
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Strip glyph names from a font"""

import argparse
from fontTools.ttLib import TTFont


def main(argv=None):
    parser = argparse.ArgumentParser(description="Strip glyph names from a font.")
    parser.add_argument("font", help="input font")
    parser.add_argument("-o", "--output", help="output font", required=True)
    args = parser.parse_args(argv)

    font = TTFont(args.font, lazy=True, recalcBBoxes=False, recalcTimestamp=False)
    font["post"].formatType = 3.0
    font.save(args.output)
