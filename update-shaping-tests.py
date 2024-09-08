# Copyright 2020 Google Sans Authors
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

"""Update a regression test file with the shaping output of a list of fonts."""

from __future__ import annotations

import enum
import sys
import yaml
from pathlib import Path
from typing import Any, Dict, List, Optional, TypedDict

import vharfbuzz as vhb  # type: ignore
from fontTools.ttLib import TTFont  # type: ignore
from fontTools.ttLib.tables._f_v_a_r import table__f_v_a_r  # type: ignore


def main(args: List[str] | None = None) -> None:
    import argparse
    import json

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "shaping_file", type=Path, help="The .yaml shaping definition input file path."
    )
    parser.add_argument(
        "output_file",
        type=Path,
        help="The .json shaping expectations output file path.",
    )
    parser.add_argument(
        "fonts",
        nargs="+",
        type=Path,
        help="The fonts to update the testing file with.",
    )
    parsed_args = parser.parse_args(args)

    input_path: Path = parsed_args.shaping_file
    output_path: Path = parsed_args.output_file
    fonts: List[Path] = parsed_args.fonts

    shaping_input = load_shaping_input(input_path)
    shaping_output = update_shaping_output(shaping_input, fonts)
    output_path.write_text(json.dumps(shaping_output, indent=2, ensure_ascii=False))


def update_shaping_output(
    shaping_input: ShapingInputYaml, font_paths: List[Path]
) -> ShapingOutput:
    tests: List[TestDefinition] = []
    shaping_output = {"tests": tests}
    if "configuration" in shaping_input:
        shaping_output["configuration"] = shaping_input["configuration"]

    for font_path in font_paths:
        shaper = vhb.Vharfbuzz(font_path)
        font = TTFont(font_path)
        for text in shaping_input["input"]["text"]:
            if "fvar" in font and "variations" not in shaping_input["input"]:
                fvar: table__f_v_a_r = font["fvar"]  # type: ignore
                for instance in fvar.instances:
                    run = shape_run(
                        shaper,
                        font_path,
                        text,
                        shaping_input["input"],
                        instance.coordinates,
                    )
                    tests.append(run)
            else:
                run = shape_run(shaper, font_path, text, shaping_input["input"])
                tests.append(run)

    return shaping_output


def shape_run(
    shaper: vhb.Vharfbuzz,
    font_path: Path,
    text: str,
    shaping_input: ShapingInput,
    variations: Optional[Dict[str, float]] = None,
) -> TestDefinition:
    parameters: VHarfbuzzParameters = {}
    if (script := shaping_input.get("script")) is not None:
        parameters["script"] = script
    if (direction := shaping_input.get("direction")) is not None:
        parameters["direction"] = direction.value
    if (language := shaping_input.get("language")) is not None:
        parameters["language"] = language
    if features := shaping_input.get("features"):
        parameters["features"] = features
    if variations:
        parameters["variations"] = variations
    elif variations := shaping_input.get("variations"):
        parameters["variations"] = variations
    buffer = shaper.shape(text, parameters)

    shaping_comparison_mode = shaping_input["comparison_mode"]
    if shaping_comparison_mode is ComparisonMode.FULL:
        glyphsonly = False
    elif shaping_comparison_mode is ComparisonMode.GLYPHSTREAM:
        glyphsonly = True
    else:
        raise ValueError(f"Unknown comparison mode {shaping_comparison_mode}.")
    expectation = shaper.serialize_buf(buffer, glyphsonly)

    test_definition: TestDefinition = {
        "only": font_path.name,
        "input": text,
        "expectation": expectation,
        **parameters,
    }

    return test_definition


def load_shaping_input(input_path: Path) -> ShapingInputYaml:
    with input_path.open("rb") as tf:
        shaping_input: ShapingInputYaml = yaml.safe_load(tf)

    if "input" not in shaping_input:
        raise ValueError(f"{input_path} does not contain a valid shaping input.")

    input_definition = shaping_input["input"]
    input_definition["text"] = input_definition.get("text", [])
    input_definition["script"] = input_definition.get("script")
    input_definition["language"] = input_definition.get("language")
    input_definition["direction"] = (
        Direction(input_definition["direction"])
        if "direction" in input_definition
        else None
    )
    input_definition["features"] = input_definition.get("features", {})
    input_definition["comparison_mode"] = ComparisonMode(
        input_definition.get("comparison_mode", "full")
    )

    shaping_input["input"] = input_definition
    if configuration := shaping_input.get("configuration"):
        shaping_input["configuration"] = configuration

    return shaping_input


class ShapingInputYaml(TypedDict):
    configuration: NotRequired[Dict[str, Any]]
    input: ShapingInput


class ShapingInput(TypedDict):
    text: List[str]
    script: Optional[str]
    language: Optional[str]
    direction: Optional[Direction]
    features: Dict[str, bool]
    comparison_mode: ComparisonMode


class ComparisonMode(enum.Enum):
    FULL = "full"  # Record glyph names, offsets and advance widths.
    GLYPHSTREAM = "glyphstream"  # Just glyph names.


class Direction(enum.Enum):
    LEFT_TO_RIGHT = "ltr"
    RIGHT_TO_LEFT = "rtl"
    TOP_TO_BOTTOM = "ttb"
    BOTTOM_TO_TOP = "btt"


class ShapingOutput(TypedDict):
    configuration: NotRequired[Dict[str, Any]]
    tests: List[TestDefinition]


class VHarfbuzzParameters(TypedDict, total=False):
    script: str
    direction: str
    language: str
    features: Dict[str, bool]
    variations: Dict[str, float]


class TestDefinition(VHarfbuzzParameters):
    input: str
    expectation: str
    only: NotRequired[str]


if __name__ == "__main__":
    main()
