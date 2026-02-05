from __future__ import annotations

import re
import json
import shlex
import shutil
import logging
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable
from functools import partial, cached_property, lru_cache
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)

PREDEFINED_REGEX = re.compile(r"^#define\s+(\S+)(?:\s+(.*))?")
TOOLCHAIN_REGEX = re.compile(
    r"Toolchain Path:\s+([^\n]+)\nC Compiler:\s+(\S+)[^\n]+\nAssembler:\s+(\S+)"
)
DEP_F_REGEX = re.compile(r"F\s\(([^)]+)\)\([^)]+\)\(([^)]+)\)")
DEP_I_REGEX = re.compile(r"I\s\(([^)]+)\)\([^)]+\)")
C_VERSION_REGEX = re.compile(r"^--c(\d+)$")
ARMCC_UNKNOWN_ARGUMENT_REGEX = [
    (re.compile(r"^--gnu$"), False),
    (re.compile(r"^--c\d+$"), False),
    (re.compile(r"^--cpp$"), False),
    (re.compile(r"^--cpu$"), True),
    (re.compile(r"^--apcs="), False),
    (re.compile(r"^--split_sections$"), False),
    (re.compile(r"^--omf_browse$"), True),
    (re.compile(r"^--depend$"), True),
    (re.compile(r"^--diag_suppress="), True),
]
PREDEFINED_FILTER_ARGUMENT_REGEX = [
    (re.compile(r"^-o$"), True),
    (re.compile(r"^--omf_browse$"), True),
    (re.compile(r"^--depend$"), True),
    (re.compile(r"^-I"), False),
    (re.compile(r"^-D"), False),
    (re.compile(r"^-MD$"), False),
    (re.compile(r"^-MMD$"), False),
]


def to_posix_path(path: str) -> str:
    """Convert Windows path separators to POSIX format."""
    return path.replace("\\", "/")


def split_and_strip(text: str, delimiter: str) -> list[str]:
    """Split text by delimiter and strip whitespace from each part."""
    return [striped for item in text.split(delimiter) if (striped := item.strip())]


@dataclass(frozen=True)
class Toolchain:
    path: str
    compiler: str
    assembler: str
    xml_tag: tuple[str, str]


@dataclass
class FileObject:
    file: str
    arguments: list[str] = field(default_factory=list)


@dataclass
class TargetSetting:
    name: str
    toolchain: Toolchain
    file_objects: list[FileObject] = field(default_factory=list)


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
            [f"-I{to_posix_path(x)}" for x in self.include_path]
            + [f"{to_posix_path(x)}" for x in self.misc_controls]
            + [f"-U{to_posix_path(x)}" for x in self.undefine]
            + ["-D" + to_posix_path(x.replace(r'\\"', '"')) for x in self.define]
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
    """Keil µVision project parser."""

    # TODO: how to deal with delimiters inside text (e.g., -DFOO="(1, 2)")
    UV_VARIOUS_CONTROLS_MAP: dict[str, tuple[str, Callable[[str], list[str]]]] = {
        "MiscControls": ("misc_controls", partial(split_and_strip, delimiter=" ")),
        "Define": ("define", partial(split_and_strip, delimiter=",")),
        "Undefine": ("undefine", partial(split_and_strip, delimiter=",")),
        "IncludePath": ("include_path", partial(split_and_strip, delimiter=";")),
    }

    UV_C51_XML_TAG: tuple[str, str] = ("C51", "Ax51")
    UV_ARM_XML_TAG: tuple[str, str] = ("Cads", "Aads")

    # Language-Extensions: https://developer.arm.com/documentation/101655/0961/Cx51-User-s-Guide/Language-Extensions?lang=en
    UV_C51_EXTENSION_KEYWORDS: dict[str, str] = {
        # Data Type
        "bit": "unsigned char",
        "sbit": "volatile unsigned char",
        "sfr": "volatile unsigned char",
        "sfr16": "volatile unsigned short",
        # Memory Models
        "small": "",
        "compact": "",
        "large": "",
        # Memory Type
        "bdata": "",
        "data": "",
        "idata": "",
        "pdata": "",
        "xdata": "",
        "far": "",
        "code": "",
        # Other
        "_at_": "",
        "alien": "",
        "interrupt": "",
        "_priority_": "",
        "reentrant": "",
        "_task_": "",
        "using": "",
    }

    UV_TOOLCHAIN_MAP: dict[str, Toolchain] = {
        "0x00": Toolchain("", "c51", "a51", UV_C51_XML_TAG),
        "0x40": Toolchain("", "armcc", "armasm", UV_ARM_XML_TAG),
        "0x41": Toolchain("", "armclang", "armasm", UV_ARM_XML_TAG),
    }

    UV_CLI_ERRORLEVEL_MAP: dict[int, str] = {
        0: "No Errors or Warnings",
        1: "Warnings Only",
        2: "Errors",
        3: "Fatal Errors",
        11: "Cannot open project file for writing",
        12: "Device with given name is not found in database",
        13: "Error writing project file",
        15: "Error reading import XML file",
        20: "Error converting project",
    }

    def __init__(self, project_path: Path) -> None:
        self.project_path: Path = project_path

    @cached_property
    def root(self) -> ET.Element:
        tree = ET.parse(self.project_path)
        return tree.getroot()

    @cached_property
    def targets(self) -> dict[str, ET.Element]:
        return {
            target_name: target
            for target in self.root.findall(".//Target")
            if (target_name := self._get_text(target.find("TargetName")))
        }

    def _get_text(self, elem: ET.Element | None) -> str | None:
        if elem is None or elem.text is None:
            return None
        return elem.text

    def get_various_controls(
        self, elem: ET.Element | None, xml_tag: str | None
    ) -> VariousControls | None:
        if elem is None or not xml_tag:
            return None

        # None: True, "0": False, "1": True, "2": inherit
        if self._get_text(elem.find(".//CommonProperty/IncludeInBuild")) == "0":
            return None

        result = {}
        for name, (var_name, pred) in self.UV_VARIOUS_CONTROLS_MAP.items():
            text = self._get_text(elem.find(f".//{xml_tag}/VariousControls/{name}"))
            result[var_name] = pred(text) if text else []
        return VariousControls(**result)

    def try_build(self, target: ET.Element | None) -> bool:
        if target is None:
            return False

        if not (target_name := self._get_text(target.find("TargetName"))):
            return False

        # See: https://developer.arm.com/documentation/101407/0543/Command-Line
        if not (uv4_path := shutil.which("uv4")):
            return False

        cmd = [
            uv4_path,
            "-b",
            "-t",
            target_name,
            self.project_path.resolve().as_posix(),
            "-j0",
        ]
        logger.info(f"Run: `{subprocess.list2cmdline(cmd)}`")
        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
            logger.info(
                f"Exit Code: {result.returncode}({self.UV_CLI_ERRORLEVEL_MAP.get(result.returncode)})"
            )
            return result.returncode in [0, 1]
        except (FileNotFoundError, OSError) as e:
            logger.warning(f"Failed to invoke compiler: {e}")
            return False

    def get_build_log_path(self, target: ET.Element | None) -> Path | None:
        if target is None:
            return None

        if not (output_directory := self._get_text(target.find(".//OutputDirectory"))):
            return None
        if not (output_name := self._get_text(target.find(".//OutputName"))):
            return None
        return (
            self.project_path.parent / output_directory / f"{output_name}.build_log.htm"
        )

    def get_toolchain_from_build_log(
        self, target: ET.Element | None, try_build: bool = False
    ) -> Toolchain | None:
        if target is None:
            return None

        if (build_log_path := self.get_build_log_path(target)) is None:
            return None

        if try_build and not build_log_path.exists():
            logger.warning("Not found build_log, try build ...")
            self.try_build(target)
        if not build_log_path.exists():
            return None

        text = build_log_path.read_text(encoding="utf-8", errors="ignore")
        if not (m := TOOLCHAIN_REGEX.search(text)):
            return None

        toolchain_path = to_posix_path(m.group(1))
        return Toolchain(
            path=toolchain_path,
            compiler=f"{toolchain_path}/{m.group(2)}",
            assembler=f"{toolchain_path}/{m.group(3)}",
            xml_tag=self.UV_C51_XML_TAG
            if "c51" in m.group(2).lower()
            else self.UV_ARM_XML_TAG,
        )

    def get_toolchain_from_xml(self, target: ET.Element | None) -> Toolchain | None:
        if target is None:
            return None

        if not (toolset_number := self._get_text(target.find("ToolsetNumber"))):
            return None

        uac6 = self._get_text(target.find("uAC6")) or ""
        key = toolset_number + uac6
        if not (toolchain := self.UV_TOOLCHAIN_MAP.get(key)):
            return None

        compiler_path = shutil.which(toolchain.compiler)
        return Toolchain(
            path=(
                Path(compiler_path).parent.resolve().as_posix()
                if compiler_path
                else toolchain.path
            ),
            compiler=(
                to_posix_path(compiler_path) if compiler_path else toolchain.compiler
            ),
            assembler=(
                (Path(compiler_path).parent / toolchain.assembler).resolve().as_posix()
                if compiler_path
                else toolchain.assembler
            ),
            xml_tag=toolchain.xml_tag,
        )

    def get_toolchain(
        self, target: ET.Element | None, try_build: bool = False
    ) -> Toolchain | None:
        if target is None:
            return None

        if toolchain := self.get_toolchain_from_build_log(target, try_build):
            return toolchain
        logger.warning("Not found build_log, fallback to parse xml")
        return self.get_toolchain_from_xml(target)

    def get_dep_path(self, target: ET.Element | None) -> Path | None:
        if target is None:
            return None

        if not (target_name := self._get_text(target.find("TargetName"))):
            return None
        if not (output_directory := self._get_text(target.find(".//OutputDirectory"))):
            return None
        return (
            self.project_path.parent
            / output_directory
            / f"{self.project_path.stem}_{target_name}.dep"
        )

    def parse_dep(
        self, target: ET.Element | None, try_build: bool = False
    ) -> list[FileObject]:
        if target is None:
            return []

        if (dep_path := self.get_dep_path(target)) is None:
            return []

        if try_build and not dep_path.exists():
            logger.warning("Not Found dep file, try build ...")
            self.try_build(target)
        if not dep_path.exists():
            return []

        content = (
            re.sub(r'\\(?!")', "/", dep_path.read_text(encoding="utf-8"))
            .replace("-I ", "-I")  # avoid "-I ./inc" split to two line
            .replace("\n", " ")  # to one line
        )

        # Header directory: parse "I (header)(hex)"
        header_dirs = sorted(
            {Path(m.group(1)).parent.as_posix() for m in DEP_I_REGEX.finditer(content)}
        )

        # Source file: parse "F (source)(hex)(arguments)"
        file_objects = []
        for m in DEP_F_REGEX.finditer(content):
            file, args = m.group(1), shlex.split(m.group(2))

            # Add missing include path
            existing = {arg[2:] for arg in args if arg.startswith("-I")}
            args.extend([f"-I{d}" for d in header_dirs if d not in existing])
            file_objects.append(FileObject(file=file, arguments=args))
        return file_objects

    def parse_xml(
        self, target: ET.Element | None, toolchain: Toolchain | None
    ) -> list[FileObject]:
        if target is None or toolchain is None:
            return []

        xml_tag = toolchain.xml_tag[0]

        if (target_vc := self.get_various_controls(target, xml_tag)) is None:
            logger.warning("Not found target_controls in target")
            return []

        file_objects = []
        for group in target.findall(".//Group"):
            if (group_vc := self.get_various_controls(group, xml_tag)) is None:
                continue

            current_vc = VariousControls.merge(target_vc, group_vc)
            for file in group.findall(".//File"):
                file_path = self._get_text(file.find("FilePath"))
                # file_type = self._get_text(file.find("FileType"))

                if not file_path or not file_path.lower().endswith(
                    (".a51", ".s", ".c", ".cpp", ".cc", ".cx", ".cxx")
                ):
                    continue

                if (file_controls := self.get_various_controls(file, xml_tag)) is None:
                    continue

                file_objects.append(
                    FileObject(
                        file=to_posix_path(file_path),
                        arguments=VariousControls.merge(
                            current_vc, file_controls
                        ).get_options(),
                    )
                )
                # logger.debug(f"file_object: {file_objects[-1]}")
        return file_objects

    def parse(self, target_name: str, try_build: bool = False) -> TargetSetting | None:
        if (target := self.targets.get(target_name)) is None:
            logger.warning(f"Not found target: {target_name}")
            return None

        if (toolchain := self.get_toolchain(target, try_build)) is None:
            logger.warning("Not found toolchain")
            return None
        logger.info(f"Toolchain: {toolchain}")

        if toolchain.xml_tag == self.UV_C51_XML_TAG:
            file_objects = self.parse_xml(target, toolchain)
        elif toolchain.xml_tag == self.UV_ARM_XML_TAG:
            if not (file_objects := self.parse_dep(target, try_build)):
                logger.warning("Not found dep file, fallback to parse xml")
                file_objects = self.parse_xml(target, toolchain)
        else:
            logger.error(f"Unknown {toolchain.xml_tag=}")
            return None

        return TargetSetting(
            name=target_name,
            toolchain=toolchain,
            file_objects=file_objects,
        )

    @staticmethod
    @lru_cache(maxsize=32)
    def _get_predefined_macros_cached(
        compiler: str, args: tuple[str, ...]
    ) -> tuple[str, ...]:
        """Get predefined macros from compiler with caching."""
        if "armcc" in compiler.lower():
            cmd = [compiler, *args, "--list_macros"]
        elif "armclang" in compiler.lower():
            cmd = [compiler, *args, "--target=arm-arm-none-eabi", "-dM", "-E", "-"]
        else:
            return ()

        logger.info(f"Get predefined macro by: `{subprocess.list2cmdline(cmd)}`")
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, input="")
            if result.returncode != 0:
                logger.warning(
                    f"Exited with code {result.returncode}: {result.stderr.strip()}"
                )
                return ()
        except (FileNotFoundError, OSError) as e:
            logger.warning(f"Failed to invoke compiler: {e}")
            return ()

        return tuple(
            f"-D{name}={value}"
            for line in result.stdout.splitlines()
            if (m := PREDEFINED_REGEX.match(line.strip()))
            for name, value in [m.groups()]
        )

    def get_predefined_macros(
        self, toolchain: Toolchain | None, args: list[str] | None = None
    ) -> list[str]:
        if toolchain is None or not args:
            return []

        if toolchain.xml_tag == self.UV_C51_XML_TAG:
            # Predefined-Macros: https://developer.arm.com/documentation/101655/0961/Cx51-User-s-Guide/Preprocessor/Macros/Predefined-Macros?lang=en
            c51_defs = ["-D__C51__"]
            c51_defs.extend(
                f"-D{key}={val}" for key, val in self.UV_C51_EXTENSION_KEYWORDS.items()
            )

            # /path/to/keil/c51/bin -> /path/to/keil/c51/inc
            if (idx := toolchain.path.lower().rfind("bin")) != -1:
                c51_inc = toolchain.path[:idx] + toolchain.path[idx:].lower().replace(
                    "bin", "inc"
                )
                c51_defs.append(f"-I{c51_inc}")
            return c51_defs

        filtered_args = []
        args_iter = iter(args)
        for arg in args_iter:
            gen = (
                skip for pat, skip in PREDEFINED_FILTER_ARGUMENT_REGEX if pat.match(arg)
            )
            if (skip := next(gen, None)) is None:
                filtered_args.append(arg)
            elif skip:
                next(args_iter, None)

        return list(
            self._get_predefined_macros_cached(toolchain.compiler, tuple(filtered_args))
        )

    def filter_unknown_argument(
        self, toolchain: Toolchain | None, arguments: list[str]
    ) -> list[str]:
        if toolchain is None or not arguments:
            return []

        if "armcc" not in toolchain.compiler.lower():
            return arguments

        filtered_args = []
        args = iter(arguments)
        for arg in args:
            gen = (skip for pat, skip in ARMCC_UNKNOWN_ARGUMENT_REGEX if pat.match(arg))
            if (skip := next(gen, None)) is None:
                filtered_args.append(arg)
            elif skip:
                next(args, None)

        return filtered_args

    def generate_command_objects(
        self,
        target_setting: TargetSetting | None,
        extra_args: list[str] | None = None,
        predefined_macros: bool = False,
    ) -> list[CommandObject]:
        if target_setting is None:
            return []

        extra_args = extra_args or []
        command_objects = []
        directory = self.project_path.parent.resolve().as_posix()
        for file_object in target_setting.file_objects:
            toolchain_args = (
                self.get_predefined_macros(
                    target_setting.toolchain, file_object.arguments
                )
                if predefined_macros
                and not file_object.file.lower().endswith((".a51", ".s"))
                else []
            )
            arguments = self.filter_unknown_argument(
                target_setting.toolchain, file_object.arguments
            )
            command_objects.append(
                CommandObject(
                    directory=directory,
                    file=file_object.file,
                    arguments=(
                        [
                            (
                                target_setting.toolchain.compiler
                                if not file_object.file.lower().endswith((".a51", ".s"))
                                else target_setting.toolchain.assembler
                            )
                        ]
                        + toolchain_args
                        + arguments
                        + extra_args
                        + [file_object.file]
                    ),
                )
            )
        return command_objects


def generate_compile_commands(
    command_objects: list[CommandObject], output: Path
) -> bool:
    if not command_objects:
        logger.warning("No command objects")
        return False

    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        json.dump(
            [asdict(obj) for obj in command_objects],
            f,
            indent=4,
            ensure_ascii=False,
        )

    return True
