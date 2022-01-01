BUILD_DIR ?= build
HUGO_BASE_DIR ?= hugo-base
HUGO_PUBLIC_DIR ?= $(HUGO_BASE_DIR)/public
INDEX_JSON ?= $(HUGO_PUBLIC_DIR)/index.json
GENERATOR_BIN = ./plugin-gallery-generator
HUGO_BASE_FILES = $(shell find "$(HUGO_BASE_DIR)" -type f)
STAMP_PLUGIN_GALLERY_GENERATOR = $(BUILD_DIR)/.stamp-plugin-gallery-generator


.PHONY: help
help:
	@echo "Available targets:"
	@echo "  build"
	@echo "  clean"
	@echo "  help"
	@echo "  show-indexing-words"
	@echo

.PHONY: build
build: $(STAMP_PLUGIN_GALLERY_GENERATOR)

$(STAMP_PLUGIN_GALLERY_GENERATOR): Makefile $(GENERATOR_BIN) config.yml
	"$(GENERATOR_BIN)" build
	touch "$@"

$(INDEX_JSON): $(STAMP_PLUGIN_GALLERY_GENERATOR)

.PHONY: show-indexing-words
show-indexing-words: $(INDEX_JSON)
	cat "$<" | jq -r .[].content | tr -d . | tr ' ' '\n' | tr '[A-Z]' '[a-z]' | grep -v "^$$" | sort | uniq -c | sort -n

.PHONY: lint
lint: $(INDEX_JSON)
	codespell $(GENERATOR_BIN) $(HUGO_BASE_DIR)/content
	@# verify the exported JSON format
	jq . <"$(INDEX_JSON)" >/dev/null
	python3 -m flake8 "$(GENERATOR_BIN)"
	black --check "$(GENERATOR_BIN)"

.PHONY: clean
clean:
	$(RM) -r "$(BUILD_DIR)"
