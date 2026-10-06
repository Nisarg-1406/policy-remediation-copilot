.PHONY: test run eval lint clean install

install:
	pip install -e ".[dev]"

PYTHON ?= $(shell if [ -f .venv/bin/python ]; then echo .venv/bin/python; else echo python3; fi)

test:
	$(PYTHON) -m unittest helpers/test_portfolio.py -v
	$(PYTHON) -m pytest tests/ -v --tb=short

HOUSEHOLD ?=
MODEL ?=
EXTRA_ARGS = $(if $(HOUSEHOLD),--household $(HOUSEHOLD),--all) $(if $(MODEL),--model $(MODEL),)

run:
	$(PYTHON) -m copilot run $(EXTRA_ARGS)

eval:
	$(PYTHON) -m copilot eval $(if $(MODEL),--model $(MODEL),)

lint:
	$(PYTHON) -m copilot lint $(if $(HOUSEHOLD),--household $(HOUSEHOLD),--all)

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true

# Record mode — requires OPENAI_API_KEY
record:
	LLM_MODE=record $(PYTHON) -m copilot run $(EXTRA_ARGS)

# Live variance runs — requires OPENAI_API_KEY
variance:
	LLM_MODE=live $(PYTHON) -m copilot eval --variance
