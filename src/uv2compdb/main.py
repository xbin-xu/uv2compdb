"""
Generate Compilation Database by parse Keil uVision project.
"""

from __future__ import annotations

import json
import logging
import argparse
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable
from functools import partial, cached_property
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname).1s] %(message)s",
)


def _to_posix_path(path: str) -> str:
    """Convert Windows path separators to POSIX format."""
    return path.replace("\\", "/")


def _split_and_strip(text: str, delimiter: str) -> list[str]:
    """Split text by delimiter and strip whitespace from each part."""
    return [item.strip() for item in text.split(delimiter) if item.strip()]


@dataclass(frozen=True)
class CommandObject:
    directory: str
    file: str
    arguments: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class VariousControls:
    """
    Various Controls Levels: Target, Group, File

    Various Controls Rules:
        OPTIONS      = INCLUDE_PATH + MISC + DEFINE
        INCLUDE_PATH = File.include_path + Group.include_path
                       + Target.include_path
        MISC         = Target.misc_controls + Group.misc_controls
                       + File.misc_controls
        DEFINE       = Target.undefine + Target.define
                       + Group.undefine + Group.define
                       + File.undefine + File.define
    """

    misc_controls: list[str] = field(default_factory=list)
    define: list[str] = field(default_factory=list)
    undefine: list[str] = field(default_factory=list)
    include_path: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return " ".join(self.get_options())

    def get_options(self) -> list[str]:
        # 'MBEDTLS_CONFIG_FILE=/\"config-aes-cbc.h/\"'
        #    => '-DMBEDTLS_CONFIG_FILE="config-aes-cbc.h"'
        return (
            [f"-I{_to_posix_path(x)}" for x in self.include_path]
            + [f"{_to_posix_path(x)}" for x in self.misc_controls]
            + [f"-U{_to_posix_path(x)}" for x in self.undefine]
            + [f"-D{_to_posix_path(x.replace(r'\"', '"'))}" for x in self.define]
        )

    @classmethod
    def merge(cls, parent: VariousControls, child: VariousControls) -> VariousControls:
        return cls(
            misc_controls=parent.misc_controls + child.misc_controls,
            define=parent.undefine + parent.define + child.undefine + child.define,
            undefine=[],
            include_path=child.include_path + parent.include_path,
        )


class UV2CompDB:
    """Keil uVision project parser."""

    # TODO: how to deal with delimiters inside text (e.g., -DFOO="(1, 2)")
    UV_VARIOUS_CONTROLS_MAP: dict[str, tuple[str, Callable[[str], list[str]]]] = {
        "MiscControls": ("misc_controls", partial(_split_and_strip, delimiter=" ")),
        "Define": ("define", partial(_split_and_strip, delimiter=",")),
        "Undefine": ("undefine", partial(_split_and_strip, delimiter=",")),
        "IncludePath": ("include_path", partial(_split_and_strip, delimiter=";")),
    }

    UV_TOOLCHAIN_MAP: dict[str, str] = {
        "0x00": "c51",
        "0x40": "armcc",
        "0x41": "armclang",
    }

    def __init__(self, project_path: Path) -> None:
        self.project_path: Path = project_path

    @cached_property
    def root(self) -> ET.Element:
        tree = ET.parse(self.project_path)
        return tree.getroot()

    @cached_property
    def targets(self) -> dict[str, ET.Element]:
        targets = {}
        for target in self.root.findall(".//Target"):
            target_name = self._get_text(target.find("TargetName"))
            if target_name is not None:
                targets[target_name] = target
        return targets

    def _get_text(
        self, elem: ET.Element | None, pred: Callable[[str], list[str]] | None = None
    ) -> str | list[str] | None:
        if elem is None or elem.text is None:
            return None
        return pred(elem.text) if pred else elem.text

    def get_various_controls(self, elem: ET.Element | None) -> VariousControls | None:
        if elem is None:
            return None

        include_in_build = self._get_text(elem.find(".//CommonProperty/IncludeInBuild"))
        # None: True, "0": False, "1": True, "2": inherit
        if include_in_build == "0":
            return None

        result = {}
        for name, (var_name, pred) in self.UV_VARIOUS_CONTROLS_MAP.items():
            result[var_name] = (
                self._get_text(elem.find(f".//Cads/VariousControls/{name}"), pred) or []
            )
        return VariousControls(**result)

    def get_toolchain(self, target: ET.Element) -> str | None:
        toolset_number = self._get_text(target.find("ToolsetNumber"))
        if toolset_number is None:
            return None

        uac6 = self._get_text(target.find("uAC6"))
        key = toolset_number + (uac6 if uac6 else "")
        return self.UV_TOOLCHAIN_MAP.get(key)

    def parse(self, target_name: str) -> list[CommandObject]:
        target = self.targets.get(target_name)
        if target is None:
            logger.warning(f"Not found target: {target_name}")
            return []

        toolchain = self.get_toolchain(target)
        logger.info(f"Toolchain: {toolchain}")

        target_vc = self.get_various_controls(target)
        if target_vc is None:
            logger.warning(f"Not found target_controls in target: {target_name}")
            return []

        command_objects = []
        for group in target.findall(".//Group"):
            group_vc = self.get_various_controls(group)
            if group_vc is None:
                continue

            current_vc = VariousControls.merge(target_vc, group_vc)
            for file in group.findall(".//File"):
                file_path = self._get_text(file.find("FilePath"))
                # file_type = self._get_text(file.find("FileType"))

                if not file_path or not file_path.endswith(
                    (".s", ".c", ".cpp", ".cc", ".cx", ".cxx")
                ):
                    continue

                file_controls = self.get_various_controls(file)
                if file_controls is None:
                    continue

                file = _to_posix_path(file_path)
                command_objects.append(
                    CommandObject(
                        directory=self.project_path.resolve().parent.as_posix(),
                        file=file,
                        arguments=(
                            [_to_posix_path(toolchain)]
                            + VariousControls.merge(
                                current_vc, file_controls
                            ).get_options()
                            + ["-c"]
                            + [file]
                        ),
                    )
                )
                # logger.debug(f"command_object: {command_objects[-1]}")
        return command_objects


def generate_compile_commands(
    command_objects: list[CommandObject],
    output: Path,
    extra_arguments: str | None,
) -> bool:
    if not command_objects:
        logger.warning("No command objects")
        return False

    objs = [asdict(obj) for obj in command_objects]
    if extra_arguments:
        extra_args = _split_and_strip(_to_posix_path(extra_arguments), delimiter=" ")
        for obj in objs:
            obj["arguments"].extend(extra_args)

    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        json.dump(objs, f, indent=4, ensure_ascii=False)

    logger.info(f"Generate at {output.resolve().as_posix()}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate compile_commands.json by parse Keil uVision project"
    )
    parser.add_argument("-a", "--arguments", default=None, help="add extra arguments")
    parser.add_argument("-t", "--target", default=None, help="target name")
    parser.add_argument(
        "-o",
        "--output",
        default="compile_commands.json",
        help="output dir/file path (default: compile_commands.json)",
    )
    parser.add_argument("project", type=Path, help="path to .uvproj[x] file")

    args = parser.parse_args()

    try:
        uv2compdb = UV2CompDB(args.project)

        targets = list(uv2compdb.targets.keys())
        if not targets:
            logger.error("No targets found in project")
            return 1

        if not args.target:
            args.target = targets[0]
            logger.warning(
                f"Project has multi targets: {targets}, use the first {args.target}"
            )
        elif args.target not in targets:
            logger.error(f"Not found target: {args.target}")
            return 1

        output_path = Path(args.output)
        if args.output.endswith(("/", "\\")) or (
            output_path.exists() and output_path.is_dir()
        ):
            args.output = output_path / "compile_commands.json"
        else:
            args.output = output_path

        command_objects = uv2compdb.parse(args.target)
        generate_compile_commands(command_objects, args.output, args.arguments)
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        return 1

    return 0
