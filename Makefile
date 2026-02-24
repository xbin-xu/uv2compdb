PROJECT_NAME := uv2compdb
PROJECT_DIST_DIR := dist
PROJECT_OUTPUT_DIR := build
PROJECT_SRC_PATH := src/uv2compdb
PROJECT_ICON_PATH := assets/$(PROJECT_NAME).ico

.PHONY: sync-version
sync-version:
	@grep "^version" pyproject.toml | sed 's/version = "\(.*\)"/__version__ = "\1"/' > $(PROJECT_SRC_PATH)/_version.py

.PHONY: build
build: sync-version
	uv build

.PHONY: install
install: build
	uv pip install .

.PHONY: publish publish-test
publish:
	uv tool run twine upload dist/*

publish-test:
	uv tool run twine upload --repository testpypi dist/*

.PHONY: exe
# nuitka
exe: sync-version
	uv tool run --from nuitka nuitka.cmd \
		--standalone \
		--onefile \
		--output-dir=$(PROJECT_OUTPUT_DIR) \
		--output-filename=$(PROJECT_NAME) \
		--windows-icon-from-ico=$(PROJECT_ICON_PATH) \
		$(PROJECT_SRC_PATH)

# pyinstaller
# exe: sync-version
# 	uv tool run pyinstaller \
# 		--onefile \
# 		--console \
# 		--distpath=$(PROJECT_OUTPUT_DIR) \
# 		--workpath=$(PROJECT_OUTPUT_DIR)/pyinstaller.build \
# 		--name $(PROJECT_NAME) \
# 		--icon $(PROJECT_ICON_PATH) \
# 		$(PROJECT_SRC_PATH)/__main__.py

.PHONY: clean
clean:
	rm -rf $(PROJECT_DIST_DIR) $(PROJECT_OUTPUT_DIR) $(PROJECT_SRC_PATH)/__pycache__
	uv pip uninstall $(PROJECT_NAME)
