# uv2compdb

Generate [Compilation Database] by parse Keil uVision project.

## Usage

```sh
usage: uv2compdb [-h] [-a ARGUMENTS] [-b] [-t TARGET] [-o OUTPUT] [-p] project

Generate compile_commands.json by parse Keil uVision project

positional arguments:
  project               path to .uvproj[x] file

options:
  -h, --help            show this help message and exit
  -a, --arguments ARGUMENTS
                        add extra arguments
  -b, --build           try to build while dep/build_log files don't not exist
  -t, --target TARGET   target name
  -o, --output OUTPUT   output dir/file path (default: compile_commands.json)
  -p, --predefined      try to add predefined macros
```

## Limit

+ [ ] Not support C51
+ [x] Not parsed `"Options" -> "C/C++" -> "Language / Code Generation"`
+ [x] Not parsed `"Options" -> "ASM"`, so Asm file use same options with C file
+ [x] Can't parse **RTE** components
+ [x] Can't add toolchain predefined macros and include path
+ [ ] The support for ARMCC(AC5) not well
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
