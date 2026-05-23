# Baseline Results

No baseline snapshot committed — runs depend on API credentials and model behavior, so they're better generated locally.

```bash
python -m evals.run_eval --output results.json
```

The default test suite (`python3 -m pytest -q`) covers the framework and helper code and should be green. Docker tests are opt-in (`-m docker`).
