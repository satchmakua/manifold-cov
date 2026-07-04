# Manifold — one-command install, checks, and demo.
# Windows without `make`? Run the commands under each target directly (see README).

export PYTHONIOENCODING = utf-8

.PHONY: install check demo curve clean

install:                      ## editable install with dev extras
	python -m pip install -e ".[dev]"

check:                        ## the gate that runs every build loop
	ruff check .
	mypy
	pytest -q

demo: check                   ## produce the flagship artifacts (heatmap + coverage curve + Claude bug)
	manifold run examples/toy_agents.py --spec examples/spec_example.py \
		--agent toy.retry_forever --scenarios 50 --html report.html
	manifold curve examples/research_agent.py --agent research.pipeline \
		--max-scenarios 120 --svg docs/coverage_curve.svg
	python examples/claude_agent.py

curve:                        ## just the directed-vs-random coverage curve
	manifold curve examples/research_agent.py --agent research.pipeline \
		--max-scenarios 120 --svg docs/coverage_curve.svg

clean:
	rm -rf report.html .pytest_cache .ruff_cache .mypy_cache
