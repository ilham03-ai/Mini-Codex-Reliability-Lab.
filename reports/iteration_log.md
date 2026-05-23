# Iteration Log

Rough notes, mostly so I do not forget why some of the stricter bits are there.

- Started as a very small five-tool loop with a batch runner and ten toy
  workspaces.
- Later pass locked file access to the chosen workspace and moved test
  execution off `shell=True`.
- `run_tests` now only accepts `pytest` or `<python> -m pytest`, and
  `CONFIRM` permissions need an explicit callback instead of silently falling
  through.
- Final verification uses the current interpreter locally and `python` inside
  the container, which avoids depending on `pytest` being on the host `PATH`.
- Docker runs use a small runner image with `pytest` baked in. The Docker tests
  are marked and skipped unless you opt in.
