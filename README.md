# uv2compdb

Generate [Compilation Database] by parse Keil uVision project.

## Usage

```sh
usage: uv2compdb [-h] [-a ARGUMENTS] [-t TARGET] [-o OUTPUT] project

Generate compile_commands.json by parse Keil uVision project

positional arguments:
  project               path to .uvproj[x] file

options:
  -h, --help            show this help message and exit
  -a, --arguments ARGUMENTS
                        add extra arguments
  -t, --target TARGET   target name
  -o, --output OUTPUT   output dir/file path (default: compile_commands.json)
```

## Limit

+ [ ] Not support C51
+ [ ] Not parsed `"Options" -> "C/C++" -> "Language / Code Generation"`
+ [ ] Not parsed `"Options" -> "ASM"`, so Asm file use same options with C file
+ [ ] Can't parse **RTE** components
+ [ ] Can't add toolchain predefined macros and include path
  + need use `-a"-I/path/to/toolchain/include"` or config `.clangd` manually

## [Clangd]

[.clangd config]

```yaml
CompileFlags:
  CompilationDatabase: /path/to/compile-commands-dir
  # armcc
  # Compiler: armcc
  # Add:
  #   - -I/path/to/toolchain/include
  #   - -D__CC_ARM
  #   - -D__ARMCC_VERSION=500606
  # arm-none-eabi-gcc
  Compiler: arm-none-eabi-gcc
  Add:
    - -fdeclspec        # fix use arm-none-eabi-gcc instead of armcc

Diagnostics:
  UnusedIncludes: None  # Strict(default), None
```

## References

+ [keil2clangd]

[Compilation Database]: <https://clang.llvm.org/docs/JSONCompilationDatabase.html>
[Clangd]: <https://clangd.llvm.org/>
[.clangd config]: <https://clangd.llvm.org/config>
[keil2clangd]: <https://github.com/huiyi-li/keil2clangd/tree/master>
