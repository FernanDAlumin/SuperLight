.PHONY: app test
app:
	bash scripts/build-macos.sh

test:
	python3 -m unittest discover -s tests -v
