# Handy shortcuts for development on any platform.

PY ?= python3

.PHONY: help run test icon build clean

help:
	@echo "make run    - start the GUI"
	@echo "make test   - run the test suite"
	@echo "make icon   - regenerate assets/gantz.png + gantz.ico"
	@echo "make build  - build a standalone executable with PyInstaller"
	@echo "make clean  - remove build artefacts"

run:
	$(PY) gantz_gui.py

test:
	$(PY) -m unittest discover -s tests -v

icon:
	$(PY) tools/make_icon.py

build: icon
	$(PY) -m pip install -r requirements-build.txt
	$(PY) -m PyInstaller --noconfirm --clean gantz.spec

clean:
	rm -rf build dist __pycache__ tests/__pycache__
