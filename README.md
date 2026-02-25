# uv2compdb

Generate [Compilation Database] by parse Keil µVision project.

## Features

+ Parse strategy (dep/build_log -> XML)
+ Extract toolchain predefined macros with `-p` option
+ VariousControls hierarchical merge (Target -> Group -> File)

## Installation

```sh
pip install uv2compdb
```

## Usage

### Basic Usage

Generate `compile_commands.json` in the current directory for the first target
if the project has multiple targets.

```sh
uv2compdb /path/to/project
```

### Specify target and output

Generate `compile_commands.json` for a specific target and output

```sh
uv2compdb /path/to/project -t target -o /path/to/compile_commands.json
```

### Help

```sh
usage: uv2compdb [-h] [-v] [-V] [-a ARGUMENTS] [-b] [-t TARGET] [-o OUTPUT] [-p] project

Generate compile_commands.json by parse Keil µVision project

positional arguments:
  project               Path to .uvproj[x] file

optional arguments:
  -h, --help            Show this help message and exit
  -v, --verbose         Enable verbose output
  -V, --version         Show version and exit
  -a ARGUMENTS, --arguments ARGUMENTS
                        Add extra arguments
  -b, --build           Try to build while dep/build_log files don't not exist
  -t TARGET, --target TARGET
                        Target name
  -o OUTPUT, --output OUTPUT
                        Output dir/file path (default: compile_commands.json)
  -p, --predefined      Try to add predefined macros
```

## Limit

+ [x] Not parsed `"Options" -> "C/C++" -> "Language / Code Generation"`
+ [x] Not parsed `"Options" -> "ASM"`, so Asm file use same options with C file
+ [x] Can't parse **RTE** components
+ [x] Can't add toolchain predefined macros and include path
+ [ ] The support for C51 / ARMCC (AC5) not well
  + need config `.clangd` manually

## [Clangd]

[.clangd config]

```yaml
CompileFlags:
  CompilationDatabase: /path/to/compile-commands-dir
  Compiler: arm-none-eabi-gcc # use arm-neon-eabi-gcc instead of armcc
  Add:
    - -fdeclspec  # fix '__declspec' if use arm-none-eabi-gcc instead of armcc

Diagnostics:
  UnusedIncludes: None  # Strict(default), None
  # Suppress:
  #   - no_member
  #   - no_member_suggest
  #   - no_template
  #   - undeclared_var_use
```

## References

+ [keil2clangd]
+ [uvConvertor]
+ [a3750/uvconvertor]

[Compilation Database]: <https://clang.llvm.org/docs/JSONCompilationDatabase.html>
[Clangd]: <https://clangd.llvm.org/>
[.clangd config]: <https://clangd.llvm.org/config>
[keil2clangd]: <https://github.com/huiyi-li/keil2clangd>
[uvConvertor]: <https://github.com/vankubo/uvConvertor>
[a3750/uvconvertor]: <https://github.com/a3750/uvconvertor>
